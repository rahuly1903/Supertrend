"""Historical daily backfill (yfinance). Incremental: only dates after what is stored.

    run_backfill(cfg)                              # all active symbols, incremental
    run_backfill(cfg, symbols=["TCS"], full=True)  # wipe + re-download (corporate action fix)
    run_backfill(cfg, years=12, extend=True)       # prepend older history before the first stored date
"""
from __future__ import annotations

import datetime as dt
import json
import logging

import numpy as np
import pandas as pd

import db
from common import last_complete_session, timed
from config import Config
from sources.base import PriceSource
from sources.validate import ISSUE_COLUMNS, detect_gaps, trading_calendar, validate_daily
from sources.yfinance_src import YFinanceSource

log = logging.getLogger(__name__)

STALE_DAYS = 7  # symbol with no new candle for this long -> logged as possibly suspended


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [f for f in frames if f is not None and not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=ISSUE_COLUMNS)


Plan = dict[tuple[dt.date, dt.date], list[str]]


def plan_downloads(state: pd.DataFrame, default_start: dt.date, end: dt.date) -> Plan:
    """Group symbols by download range (day after last stored candle .. end)."""
    start = state["last_date"].map(
        lambda d: default_start if pd.isna(d) else pd.Timestamp(d).date() + dt.timedelta(days=1)
    )
    todo = state.assign(start=start)
    todo = todo[todo["start"] <= end]
    return {(s, end): g["symbol"].tolist() for s, g in todo.groupby("start")}


def plan_extend(state: pd.DataFrame, default_start: dt.date) -> Plan:
    """Group symbols by backward range (default_start .. day before first stored candle).

    Symbols with a known listing date (first candle is the listing) have nothing older to fetch."""
    todo = state[state["first_date"].notna() & state["listed_date"].isna()]
    first = todo["first_date"].map(lambda d: pd.Timestamp(d).date())
    todo = todo.assign(end=first - dt.timedelta(days=1))
    todo = todo[todo["end"] > default_start]
    return {(default_start, e): g["symbol"].tolist() for e, g in todo.groupby("end")}


def _download(src: PriceSource, plan: Plan) -> pd.DataFrame:
    frames = []
    for (start, end), syms in sorted(plan.items()):
        log.info("download %d symbols from %s to %s", len(syms), start, end)
        df = src.download_daily(syms, start, end)
        if df.empty:
            continue
        d = pd.to_datetime(df["date"]).dt.date
        frames.append(df[(d >= start) & (d <= end)])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _log_issues(conn, issues: pd.DataFrame, ids: dict[str, int], source: str) -> None:
    if issues.empty:
        return
    rows = issues.assign(symbol_id=issues["symbol"].map(ids), source=source)
    db.copy_frame(conn, "data_quality_log", rows[["symbol_id", "date", "source", "issue", "details"]])


def session_calendar(cfg: Config, since) -> pd.DatetimeIndex:
    """NSE sessions from the calendar benchmark's stored dates."""
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT date FROM benchmark_candles WHERE index_name = %s AND date >= %s ORDER BY date",
            (cfg.data.calendar_benchmark, pd.Timestamp(since).date()),
        ).fetchall()
    return pd.DatetimeIndex([r[0] for r in rows])


def backfill_symbols(cfg: Config, src: PriceSource, years: int | None, symbols: list[str] | None,
                     full: bool, timings: dict, extend: bool = False) -> dict:
    end = last_complete_session(cfg.data.candle_ready_ist)
    default_start = end - dt.timedelta(days=round(365.25 * (years or cfg.data.backfill_years)))

    with db.connection() as conn:
        where, params = "s.is_active", []
        if symbols:
            where, params = "s.symbol = ANY(%s)", [symbols]
        state = db.read_frame(conn, f"""
            SELECT s.id, s.symbol,
                   (SELECT max(d.date) FROM daily_candles d WHERE d.symbol_id = s.id) AS last_date,
                   (SELECT min(d.date) FROM daily_candles d WHERE d.symbol_id = s.id) AS first_date,
                   s.listed_date
            FROM symbols s WHERE {where} ORDER BY s.symbol""", params)
        if symbols and (unknown := sorted(set(symbols) - set(state["symbol"]))):
            raise SystemExit(f"unknown symbols: {unknown}")
    if full:  # rows are deleted in the write transaction, so a failed download loses nothing
        state["last_date"] = None

    plan = plan_extend(state, default_start) if extend else plan_downloads(state, default_start, end)
    if not plan:
        log.info("daily candles already cover %s .. %s", default_start if extend else "-", end)
        return {"rows": 0, "symbols": 0, "through": str(end)}

    with timed(log, "yfinance download", timings):
        raw = _download(src, plan)
    requested = {s for syms in plan.values() for s in syms}

    with timed(log, "validate", timings):
        clean, issues = validate_daily(raw, cfg.data.ohlc_tolerance) if not raw.empty else (raw, pd.DataFrame())
        if not clean.empty:
            cal = session_calendar(cfg, clean["date"].min())
            if len(cal) == 0:
                cal = trading_calendar(clean, cfg.data.gap_calendar_min_frac)
            issues = _concat([issues, detect_gaps(clean, cal)])
        got = set(clean["symbol"]) if not clean.empty else set()
        last = state.set_index("symbol")["last_date"]
        no_data = [] if extend else [
            s for s in sorted(requested - got)
            if pd.isna(last.get(s)) or (end - pd.Timestamp(last[s]).date()).days > STALE_DAYS
        ]
        if no_data:
            log.warning("no data for %d symbols: %s", len(no_data), no_data[:30])
            issues = _concat([issues, pd.DataFrame({
                "symbol": no_data, "date": pd.Timestamp(end), "issue": "no_data",
                "details": [{"requested_from": str(default_start)}] * len(no_data)})])

    ids = dict(zip(state["symbol"], state["id"]))
    with timed(log, "write daily_candles", timings), db.connection() as conn:
        if full and not clean.empty:
            conn.execute("DELETE FROM daily_candles WHERE symbol_id = ANY(%s)", (state["id"].tolist(),))
            log.info("full re-backfill: replaced history for %d symbols", len(state))
        if not clean.empty:
            rows = clean.assign(symbol_id=clean["symbol"].map(ids), source=src.name)
            # date-major order keeps the BRIN index on daily_candles.date effective
            rows = rows.sort_values(["date", "symbol_id"])[
                ["symbol_id", "date", "open", "high", "low", "close", "volume", "source"]]
            # DO NOTHING: never overwrite bhavcopy rows (they carry delivery data)
            n = db.upsert_frame(conn, "daily_candles", rows, ["symbol_id", "date"], update_cols=[])
        else:
            n = 0
        _log_issues(conn, issues, ids, src.name)
        # new listings: first candle well after the requested start
        conn.execute("""
            UPDATE symbols s SET listed_date = f.first_date
            FROM (SELECT symbol_id, min(date) AS first_date FROM daily_candles
                  WHERE symbol_id = ANY(%s) GROUP BY symbol_id) f
            WHERE s.id = f.symbol_id AND s.listed_date IS NULL AND f.first_date > %s""",
            (list(ids.values()), default_start + dt.timedelta(days=10)))

    counts = issues["issue"].value_counts().to_dict() if not issues.empty else {}
    return {"rows": n, "symbols": len(got), "requested": len(requested), "through": str(end),
            "no_data": no_data, "issues": counts}


def backfill_benchmarks(cfg: Config, src: PriceSource, years: int | None, timings: dict,
                        extend: bool = False) -> dict:
    last_session = last_complete_session(cfg.data.candle_ready_ist)
    default_start = last_session - dt.timedelta(days=round(365.25 * (years or cfg.data.backfill_years)))
    with db.connection() as conn:
        span = {n: (a, b) for n, a, b in conn.execute(
            "SELECT index_name, min(date), max(date) FROM benchmark_candles GROUP BY index_name").fetchall()}

    frames = []
    with timed(log, "benchmark download", timings):
        for name, ticker in cfg.data.benchmarks.items():
            if extend:
                start, end = default_start, (span[name][0] - dt.timedelta(days=1) if name in span else last_session)
            else:
                start, end = (span[name][1] + dt.timedelta(days=1) if name in span else default_start), last_session
            if start > end:
                continue
            df = src.download_index({name: ticker}, start, end)
            d = df["date"].dt.date
            frames.append(df[(d >= start) & (d <= end) & (df["close"] > 0)])
    if not frames or all(f.empty for f in frames):
        return {"rows": 0}
    df = pd.concat(frames, ignore_index=True).rename(columns={"symbol": "index_name"})
    df["volume"] = df["volume"].replace(0, np.nan)
    with db.connection() as conn:
        n = db.upsert_frame(conn, "benchmark_candles", df.sort_values(["date", "index_name"]),
                            ["index_name", "date"])
    return {"rows": n, "by_index": df["index_name"].value_counts().to_dict()}


def run_backfill(cfg: Config, years: int | None = None, symbols: list[str] | None = None,
                 full: bool = False, source: PriceSource | None = None, extend: bool = False) -> dict:
    if full and extend:
        raise SystemExit("--full and --extend are exclusive")
    if full and not symbols:
        raise SystemExit("--full requires --symbols (refusing to wipe the whole table)")
    src = source or YFinanceSource(cfg.data)
    timings: dict = {}
    # benchmarks first: they define the trading calendar used for gap detection
    bench = backfill_benchmarks(cfg, src, years, timings, extend) if not symbols else None
    stocks = backfill_symbols(cfg, src, years, symbols, full, timings, extend)
    with db.connection() as conn:
        db.upsert_frame(conn, "ingest_log", pd.DataFrame([{
            "source": "yf_backfill", "trade_date": dt.date.fromisoformat(stocks["through"]),
            "status": "ok", "rows": stocks["rows"],
            "message": json.dumps({"stocks": stocks, "benchmarks": bench}, default=str)[:4000],
        }]), ["source", "trade_date"])
    return {"stocks": stocks, "benchmarks": bench, "timings": timings}

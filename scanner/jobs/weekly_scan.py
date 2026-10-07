"""Weekly scan pipeline:

    weekly candles -> supertrend -> load inputs -> panels -> per-week scan -> snapshot tables

Candle and Supertrend stages are incremental (no-op when nothing changed). Each scan
week is written in one transaction (DELETE + COPY per snapshot table), so re-running a
week overwrites it atomically.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
import traceback

import numpy as np
import pandas as pd

import db
from common import last_complete_session, timed
from config import Config
from engine.scan import ScanInputs, ScanResult, Scanner
from settings import get_settings
from engine.supertrend import SERIES_COLUMNS, compute_supertrend
from engine.weekly import WEEKLY_COLUMNS, diff_weekly, resample_weekly, week_monday

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Weekly candles
# ---------------------------------------------------------------------------
def step_weekly_candles(conn, cfg: Config, full: bool = False, since: dt.date | None = None,
                        cutoff: dt.date | None = None, symbol_ids: list[int] | None = None) -> pd.DataFrame:
    """Rebuild weekly candles from `since` (default: last `rebuild_weeks` stored weeks).

    `since` is snapped back to its week's Monday. `symbol_ids` limits the rebuild
    (used after gap repairs / corporate actions). Only symbols whose candles actually
    changed are rewritten. Returns [symbol_id, first_changed] for the Supertrend stage.
    """
    cutoff = cutoff or last_complete_session(cfg.data.candle_ready_ist)
    if full:
        since = None
    elif since is None:
        last = conn.execute("SELECT max(week_end_date) FROM weekly_candles").fetchone()[0]
        if last is not None:
            since = (week_monday([last])[0] - np.timedelta64(7 * (cfg.weekly.rebuild_weeks - 1), "D")).item()
    else:
        since = week_monday([since])[0].item()
    conds, params = [], {}
    if since:
        conds.append("{d} >= %(since)s")
        params["since"] = since
    if symbol_ids is not None:
        conds.append("symbol_id = ANY(%(ids)s)")
        params["ids"] = list(symbol_ids)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    daily = db.read_frame_copy(conn, f"""
        SELECT symbol_id, date, open, high, low, close, volume FROM daily_candles
        {where.format(d='date')} ORDER BY symbol_id, date""", params or None, parse_dates=["date"])
    new = resample_weekly(daily, cutoff)
    old = db.read_frame_copy(conn, f"""
        SELECT symbol_id, week_end_date, open, high, low, close, volume FROM weekly_candles
        {where.format(d='week_end_date')}""", params or None, parse_dates=["week_end_date"])
    changed = diff_weekly(new, old)
    if changed.empty:
        log.info("weekly candles unchanged (since %s, cutoff %s)", since or "start", cutoff)
        return changed

    ids = changed["symbol_id"].tolist()
    if since:
        conn.execute("DELETE FROM weekly_candles WHERE symbol_id = ANY(%s) AND week_end_date >= %s", (ids, since))
    else:
        conn.execute("DELETE FROM weekly_candles WHERE symbol_id = ANY(%s)", (ids,))
    rows = new[new["symbol_id"].isin(ids)].sort_values(["week_end_date", "symbol_id"])[WEEKLY_COLUMNS]
    db.copy_frame(conn, "weekly_candles", rows)
    log.info("weekly candles: %d rows rewritten for %d symbols (since %s, cutoff %s)",
             len(rows), len(ids), since or "start", cutoff)
    return changed


# ---------------------------------------------------------------------------
# Supertrend
# ---------------------------------------------------------------------------
def plan_supertrend(state: pd.DataFrame, latest: pd.DataFrame, changed: pd.DataFrame,
                    period: int, mult: float, full: bool) -> tuple[list[int], list[int]]:
    """Decide per symbol: full recompute, incremental update, or nothing.

    full        no usable state, params changed, or a week at/before the state week changed
    incremental newer weekly candles exist than the state week
    """
    df = latest.merge(state[["symbol_id", "week_end_date", "atr_period", "multiplier", "atr"]]
                      .rename(columns={"week_end_date": "state_week"}), on="symbol_id", how="left")
    df = df.merge(changed, on="symbol_id", how="left")
    if full:
        return df["symbol_id"].tolist(), []
    state_week = pd.to_datetime(df["state_week"])
    usable = (df["atr"].notna() & (df["atr_period"] == period)
              & np.isclose(df["multiplier"].astype(float), mult))
    rewrite = pd.to_datetime(df["first_changed"]) <= state_week
    need_full = ~usable | rewrite
    need_inc = ~need_full & (pd.to_datetime(df["latest_week"]) > state_week)
    return df.loc[need_full, "symbol_id"].tolist(), df.loc[need_inc, "symbol_id"].tolist()


def step_supertrend(conn, cfg: Config, changed: pd.DataFrame, full: bool = False) -> dict:
    period, mult = cfg.supertrend.atr_period, cfg.supertrend.multiplier
    state = db.read_frame(conn, "SELECT * FROM supertrend_state")
    latest = db.read_frame(conn, """SELECT symbol_id, max(week_end_date) AS latest_week
                                    FROM weekly_candles GROUP BY symbol_id""")
    full_ids, inc_ids = plan_supertrend(state, latest, changed, period, mult, full)
    if not full_ids and not inc_ids:
        log.info("supertrend up to date")
        return {"full": 0, "incremental": 0}

    weekly = db.read_frame_copy(conn, """
        SELECT w.symbol_id, w.week_end_date, w.high, w.low, w.close
        FROM weekly_candles w LEFT JOIN supertrend_state s ON s.symbol_id = w.symbol_id
        WHERE w.symbol_id = ANY(%(full)s)
           OR (w.symbol_id = ANY(%(inc)s) AND w.week_end_date > s.week_end_date)""",
        {"full": full_ids, "inc": inc_ids}, parse_dates=["week_end_date"])
    prior = state[state["symbol_id"].isin(inc_ids)]
    series, new_state = compute_supertrend(weekly, period, mult, prior)

    if full_ids:
        conn.execute("DELETE FROM weekly_supertrend WHERE symbol_id = ANY(%s)", (full_ids,))
        conn.execute("DELETE FROM supertrend_state WHERE symbol_id = ANY(%s)", (full_ids,))
    db.upsert_frame(conn, "weekly_supertrend",
                    series.sort_values(["week_end_date", "symbol_id"])[SERIES_COLUMNS],
                    ["symbol_id", "week_end_date"])
    db.upsert_frame(conn, "supertrend_state",
                    new_state.assign(updated_at=pd.Timestamp.now(tz="UTC")), ["symbol_id"])
    log.info("supertrend: %d full, %d incremental, %d weekly rows", len(full_ids), len(inc_ids), len(series))
    return {"full": len(full_ids), "incremental": len(inc_ids), "rows": len(series)}


def supertrend_summary(conn) -> dict:
    row = conn.execute("""
        WITH last AS (SELECT max(week_end_date) w FROM supertrend_state)
        SELECT (SELECT w FROM last),
               count(*) FILTER (WHERE direction = 1),
               count(*) FILTER (WHERE direction = -1),
               count(*) FILTER (WHERE direction = 1 AND weeks_in_trend = 1 AND flip_date IS NOT NULL),
               count(*) FILTER (WHERE direction = -1 AND weeks_in_trend = 1 AND flip_date IS NOT NULL),
               count(*) FILTER (WHERE week_end_date < (SELECT w FROM last))
        FROM supertrend_state""").fetchone()
    keys = ["week_end_date", "bullish", "bearish", "new_bullish_flips", "new_bearish_flips", "stale_symbols"]
    return dict(zip(keys, row))


def run_supertrend(cfg: Config, full: bool = False, cutoff: dt.date | None = None) -> dict:
    timings: dict = {}
    with db.connection() as conn:
        with timed(log, "weekly candles", timings):
            changed = step_weekly_candles(conn, cfg, full=full, cutoff=cutoff)
        with timed(log, "supertrend", timings):
            st = step_supertrend(conn, cfg, changed, full=full)
        summary = supertrend_summary(conn)
    return {"changed_symbols": len(changed), "supertrend": st, "summary": summary, "timings": timings}


# ---------------------------------------------------------------------------
# Scan: inputs, snapshot writes, run bookkeeping
# ---------------------------------------------------------------------------
def load_scan_inputs(conn, cfg: Config) -> ScanInputs:
    """One query per table; everything the scan needs for every stored week."""
    active = "symbol_id IN (SELECT id FROM symbols WHERE is_active)"
    daily = db.read_frame_copy(conn, f"""
        SELECT symbol_id, date, open, high, low, close, volume, delivery_pct
        FROM daily_candles WHERE {active} ORDER BY symbol_id, date""", parse_dates=["date"])
    weekly = db.read_frame_copy(conn, f"""
        SELECT symbol_id, week_end_date, open, high, low, close, volume
        FROM weekly_candles WHERE {active}""", parse_dates=["week_end_date"])
    st = db.read_frame_copy(conn, f"""
        SELECT symbol_id, week_end_date, st_value, direction
        FROM weekly_supertrend WHERE {active}""", parse_dates=["week_end_date"])
    bench = db.read_frame_copy(conn, "SELECT index_name, date, close FROM benchmark_candles",
                               parse_dates=["date"])
    symbols = db.read_frame(conn, """SELECT id, symbol, name, industry, shares_outstanding
                                     FROM symbols WHERE is_active""")
    idx = db.read_frame(conn, """
        SELECT m.symbol_id,
               string_agg(i.name, '|' ORDER BY CASE i.category WHEN 'broad' THEN 0
                          WHEN 'sectoral' THEN 1 ELSE 2 END, i.id) AS index_list
        FROM index_members m JOIN indices i ON i.id = m.index_id
        WHERE NOT (i.name = ANY(%s)) GROUP BY m.symbol_id""", (cfg.indicators.index_list_exclude,))
    exc = db.read_frame_copy(conn, "SELECT as_of_date, symbol_id FROM exclusion_list",
                             parse_dates=["as_of_date"])
    members = None
    if cfg.universe.point_in_time:
        members = db.read_frame(conn, """
            SELECT s.snapshot_date, s.symbol_id FROM index_member_snapshots s
            JOIN indices i ON i.id = s.index_id WHERE i.name = %s""", (cfg.universe.universe_index,))
        members["snapshot_date"] = pd.to_datetime(members["snapshot_date"])
    return ScanInputs(daily=daily, weekly=weekly, st_series=st, bench=bench,
                      symbols=symbols.astype({"shares_outstanding": float}),
                      index_lists=idx.set_index("symbol_id")["index_list"], exclusions=exc, members=members)


def _table_frame(conn, table: str, df: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in db.table_columns(conn, table) if c in df.columns]
    return df[cols]


def _start_run(week_end: dt.date) -> int:
    """Create/claim the scan_runs row. A complete week stays 'complete' while re-running,
    so the UI keeps showing it until the new snapshot commits."""
    with db.connection() as conn:
        return conn.execute("""
            INSERT INTO scan_runs (week_end_date, status, started_at) VALUES (%s, 'running', now())
            ON CONFLICT (week_end_date) DO UPDATE SET started_at = now(), error = NULL,
                status = CASE WHEN scan_runs.status = 'complete' THEN 'complete' ELSE 'running' END
            RETURNING id""", (week_end,)).fetchone()[0]


def _fail_run(run_id: int, exc: BaseException) -> None:
    with db.connection() as conn:
        conn.execute("""
            UPDATE scan_runs SET finished_at = now(), error = %s,
                status = CASE WHEN status = 'complete' THEN 'complete' ELSE 'failed' END
            WHERE id = %s""", ("".join(traceback.format_exception(exc))[-4000:], run_id))


def write_result(conn, run_id: int, res: ScanResult, cfg: Config, timings: dict, started: float) -> None:
    stocks = res.stocks.assign(run_id=run_id)
    sectors = res.sectors.assign(run_id=run_id)
    regime = pd.DataFrame([{**res.regime, "run_id": run_id}])
    db.replace_rows(conn, "stock_scan_results", _table_frame(conn, "stock_scan_results", stocks), {"run_id": run_id})
    db.replace_rows(conn, "sector_scan_results", _table_frame(conn, "sector_scan_results", sectors), {"run_id": run_id})
    db.replace_rows(conn, "market_regime", _table_frame(conn, "market_regime", regime), {"run_id": run_id})
    db.upsert_frame(conn, "sector_index_weekly", res.sector_history, ["industry", "week_end_date"])
    conn.execute("""
        UPDATE scan_runs SET status = 'complete', finished_at = now(), duration_ms = %s,
            stocks_scanned = %s, qualified_count = %s, notes = %s, error = NULL,
            config = %s, timings = %s
        WHERE id = %s""", (
        round((time.perf_counter() - started) * 1000), len(stocks), int(stocks["qualified"].sum()),
        "; ".join(res.notes) or None, json.dumps(cfg.model_dump()), json.dumps(timings), run_id))


class DataIncomplete(RuntimeError):
    pass


def check_week_complete(conn, cfg: Config, week_end: dt.date) -> list[str]:
    """Every session of the scan week must have candles for >= min_coverage of active symbols.

    A weekday is a holiday if bhavcopy said so, or if the calendar benchmark has later
    data but none for that day. Returns a list of problems (empty = OK).
    """
    monday = week_end - dt.timedelta(days=week_end.weekday())
    weekdays = [monday + dt.timedelta(days=i) for i in range(5)]
    holidays = {r[0] for r in conn.execute(
        """SELECT trade_date FROM ingest_log WHERE source = 'bhavcopy' AND status = 'holiday'
           AND trade_date BETWEEN %s AND %s""", (weekdays[0], weekdays[-1])).fetchall()}
    bench = {r[0] for r in conn.execute(
        "SELECT date FROM benchmark_candles WHERE index_name = %s AND date >= %s",
        (cfg.data.calendar_benchmark, weekdays[0])).fetchall()}
    bench_max = max(bench) if bench else None
    sessions = [d for d in weekdays
                if d not in holidays and not (d not in bench and bench_max and d < bench_max)]
    active = conn.execute("SELECT count(*) FROM symbols WHERE is_active").fetchone()[0]
    counts = dict(conn.execute("""
        SELECT date, count(*) FROM daily_candles WHERE date = ANY(%s)
          AND symbol_id IN (SELECT id FROM symbols WHERE is_active) GROUP BY date""", (sessions,)).fetchall())
    return [f"{d}: {counts.get(d, 0)}/{active} symbols" for d in sessions
            if counts.get(d, 0) < cfg.daily.min_coverage * active]


def scan_week(scanner: Scanner, cfg: Config, week_end: pd.Timestamp, base_timings: dict,
              guard: bool = False) -> dict:
    started = time.perf_counter()
    run_id = _start_run(week_end.date())
    timings = dict(base_timings)
    try:
        if guard:
            with db.connection() as conn:
                problems = check_week_complete(conn, cfg, week_end.date())
            if problems:
                raise DataIncomplete("daily data incomplete for week " + str(week_end.date())
                                     + ": " + "; ".join(problems))
        with timed(log, f"scan {week_end.date()}", timings):
            res = scanner.run(week_end)
        with timed(log, "write snapshot", timings), db.connection() as conn:
            write_result(conn, run_id, res, cfg, timings, started)
    except BaseException as exc:
        _fail_run(run_id, exc)
        raise
    st = res.stocks
    return {
        "run_id": run_id, "week_end_date": str(week_end.date()), "regime": res.regime["regime"],
        "stocks": len(st), "bullish": int((st.direction == "Bullish").sum()),
        "qualified": int(st.qualified.sum()),
        "grades": st.grade.value_counts().to_dict(),
        "tradeable_sectors": res.sectors.loc[res.sectors.tradeable, "industry"].tolist(),
        "top_sectors": res.sectors.head(5)[["industry", "sector_score", "rrg_quadrant"]].round(1).values.tolist(),
        "notes": res.notes,
    }


def run_weekly(cfg: Config, week: dt.date | None = None, history: int = 0, full: bool = False,
               ingest: bool = False) -> dict:
    """Update candles + Supertrend, then scan the latest week (or `week`, or the last `history` weeks).

    ingest=True (the Saturday cron): first catch up daily data and refresh ASM/GSM, and
    refuse to scan the latest week if any of its sessions is missing data.
    """
    settings = get_settings()
    timings: dict = {}
    pre: dict = {}
    if ingest:
        from jobs.daily_ingest import run_daily
        from jobs.universe import refresh_exclusions

        with timed(log, "daily catch-up", timings):
            pre["daily"] = {k: v for k, v in run_daily(cfg).items() if k in ("through", "corporate_actions_resolved")}
        try:
            with timed(log, "ASM/GSM", timings):
                pre["exclusions"] = refresh_exclusions(cfg)["in_symbols"]
        except Exception as exc:  # keep last snapshot; not worth failing the scan
            log.warning("ASM/GSM refresh failed, using previous snapshot: %s", exc)
    with db.connection() as conn:
        with timed(log, "weekly candles", timings):
            changed = step_weekly_candles(conn, cfg, full=full)
        with timed(log, "supertrend", timings):
            step_supertrend(conn, cfg, changed, full=full)
        with timed(log, "load inputs", timings):
            inputs = load_scan_inputs(conn, cfg)
    with timed(log, "build panels", timings):
        scanner = Scanner(inputs, cfg, settings.capital, settings.risk_per_trade_pct)

    weeks = scanner.scan_weeks()
    if week:
        targets = [w for w in weeks if w.date() == week or (w - pd.Timedelta(days=6)).date() <= week <= w.date()]
        if not targets:
            raise SystemExit(f"no stored week contains {week}")
    else:
        targets = weeks[-max(history, 1):]
    latest = weeks[-1]
    out = [scan_week(scanner, cfg, w, timings, guard=ingest and w == latest) for w in targets]
    return {**pre, "weeks": out, "timings": timings}

"""Daily ingest (Mon-Fri 18:45 IST): bhavcopy -> daily_candles, benchmarks, corporate actions.

Per bhavcopy date D and symbol:
    append   D is after the symbol's last stored candle      -> insert raw row
             (+ corporate-action check: stored close(D-1) vs NSE PREV_CLOSE(D))
    update   a candle for D already exists                   -> delivery columns only;
             stored prices are never rewritten, so adjusted and raw history never mix
    repair   D falls inside a gap (candles exist after D)    -> insert row scaled by
             stored close(D-1) / PREV_CLOSE(D), so it matches the adjusted history

Corporate actions (split/bonus: |ratio - 1| > threshold) are resolved by re-downloading
the symbol's history *before* the ex-date from yfinance (already adjusted) and checking
that the join is now continuous. Unresolved ones are retried on the next run.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time

import numpy as np
import pandas as pd

import db
from common import last_complete_session, timed, today_ist
from config import Config
from jobs.backfill import backfill_benchmarks, backfill_symbols
from jobs.weekly_scan import step_supertrend, step_weekly_candles
from sources import bhavcopy
from sources.http import HttpClient
from sources.validate import validate_daily
from sources.yfinance_src import YFinanceSource

log = logging.getLogger(__name__)

CANDLE_COLS = ["symbol_id", "date", "open", "high", "low", "close", "volume", "delivery_qty",
               "delivery_pct", "source"]


# ---------------------------------------------------------------------------
# Pure planning
# ---------------------------------------------------------------------------
def plan_rows(bhav: pd.DataFrame, state: pd.DataFrame, date: dt.date, prev_session: dt.date | None,
              threshold: float) -> dict[str, pd.DataFrame]:
    """Split one day's bhavcopy rows into append / repair / delivery-update / corporate actions.

    bhav:  [symbol_id, open, high, low, close, prev_close, volume, delivery_qty, delivery_pct]
    state: [symbol_id, prev_close_db, prev_date_db, exists_d, has_after, has_any]
    """
    m = bhav.merge(state, on="symbol_id", how="inner")
    m = m[m["has_any"]]  # symbols without history get their backfill from yfinance first
    contiguous = pd.to_datetime(m["prev_date_db"]).dt.date == prev_session if prev_session else False
    ratio = m["prev_close_db"] / m["prev_close"]
    m = m.assign(ratio=ratio, contiguous=contiguous, date=pd.Timestamp(date))

    upd = m[m["exists_d"]][["symbol_id", "date", "delivery_qty", "delivery_pct"]]

    new = m[~m["exists_d"]]
    append = new[~new["has_after"]]
    ca = append[append["contiguous"] & ((append["ratio"] - 1).abs() > threshold)]

    rep = new[new["has_after"]]
    rep_ok = rep[rep["contiguous"] & rep["ratio"].between(0.01, 100.0)].copy()
    for c in ("open", "high", "low", "close"):
        rep_ok[c] = rep_ok[c] * rep_ok["ratio"]
    rep_ok["volume"] = rep_ok["volume"] / rep_ok["ratio"]
    rep_ok["delivery_qty"] = rep_ok["delivery_qty"] / rep_ok["ratio"]

    return {
        "append": append.assign(source="bhav")[CANDLE_COLS],
        "repair": rep_ok.assign(source="bhav")[CANDLE_COLS],
        "repair_skipped": rep[~rep.index.isin(rep_ok.index)][["symbol_id", "date"]],
        "delivery": upd,
        "corporate_actions": ca.assign(ex_date=pd.Timestamp(date))[["symbol_id", "ex_date", "ratio"]],
    }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _symbol_map(conn) -> dict[str, int]:
    m = dict(conn.execute("SELECT symbol, id FROM symbols").fetchall())
    for old, sid in conn.execute("SELECT old_symbol, symbol_id FROM symbol_aliases").fetchall():
        m.setdefault(old, sid)
    return m


def _state_for(conn, ids: list[int], date: dt.date) -> pd.DataFrame:
    return db.read_frame(conn, """
        SELECT s.id AS symbol_id, p.close AS prev_close_db, p.date AS prev_date_db,
               EXISTS (SELECT 1 FROM daily_candles d WHERE d.symbol_id = s.id AND d.date = %(d)s) AS exists_d,
               EXISTS (SELECT 1 FROM daily_candles d WHERE d.symbol_id = s.id AND d.date > %(d)s) AS has_after,
               EXISTS (SELECT 1 FROM daily_candles d WHERE d.symbol_id = s.id) AS has_any
        FROM symbols s
        LEFT JOIN LATERAL (SELECT d.close, d.date FROM daily_candles d
                           WHERE d.symbol_id = s.id AND d.date < %(d)s
                           ORDER BY d.date DESC LIMIT 1) p ON true
        WHERE s.id = ANY(%(ids)s)""", {"d": date, "ids": ids})


def _prev_session(conn, date: dt.date, cfg: Config) -> dt.date | None:
    row = conn.execute("""
        SELECT max(date) FROM (
            SELECT date FROM benchmark_candles WHERE index_name = %s AND date < %s
            UNION SELECT trade_date FROM ingest_log WHERE source = 'bhavcopy' AND status = 'ok'
                  AND trade_date < %s) x""", (cfg.data.calendar_benchmark, date, date)).fetchone()
    return row[0]


def _log_ingest(conn, source: str, date: dt.date, status: str, rows: int = 0, message: dict | None = None):
    db.upsert_frame(conn, "ingest_log", pd.DataFrame([{
        "source": source, "trade_date": date, "status": status, "rows": rows,
        "message": json.dumps(message, default=str) if message else None,
    }]), ["source", "trade_date"])


def ingest_bhav(conn, cfg: Config, res: bhavcopy.BhavResult) -> dict:
    """Write one fetched bhavcopy. Returns counts + dates/symbols whose history changed."""
    d = res.date
    if res.status != "ok":
        weekend = d.weekday() >= 5
        status = "holiday" if res.status == "holiday" or weekend else "missing"
        _log_ingest(conn, "bhavcopy", d, status)
        return {"date": str(d), "status": status}

    ids = _symbol_map(conn)
    bhav = res.df.assign(symbol_id=res.df["symbol"].map(ids)).dropna(subset=["symbol_id"])
    bhav["symbol_id"] = bhav["symbol_id"].astype(int)
    clean, issues = validate_daily(bhav.assign(date=pd.Timestamp(d)), cfg.data.ohlc_tolerance)
    state = _state_for(conn, clean["symbol_id"].tolist(), d)
    plan = plan_rows(clean, state, d, _prev_session(conn, d, cfg), cfg.daily.corp_action_threshold)

    rows = pd.concat([plan["append"], plan["repair"]], ignore_index=True)
    n = db.upsert_frame(conn, "daily_candles", rows, ["symbol_id", "date"])
    db.update_from_frame(conn, "daily_candles", plan["delivery"], ["symbol_id", "date"])
    if not plan["corporate_actions"].empty:
        ca = plan["corporate_actions"].assign(action_type="unknown")
        db.upsert_frame(conn, "corporate_actions", ca, ["symbol_id", "ex_date"], update_cols=["ratio"])
        log.warning("corporate actions detected %s: %s", d, ca[["symbol_id", "ratio"]].round(3).values.tolist())
    if not issues.empty:
        iss = issues.assign(symbol_id=issues["symbol"].map(ids), source="bhav")
        db.copy_frame(conn, "data_quality_log", iss[["symbol_id", "date", "source", "issue", "details"]])
    counts = {"append": len(plan["append"]), "repair": len(plan["repair"]),
              "repair_skipped": len(plan["repair_skipped"]), "delivery": len(plan["delivery"]),
              "corporate_actions": len(plan["corporate_actions"])}
    _log_ingest(conn, "bhavcopy", d, "ok", n, counts)
    log.info("bhavcopy %s: %s", d, counts)
    return {"date": str(d), "status": "ok", **counts,
            "repaired_ids": plan["repair"]["symbol_id"].tolist()}


def pending_dates(conn, cfg: Config, days: int | None, end: dt.date) -> list[dt.date]:
    if days:
        start = end - dt.timedelta(days=days - 1)
    else:
        last = conn.execute("""SELECT max(trade_date) FROM ingest_log WHERE source = 'bhavcopy'
                               AND status IN ('ok', 'holiday')""").fetchone()[0]
        if last is None:
            last = conn.execute("SELECT max(date) FROM daily_candles").fetchone()[0] or end
        start = max(last + dt.timedelta(days=1), end - dt.timedelta(days=cfg.daily.max_catchup_days))
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)
            if (start + dt.timedelta(days=i)).weekday() != 6]   # no Sunday sessions


def fetch_with_wait(client: HttpClient, cfg: Config, d: dt.date) -> bhavcopy.BhavResult:
    """Today's file appears ~18:00-19:00 IST: poll until `wait_minutes`."""
    deadline = time.monotonic() + cfg.daily.wait_minutes * 60
    while True:
        res = bhavcopy.fetch(client, d, cfg.daily.series)
        if res.status != "unavailable" or d != today_ist() or time.monotonic() >= deadline:
            return res
        log.info("bhavcopy %s not published yet; retrying in %d min", d, cfg.daily.poll_minutes)
        time.sleep(cfg.daily.poll_minutes * 60)


# ---------------------------------------------------------------------------
# Corporate actions
# ---------------------------------------------------------------------------
def resolve_corporate_actions(cfg: Config, src: YFinanceSource) -> list[int]:
    """Re-download history before each unresolved ex-date; keep it only if the join is continuous."""
    with db.connection() as conn:
        todo = db.read_frame(conn, """
            SELECT c.id, c.symbol_id, s.symbol, c.ex_date, c.ratio,
                   (SELECT min(date) FROM daily_candles d WHERE d.symbol_id = c.symbol_id) AS first_date
            FROM corporate_actions c JOIN symbols s ON s.id = c.symbol_id
            WHERE NOT c.resolved ORDER BY c.ex_date""")
    fixed = []
    for r in todo.itertuples():
        ex = pd.Timestamp(r.ex_date).date()
        hist = src.download_daily([r.symbol], r.first_date, ex - dt.timedelta(days=1))
        hist, _ = validate_daily(hist, cfg.data.ohlc_tolerance) if not hist.empty else (hist, None)
        hist = hist[hist["date"].dt.date < ex] if not hist.empty else hist
        with db.connection() as conn:
            old = db.read_frame(conn, """SELECT date, close, delivery_qty, delivery_pct FROM daily_candles
                                         WHERE symbol_id = %s AND date < %s ORDER BY date""", (r.symbol_id, ex))
            if hist.empty or old.empty:
                log.warning("CA %s %s: no replacement history yet", r.symbol, ex)
                continue
            bhav_prev_close = old["close"].iloc[-1] / r.ratio      # NSE-adjusted PREV_CLOSE
            new_prev = hist.sort_values("date")["close"].iloc[-1]
            if abs(new_prev / bhav_prev_close - 1) > cfg.daily.corp_action_threshold:
                log.warning("CA %s %s: yfinance not adjusted yet (%.2f vs %.2f); retry later",
                            r.symbol, ex, new_prev, bhav_prev_close)
                continue
            keep = old.assign(date=pd.to_datetime(old["date"]))[["date", "delivery_qty", "delivery_pct"]]
            rows = hist.merge(keep, on="date", how="left").assign(symbol_id=r.symbol_id, source="yf")
            rows["delivery_qty"] = rows["delivery_qty"] * r.ratio
            conn.execute("DELETE FROM daily_candles WHERE symbol_id = %s AND date < %s", (r.symbol_id, ex))
            db.copy_frame(conn, "daily_candles", rows.sort_values("date")[CANDLE_COLS])
            conn.execute("""UPDATE corporate_actions SET resolved = true,
                            action_type = CASE WHEN ratio > 1 THEN 'split' ELSE 'unknown' END
                            WHERE id = %s""", (r.id,))
            log.info("CA %s %s resolved: ratio %.3f, %d rows replaced", r.symbol, ex, r.ratio, len(rows))
            fixed.append(int(r.symbol_id))
    return fixed


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------
def rebuild_history(cfg: Config, symbol_ids: list[int], since: dt.date | None, timings: dict) -> dict:
    """Weekly candles + Supertrend for symbols whose daily history changed in the past."""
    with timed(log, "rebuild weekly/supertrend", timings), db.connection() as conn:
        changed = step_weekly_candles(conn, cfg, since=since, symbol_ids=symbol_ids)
        st = step_supertrend(conn, cfg, changed)
    return {"symbols": len(symbol_ids), "since": str(since), "supertrend": st}


def run_daily(cfg: Config, days: int | None = None, dates: list[dt.date] | None = None) -> dict:
    timings: dict = {}
    src = YFinanceSource(cfg.data)
    end = last_complete_session(cfg.data.candle_ready_ist)

    with db.connection() as conn:
        no_data = [r[0] for r in conn.execute("""
            SELECT s.symbol FROM symbols s WHERE s.is_active
              AND NOT EXISTS (SELECT 1 FROM daily_candles d WHERE d.symbol_id = s.id)""").fetchall()]
    new_listings = None
    if no_data:
        log.info("yfinance history for %d new symbols: %s", len(no_data), no_data)
        new_listings = backfill_symbols(cfg, src, None, no_data, False, timings)
    with timed(log, "benchmarks", timings):
        bench = backfill_benchmarks(cfg, src, None, timings)

    client = HttpClient(min_interval_s=cfg.data.nse_min_interval_s, timeout_s=cfg.data.http_timeout_s,
                        retries=cfg.data.http_retries)
    results, repaired, repair_since = [], set(), None
    with db.connection() as conn:
        todo = dates or pending_dates(conn, cfg, days, end)
    with timed(log, f"bhavcopy ({len(todo)} dates)", timings):
        for d in sorted(todo):
            res = fetch_with_wait(client, cfg, d)
            with db.connection() as conn:
                out = ingest_bhav(conn, cfg, res)
            ids_rep = out.pop("repaired_ids", [])
            if ids_rep:
                repaired.update(ids_rep)
                repair_since = min(repair_since or d, d)
            results.append(out)

    with timed(log, "corporate actions", timings):
        ca_fixed = resolve_corporate_actions(cfg, src)

    rebuilt = None
    if repaired or ca_fixed:
        ids = sorted(set(repaired) | set(ca_fixed))
        rebuilt = rebuild_history(cfg, ids, None if ca_fixed else repair_since, timings)
    return {"through": str(end), "new_listings": new_listings, "benchmarks": bench,
            "bhavcopy": results, "corporate_actions_resolved": ca_fixed, "rebuilt": rebuilt,
            "timings": timings}


def find_gaps(conn, cfg: Config) -> pd.DataFrame:
    """(symbol_id, date) sessions missing between each active symbol's first and last candle."""
    return db.read_frame(conn, """
        WITH cal AS (SELECT date FROM benchmark_candles WHERE index_name = %s),
             span AS (SELECT symbol_id, min(date) AS mn, max(date) AS mx FROM daily_candles
                      WHERE symbol_id IN (SELECT id FROM symbols WHERE is_active) GROUP BY 1)
        SELECT s.symbol_id, c.date FROM span s JOIN cal c ON c.date BETWEEN s.mn AND s.mx
        LEFT JOIN daily_candles d ON d.symbol_id = s.symbol_id AND d.date = c.date
        WHERE d.date IS NULL ORDER BY c.date""", (cfg.data.calendar_benchmark,))


def run_repair_gaps(cfg: Config) -> dict:
    """Fill historical gaps from bhavcopy (scaled onto adjusted history), then rebuild."""
    timings: dict = {}
    with db.connection() as conn:
        gaps = find_gaps(conn, cfg)
    if gaps.empty:
        return {"gaps": 0}
    dates = sorted(set(pd.to_datetime(gaps["date"]).dt.date))
    log.info("%d missing candles on %d dates", len(gaps), len(dates))
    client = HttpClient(min_interval_s=cfg.data.nse_min_interval_s, timeout_s=cfg.data.http_timeout_s,
                        retries=cfg.data.http_retries)
    results, repaired = [], set()
    with timed(log, "repair", timings):
        for d in dates:
            res = bhavcopy.fetch(client, d, cfg.daily.series)
            if res.status != "ok":
                results.append({"date": str(d), "status": res.status})
                continue
            with db.connection() as conn:
                out = ingest_bhav(conn, cfg, res)
            repaired.update(out.pop("repaired_ids", []))
            results.append(out)
    with db.connection() as conn:
        left = find_gaps(conn, cfg)
    rebuilt = rebuild_history(cfg, sorted(repaired), dates[0], timings) if repaired else None
    return {"gaps_before": len(gaps), "gaps_after": len(left),
            "repaired": int(sum(r.get("repair", 0) for r in results)),
            "dates": results, "rebuilt": rebuilt, "timings": timings}

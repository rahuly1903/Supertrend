"""Point-in-time universe from the NSE bhavcopy archive: a survivorship-free backtest database.

Today's index lists only hold the stocks that survived. This builds the universe the way it looked
at the time, from every stock NSE listed (delisted ones included):

1. entities   (symbol, ISIN) pairs are linked into one company: a rename keeps the ISIN, a face-value
              split keeps the symbol. A link is cut where a chain goes quiet for over a year (reused symbol).
2. adjustment splits, bonuses and consolidations from NSE's corporate-action list (the old-format
              PREVCLOSE is NOT adjusted) back-adjust all earlier prices, volume the other way, when the
              ex-date price gap confirms the ratio. Dividends, rights and demergers are not adjusted
              (neither in the strategy nor in its yardstick).
3. universe   semi-annual, like NSE: effective on the last session of March and September, ranked on
              the 6 months to the end of January / July. Eligible: an equity ISIN (INE…, so no ETFs),
              traded on >= `min_traded_pct` of sessions; ranked by average daily traded value (the
              free-float market cap NSE uses is not in the bhavcopy). Top `top_n` -> one snapshot.
4. write      every entity that was ever a member, with its full history, into the (empty, migrated)
              database at DATABASE_URL; industry and share counts copied by ISIN from `source_url` (the
              live DB) where known, else 'Unclassified'; benchmark candles copied from it too.

Run against a separate database with SCANNER_CONFIG=config_pit.yaml (universe.point_in_time: true).
"""
from __future__ import annotations

import datetime as dt
import logging
import time

import numpy as np
import pandas as pd
import psycopg

import db
from common import timed
from config import Config
from sources import bhav_archive

log = logging.getLogger(__name__)

UNCLASSIFIED = "Unclassified"


# ---------------------------------------------------------------------------
# 1. entities
# ---------------------------------------------------------------------------
class _DSU:
    def __init__(self):
        self.p: dict = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def link_entities(raw: pd.DataFrame, max_gap_days: int = 365) -> pd.Series:
    """Entity id per row of raw [date, symbol, isin]: rows sharing a symbol or an ISIN are one company,
    split where the company's trading dates have a gap longer than max_gap_days."""
    isin = raw["isin"].fillna("").where(raw["isin"].fillna("").str.len() == 12, "")
    pairs = pd.DataFrame({"symbol": raw["symbol"], "isin": isin}).drop_duplicates()
    dsu = _DSU()
    for s, i in pairs.itertuples(index=False):
        dsu.find("S:" + s)
        if i:
            dsu.union("S:" + s, "I:" + i)
    root = raw["symbol"].map({s: dsu.find("S:" + s) for s in pairs["symbol"].unique()})
    # cut on long silences (a symbol reused by another company years later)
    d = pd.DataFrame({"root": root, "date": raw["date"]})
    days = d.drop_duplicates().sort_values(["root", "date"])
    new_seg = days.groupby("root")["date"].diff().dt.days.fillna(0).gt(max_gap_days)
    days["seg"] = new_seg.groupby(days["root"]).cumsum()
    d = d.merge(days, on=["root", "date"], how="left")
    key = d["root"] + "#" + d["seg"].astype(str)
    return pd.Series(pd.factorize(key)[0], index=raw.index)


# ---------------------------------------------------------------------------
# 2. adjustment
# ---------------------------------------------------------------------------
def map_events(events: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    """NSE split/bonus events [symbol, isin, ex_date, factor] -> [entity, date, factor] (date = the
    entity's first session on or after the ex-date)."""
    by_isin = raw.dropna(subset=["isin"]).drop_duplicates("isin").set_index("isin")["entity"]
    sym = raw[["symbol", "date", "entity"]].sort_values("date")
    out = []
    for ev in events.itertuples(index=False):
        ent = by_isin.get(ev.isin)
        if ent is None:
            m = sym[(sym["symbol"] == ev.symbol) & (sym["date"] <= ev.ex_date + pd.Timedelta(days=10))]
            ent = m["entity"].iloc[-1] if len(m) else None
        if ent is not None:
            out.append((ent, ev.ex_date, ev.factor))
    # separate announcements on one ex-date (e.g. a split and a bonus) multiply
    ev = (pd.DataFrame(out, columns=["entity", "ex_date", "factor"]).groupby(["entity", "ex_date"], as_index=False)
          ["factor"].prod())
    ev["ex_date"] = ev["ex_date"].astype("datetime64[ns]")
    ev["entity"] = ev["entity"].astype(raw["entity"].dtype)
    days = raw[["entity", "date"]].drop_duplicates().sort_values("date").astype({"date": "datetime64[ns]"})
    ev = pd.merge_asof(ev.sort_values("ex_date"), days.rename(columns={"date": "on"}), left_on="ex_date",
                       right_on="on", by="entity", direction="forward", tolerance=pd.Timedelta(days=30))
    return ev.dropna(subset=["on"]).rename(columns={"on": "date"})[["entity", "date", "factor"]]


def adjust(df: pd.DataFrame, events: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Back-adjust prices (and volume the other way) for split / bonus events [entity, date, factor].
    An event is applied only when the price gap at the ex-date agrees with it (open(D) / close(D-1)
    closer to the factor than to 1), so a mis-dated or cancelled announcement does no harm.
    Returns (adjusted rows, applied events with the observed gap)."""
    df = df.sort_values(["entity", "date"]).reset_index(drop=True)
    gap = df["open"] / df.groupby("entity")["close"].shift(1)
    ev = events.merge(df.assign(gap=gap)[["entity", "date", "gap"]], on=["entity", "date"], how="inner")
    ok = (np.log(ev["gap"] / ev["factor"]).abs() < np.log(ev["gap"]).abs()) & ev["gap"].notna()
    applied = ev[ok]
    f = df[["entity", "date"]].merge(applied, on=["entity", "date"], how="left")["factor"].fillna(1.0).to_numpy()
    logf = pd.Series(np.log(f), index=df.index)
    after = logf.groupby(df["entity"]).transform(lambda s: s[::-1].cumsum()[::-1]) - logf
    m = np.exp(after)
    for c in ("open", "high", "low", "close"):
        df[c] = df[c] * m
    df["volume"] = df["volume"] / m
    if (~ok).any():
        log.info("%d corporate actions skipped: price gap disagrees with the announced ratio", int((~ok).sum()))
    return df, applied


# ---------------------------------------------------------------------------
# 3. universe snapshots
# ---------------------------------------------------------------------------
def _last_session(sessions: pd.DatetimeIndex, on_or_before: pd.Timestamp) -> pd.Timestamp | None:
    s = sessions[sessions <= on_or_before]
    return s[-1] if len(s) else None


def rebalance_schedule(sessions: pd.DatetimeIndex) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    """(effective date, window start, window end): effective end-Mar / end-Sep, ranked on the 6 months
    to end-Jan / end-Jul."""
    out = []
    for y in range(sessions[0].year, sessions[-1].year + 1):
        for eff_m, win_end_m in ((3, 1), (9, 7)):
            eff = _last_session(sessions, pd.Timestamp(y, eff_m, 1) + pd.offsets.MonthEnd(0))
            w_end = pd.Timestamp(y, win_end_m, 1) + pd.offsets.MonthEnd(0)
            w_start = w_end - pd.DateOffset(months=6) + pd.Timedelta(days=1)
            if eff is None or w_start < sessions[0] or eff > sessions[-1] or eff <= w_end:
                continue
            out.append((eff, w_start, w_end))
    return out


def snapshots(df: pd.DataFrame, top_n: int, min_traded_pct: float) -> pd.DataFrame:
    """[snapshot_date, entity] for every rebalance. df: [entity, date, value, isin]."""
    sessions = pd.DatetimeIndex(sorted(df["date"].unique()))
    eq = df[df["isin"].fillna("").str.startswith("INE")]
    rows = []
    for eff, w0, w1 in rebalance_schedule(sessions):
        n_sess = int(((sessions >= w0) & (sessions <= w1)).sum())
        w = eq[(eq["date"] >= w0) & (eq["date"] <= w1)]
        g = w.groupby("entity").agg(days=("date", "nunique"), value=("value", "sum"))
        g = g[g["days"] >= n_sess * min_traded_pct / 100]
        g["avg_value"] = g["value"] / n_sess
        top = g.nlargest(top_n, "avg_value").index
        rows.append(pd.DataFrame({"snapshot_date": eff, "entity": top}))
        log.info("snapshot %s: %d eligible, top %d from Rs %.1f Cr/day", eff.date(), len(g), len(top),
                 g.loc[top, "avg_value"].min() / 1e7 if len(top) else 0)
    return pd.concat(rows, ignore_index=True)


# ---------------------------------------------------------------------------
# 4. build
# ---------------------------------------------------------------------------
def build(cfg: Config, source_url: str, start: dt.date, end: dt.date, top_n: int = 750,
          min_traded_pct: float = 80) -> dict:
    t0 = time.perf_counter()
    timings: dict = {}
    index_name = cfg.universe.universe_index
    if not cfg.universe.point_in_time:
        raise SystemExit("pit-build needs a config with universe.point_in_time: true (SCANNER_CONFIG=config_pit.yaml)")
    with db.connection() as conn:
        n = conn.execute("SELECT count(*) FROM daily_candles").fetchone()[0]
        if n:
            raise SystemExit(f"target database already has {n} daily candles; pit-build needs an empty, migrated DB")
        if conn.execute("SELECT current_database()").fetchone()[0] == source_url.rsplit("/", 1)[-1].split("?")[0]:
            raise SystemExit("target database is the source database")

    with timed(log, "load bhavcopy archive", timings):
        raw = bhav_archive.load(start, end, cfg.daily.series)
    raw = raw[(raw[["open", "high", "low", "close"]] > 0).all(axis=1)].reset_index(drop=True)
    log.info("%d rows, %d sessions, %d symbols", len(raw), raw["date"].nunique(), raw["symbol"].nunique())

    with timed(log, "link entities", timings):
        raw["entity"] = link_entities(raw)
        # one row per entity-day (a symbol change can overlap a day): keep the most traded
        raw = raw.sort_values("value", ascending=False).drop_duplicates(["entity", "date"])

    with timed(log, "universe snapshots", timings):
        snaps = snapshots(raw, top_n, min_traded_pct)
    members = set(snaps["entity"])
    df = raw[raw["entity"].isin(members)]

    with timed(log, "adjust", timings):
        events = map_events(bhav_archive.corporate_actions(start, end), raw[raw["entity"].isin(members)])
        df, ca = adjust(df, events)
    g = df["open"] / df.groupby("entity")["close"].shift(1)
    big_gaps = df.loc[(g < 0.55) | (g > 1.8), ["entity", "date", "symbol"]].assign(gap=g)
    df["high"] = df[["high", "open", "close"]].max(axis=1)
    df["low"] = df[["low", "open", "close"]].min(axis=1)

    # entity attributes: latest symbol / ISIN, first and last session
    last = df.sort_values("date").groupby("entity").last()
    first = df.groupby("entity")["date"].min()
    ent = pd.DataFrame({"symbol": last["symbol"], "isin": last["isin"], "series": last["series"],
                        "listed_date": first, "last_date": last["date"]})
    isins = df.groupby("entity")["isin"].agg(lambda s: set(s.dropna()))
    # unique symbols / ISINs (a reused symbol: the older company gets a suffix)
    ent = ent.sort_values("last_date", ascending=False)
    dup = ent["symbol"].duplicated()
    ent.loc[dup, "symbol"] = ent.loc[dup, "symbol"] + "~" + ent.loc[dup, "last_date"].dt.strftime("%Y")
    ent.loc[ent["isin"].duplicated() | ent["isin"].isna(), "isin"] = None
    end_ts = df["date"].max()
    ent["status"] = np.where(ent["last_date"] < end_ts - pd.Timedelta(days=30), "delisted", "listed")

    with timed(log, "industry from source", timings), psycopg.connect(source_url) as src:
        info = db.read_frame(src, "SELECT isin, symbol, name, industry, shares_outstanding FROM symbols")
        bench = db.read_frame(src, "SELECT index_name, date, open, high, low, close, volume FROM benchmark_candles")
    by_isin = info.dropna(subset=["isin"]).set_index("isin")
    by_sym = info.set_index("symbol")

    def lookup(e):
        for i in isins.get(e, ()):
            if i in by_isin.index:
                return by_isin.loc[i]
        s = ent.at[e, "symbol"]
        return by_sym.loc[s] if s in by_sym.index else None

    rec = {e: lookup(e) for e in ent.index}
    ent["name"] = [r["name"] if r is not None else None for r in rec.values()]
    ent["industry"] = [r["industry"] if r is not None and r["industry"] else UNCLASSIFIED for r in rec.values()]
    ent["shares_outstanding"] = [r["shares_outstanding"] if r is not None else None for r in rec.values()]

    with timed(log, "write", timings), db.connection() as conn:
        idx_id = conn.execute("""
            INSERT INTO indices (name, category) VALUES (%s, 'broad')
            ON CONFLICT (name) DO UPDATE SET updated_at = now() RETURNING id""", (index_name,)).fetchone()[0]
        sym = ent.reset_index().rename(columns={"index": "entity"})
        sym["delisted_date"] = sym["last_date"].where(sym["status"] == "delisted")
        sym["is_active"] = True
        sym["shares_outstanding"] = pd.to_numeric(sym["shares_outstanding"]).round().astype("Int64")
        db.copy_frame(conn, "symbols", sym[["symbol", "name", "isin", "industry", "series", "shares_outstanding",
                                             "is_active", "status", "listed_date", "delisted_date"]])
        ids = dict(conn.execute("SELECT symbol, id FROM symbols").fetchall())
        sid = ent["symbol"].map(ids)
        candles = df.assign(symbol_id=df["entity"].map(sid), source="bhav").sort_values("date")
        db.copy_frame(conn, "daily_candles", candles[["symbol_id", "date", "open", "high", "low", "close", "volume",
                                                      "source"]])
        db.copy_frame(conn, "index_member_snapshots",
                      snaps.assign(index_id=idx_id, symbol_id=snaps["entity"].map(sid))[
                          ["index_id", "snapshot_date", "symbol_id"]])
        latest = snaps[snaps["snapshot_date"] == snaps["snapshot_date"].max()]
        db.copy_frame(conn, "index_members", pd.DataFrame({"index_id": idx_id,
                                                           "symbol_id": latest["entity"].map(sid)}))
        db.copy_frame(conn, "benchmark_candles", bench)
        if not ca.empty:
            ca = ca.assign(symbol_id=ca["entity"].map(sid), ex_date=ca["date"], ratio=ca["factor"],
                           action_type="unknown", resolved=True)
            db.copy_frame(conn, "corporate_actions", ca[["symbol_id", "ex_date", "action_type", "ratio", "resolved"]])

    per_snap = snaps.groupby("snapshot_date").size()
    survivors = int((ent["status"] == "listed").sum())
    return {"sessions": int(raw["date"].nunique()), "entities_ever_member": len(members),
            "still_listed": survivors, "delisted": len(members) - survivors,
            "unclassified_industry": int((ent["industry"] == UNCLASSIFIED).sum()),
            "snapshots": len(per_snap), "first_snapshot": str(per_snap.index.min().date()),
            "candles": len(df), "corporate_actions": len(ca),
            "unexplained_gaps": len(big_gaps), "unexplained_gap_sample": big_gaps.head(15).astype(str).values.tolist(),
            "duration_ms": round((time.perf_counter() - t0) * 1000), "timings": timings}

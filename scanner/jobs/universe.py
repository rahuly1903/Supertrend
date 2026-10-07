"""Monthly universe refresh: universe index list (config `universe_index`) -> symbols, index memberships, share counts.
Weekly: ASM/GSM exclusion lists.
"""
from __future__ import annotations

import logging

import pandas as pd

import db
from common import timed, today_ist
from config import Config
from sources.http import HttpClient, NseClient
from sources.nse_lists import fetch_asm_gsm, fetch_constituents
from sources.yfinance_src import YFinanceSource

log = logging.getLogger(__name__)


def _http(cfg: Config) -> HttpClient:
    d = cfg.data
    return HttpClient(min_interval_s=d.nse_min_interval_s, timeout_s=d.http_timeout_s, retries=d.http_retries)


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------
def plan_symbol_changes(listing: pd.DataFrame, existing: pd.DataFrame) -> tuple[pd.DataFrame, list[int]]:
    """Work out ticker renames (same ISIN, new symbol) and symbol-text collisions.

    Returns (renames[symbol_id, old_symbol, new_symbol], collision_ids). Collision rows
    hold a symbol string that the new listing assigns to a different ISIN; they must be
    parked under a temporary name before the upsert, or UNIQUE(symbol) would fail.
    """
    m = listing[["isin", "symbol"]].merge(existing, on="isin", how="inner", suffixes=("", "_old"))
    renames = m.loc[m["symbol"] != m["symbol_old"], ["id", "symbol_old", "symbol"]]
    renames.columns = ["symbol_id", "old_symbol", "new_symbol"]

    owner = listing.set_index("symbol")["isin"]
    ex = existing[existing["symbol"].isin(owner.index)]
    collisions = ex.loc[ex["isin"].values != owner.reindex(ex["symbol"]).values, "id"].tolist()
    return renames.reset_index(drop=True), collisions


def upsert_symbols(conn, listing: pd.DataFrame) -> dict:
    today = today_ist()
    existing = db.read_frame(conn, "SELECT id, symbol, isin FROM symbols")
    renames, collisions = plan_symbol_changes(listing, existing)

    if collisions:
        conn.execute(
            "UPDATE symbols SET symbol = symbol || '#' || id, updated_at = now() WHERE id = ANY(%s)",
            (collisions,),
        )
    if not renames.empty:
        log.info("symbol renames: %s", dict(zip(renames["old_symbol"], renames["new_symbol"])))
        db.upsert_frame(
            conn, "symbol_aliases",
            renames.assign(changed_on=today)[["old_symbol", "symbol_id", "changed_on"]],
            ["old_symbol", "changed_on"],
        )

    rows = listing[["symbol", "name", "isin", "industry", "series"]].assign(
        is_active=True, updated_at=pd.Timestamp.now(tz="UTC")
    )
    n = db.upsert_frame(conn, "symbols", rows, ["isin"])
    dropped = conn.execute(
        """UPDATE symbols SET is_active = false, updated_at = now()
           WHERE is_active AND (isin IS NULL OR NOT (isin = ANY(%s))) RETURNING symbol""",
        (listing["isin"].tolist(),),
    ).fetchall()
    if dropped:
        log.info("dropped from universe: %s", [r[0] for r in dropped])
    return {"upserted": n, "renamed": len(renames), "dropped": len(dropped)}


# ---------------------------------------------------------------------------
# Index membership
# ---------------------------------------------------------------------------
def map_symbol_ids(df: pd.DataFrame, symbols: pd.DataFrame) -> pd.Series:
    """symbol_id for each row: match by ISIN, fall back to ticker."""
    by_isin = df["isin"].map(symbols.dropna(subset=["isin"]).set_index("isin")["id"])
    by_sym = df["symbol"].map(symbols.set_index("symbol")["id"])
    return by_isin.fillna(by_sym)


def refresh_indices(conn, cfg: Config, client: HttpClient, universe_listing: pd.DataFrame | None = None) -> dict:
    today = today_ist()
    defs = pd.DataFrame([
        {"name": i.name, "slug": i.file.removeprefix("ind_").removesuffix(".csv"),
         "category": i.category, "csv_url": cfg.universe.base_url + i.file}
        for i in cfg.universe.indices
    ]).assign(updated_at=pd.Timestamp.now(tz="UTC"))
    db.upsert_frame(conn, "indices", defs, ["name"])
    index_ids = dict(conn.execute("SELECT name, id FROM indices").fetchall())
    symbols = db.read_frame(conn, "SELECT id, symbol, isin FROM symbols WHERE is_active")

    summary, failed = {}, []
    for idx in cfg.universe.indices:
        try:
            if universe_listing is not None and idx.name == cfg.universe.universe_index:
                listing = universe_listing
            else:
                listing = fetch_constituents(client, cfg.universe.base_url, idx.file)
        except Exception as exc:
            log.warning("index %s: fetch failed, keeping previous membership (%s)", idx.name, exc)
            failed.append(idx.name)
            continue

        index_id = index_ids[idx.name]
        ids = map_symbol_ids(listing, symbols).dropna().astype(int).unique()
        prev = db.read_frame(conn, "SELECT symbol_id, added_on FROM index_members WHERE index_id = %s", (index_id,))
        members = pd.DataFrame({"index_id": index_id, "symbol_id": ids})
        members["added_on"] = members["symbol_id"].map(prev.set_index("symbol_id")["added_on"])
        if not prev.empty:  # first load: added_on unknown, leave NULL
            members.loc[~members["symbol_id"].isin(prev["symbol_id"]), "added_on"] = today
        db.replace_rows(conn, "index_members", members, {"index_id": index_id})
        db.upsert_frame(
            conn, "index_member_snapshots",
            members[["index_id", "symbol_id"]].assign(snapshot_date=today),
            ["index_id", "snapshot_date", "symbol_id"], update_cols=[],
        )
        summary[idx.name] = {"listed": len(listing), "in_universe": len(ids)}
    return {"indices": summary, "failed": failed}


def refresh_universe(cfg: Config, with_shares: bool = True) -> dict:
    client = _http(cfg)
    timings: dict = {}
    with timed(log, "fetch universe list", timings):
        u = cfg.universe.universe_def
        listing = fetch_constituents(client, cfg.universe.base_url, u.file)
        if cfg.universe.exclude_symbol_regex:
            skip = listing["symbol"].str.contains(cfg.universe.exclude_symbol_regex, regex=True)
            if skip.any():
                log.info("excluding placeholder symbols: %s", listing.loc[skip, "symbol"].tolist())
            listing = listing.loc[~skip]
        log.info("%s: %d constituents", u.name, len(listing))
    with db.connection() as conn:
        with timed(log, "upsert symbols", timings):
            sym = upsert_symbols(conn, listing)
        with timed(log, "index memberships", timings):
            idx = refresh_indices(conn, cfg, client, universe_listing=listing)
        db.upsert_frame(conn, "ingest_log", pd.DataFrame([{
            "source": "universe", "trade_date": today_ist(),
            "status": "ok" if not idx["failed"] else "failed",
            "rows": len(listing), "message": f"failed indices: {idx['failed']}" if idx["failed"] else None,
        }]), ["source", "trade_date"])
    shares = refresh_shares(cfg) if with_shares else None
    return {"symbols": sym, **idx, "shares": shares, "timings": timings}


# ---------------------------------------------------------------------------
# Shares outstanding (market cap)
# ---------------------------------------------------------------------------
def refresh_shares(cfg: Config) -> dict:
    with db.connection() as conn:
        symbols = db.read_frame(conn, "SELECT id, symbol FROM symbols WHERE is_active")
    with timed(log, f"shares outstanding ({len(symbols)} symbols)"):
        shares = YFinanceSource(cfg.data).shares_outstanding(symbols["symbol"].tolist())
    df = symbols.assign(shares_outstanding=symbols["symbol"].map(shares)).dropna(subset=["shares_outstanding"])
    with db.connection() as conn:
        n = db.update_from_frame(
            conn, "symbols",
            df[["id", "shares_outstanding"]].assign(updated_at=pd.Timestamp.now(tz="UTC")), ["id"],
        )
    missing = sorted(set(symbols["symbol"]) - set(shares))
    if missing:
        log.warning("no share count for %d symbols: %s", len(missing), missing[:20])
    return {"updated": n, "missing": len(missing)}


# ---------------------------------------------------------------------------
# ASM / GSM
# ---------------------------------------------------------------------------
def refresh_exclusions(cfg: Config) -> dict:
    d = cfg.data
    client = NseClient(min_interval_s=d.nse_min_interval_s, timeout_s=d.http_timeout_s, retries=d.http_retries)
    lists = fetch_asm_gsm(client)
    today = today_ist()
    with db.connection() as conn:
        symbols = db.read_frame(conn, "SELECT id, symbol, isin FROM symbols")
        lists["symbol_id"] = map_symbol_ids(lists, symbols)
        hits = lists.dropna(subset=["symbol_id"]).drop_duplicates(["list_type", "symbol_id"])
        rows = hits[["list_type", "symbol_id", "stage"]].assign(as_of_date=today)
        db.replace_rows(conn, "exclusion_list", rows, {"as_of_date": today})
        db.upsert_frame(conn, "ingest_log", pd.DataFrame([{
            "source": "asm_gsm", "trade_date": today, "status": "ok", "rows": len(lists),
        }]), ["source", "trade_date"])
    log.info("ASM/GSM: %d listed, %d in our symbols", len(lists), len(rows))
    return {"listed": len(lists), "in_symbols": len(rows),
            "symbols": sorted(hits["symbol"].tolist())}

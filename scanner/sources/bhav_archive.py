"""NSE CM bhavcopy archive (every listed stock, delisted ones included) for point-in-time backtests.

Two formats:
- before 2024-07-08: content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip
  (SYMBOL, SERIES, OPEN, HIGH, LOW, CLOSE, LAST, PREVCLOSE, TOTTRDQTY, TOTTRDVAL, TIMESTAMP, ..., ISIN)
- from 2024-07-08: content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip (UDiFF)

Raw zips are cached in data_cache/bhav_archive/ (a 404 older than a week is stored as an empty
.none marker: a holiday). Weekends are never requested.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import logging
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

from sources.http import HttpClient

log = logging.getLogger(__name__)

CACHE = Path(__file__).resolve().parent.parent / "data_cache" / "bhav_archive"
UDIFF_FROM = dt.date(2024, 7, 8)
OLD = "https://nsearchives.nseindia.com/content/historical/EQUITIES/{d:%Y}/{mon}/cm{d:%d}{mon}{d:%Y}bhav.csv.zip"
NEW = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
COLS = ["date", "symbol", "series", "isin", "open", "high", "low", "close", "prev_close", "volume", "value"]


def url_for(d: dt.date) -> str:
    return NEW.format(d=d) if d >= UDIFF_FROM else OLD.format(d=d, mon=d.strftime("%b").upper())


def _path(d: dt.date) -> Path:
    return CACHE / f"{d:%Y}" / f"{d:%Y%m%d}.zip"


def _fetch(client: HttpClient, d: dt.date) -> str:
    p, none = _path(d), _path(d).with_suffix(".none")
    if p.exists():
        return "cached"
    if none.exists():
        return "holiday"
    try:
        resp = client.get(url_for(d))
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            if d < dt.date.today() - dt.timedelta(days=7):
                none.parent.mkdir(parents=True, exist_ok=True)
                none.touch()
            return "holiday"
        raise
    if not resp.content.startswith(b"PK"):
        raise RuntimeError(f"{d}: not a zip ({resp.content[:60]!r})")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(resp.content)
    return "downloaded"


def download(start: dt.date, end: dt.date, workers: int = 4, min_interval_s: float = 0.25) -> dict:
    """Fill the cache for every weekday in [start, end]. Safe to re-run."""
    days = [start + dt.timedelta(n) for n in range((end - start).days + 1)]
    days = [d for d in days if d.weekday() < 5]
    client = HttpClient(min_interval_s=min_interval_s, timeout_s=30, retries=5)
    counts: dict[str, int] = {}
    with ThreadPoolExecutor(workers) as pool:
        for i, status in enumerate(pool.map(lambda d: _fetch(client, d), days)):
            counts[status] = counts.get(status, 0) + 1
            if (i + 1) % 250 == 0:
                log.info("bhav archive %d/%d %s", i + 1, len(days), counts)
    return counts


def parse(raw: bytes, d: dt.date) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        df = pd.read_csv(z.open(z.namelist()[0]), dtype=str)
    df.columns = [c.strip() for c in df.columns]
    if "TckrSymb" in df.columns:
        df = df[df["FinInstrmTp"].str.strip() == "STK"] if "FinInstrmTp" in df else df
        df = df.rename(columns={"TckrSymb": "symbol", "SctySrs": "series", "ISIN": "isin", "OpnPric": "open",
                                "HghPric": "high", "LwPric": "low", "ClsPric": "close",
                                "PrvsClsgPric": "prev_close", "TtlTradgVol": "volume", "TtlTrfVal": "value"})
    else:
        df = df.rename(columns={"SYMBOL": "symbol", "SERIES": "series", "ISIN": "isin", "OPEN": "open",
                                "HIGH": "high", "LOW": "low", "CLOSE": "close", "PREVCLOSE": "prev_close",
                                "TOTTRDQTY": "volume", "TOTTRDVAL": "value"})
    df = df.assign(date=pd.Timestamp(d))[COLS]
    for c in ("symbol", "series", "isin"):
        df[c] = df[c].str.strip()
    for c in ("open", "high", "low", "close", "prev_close", "volume", "value"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def load(start: dt.date, end: dt.date, series: list[str]) -> pd.DataFrame:
    """Every cached session in [start, end], filtered to `series`, one row per (date, symbol)."""
    frames = []
    for p in sorted(CACHE.glob("*/*.zip")):
        d = dt.datetime.strptime(p.stem, "%Y%m%d").date()
        if start <= d <= end:
            df = parse(p.read_bytes(), d)
            frames.append(df[df["series"].isin(series)])
    out = pd.concat(frames, ignore_index=True)
    rank = {s: i for i, s in enumerate(series)}
    out = (out.assign(_r=out["series"].map(rank)).sort_values(["date", "symbol", "_r"])
           .drop_duplicates(["date", "symbol"]).drop(columns="_r"))
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Corporate actions (the old-format PREVCLOSE is not adjusted, so splits/bonuses come from NSE)
# ---------------------------------------------------------------------------
CA_URL = ("https://www.nseindia.com/api/corporates-corporateActions?index=equities"
          "&from_date={a:%d-%m-%Y}&to_date={b:%d-%m-%Y}")
CA_CACHE = CACHE.parent / "nse_corporate_actions.json"
_BONUS = re.compile(r"bonus(?:\s+issue)?\s*-?\s*(\d+)\s*:\s*(\d+)", re.I)
_SPLIT = re.compile(r"(?:split|splt|sub-?\s*division|consolidat).*?r[se]\.?\s*([\d.]+).*?to\s*r[se]\.?\s*([\d.]+)",
                    re.I)


def price_factor(subject: str) -> float | None:
    """Multiplier for prices before the ex-date: bonus a:b -> b/(a+b); face value X -> Y -> Y/X; both in one
    announcement multiply."""
    low = subject.lower()
    if "debenture" in low or "preference" in low or "ncrps" in low:
        return None
    f = 1.0
    m = _BONUS.search(subject)
    if m and int(m.group(1)) > 0 and int(m.group(2)) > 0:
        f *= int(m.group(2)) / (int(m.group(1)) + int(m.group(2)))
    m = _SPLIT.search(subject)
    if m:
        x, y = float(m.group(1).rstrip(".")), float(m.group(2).rstrip("."))
        if x > 0 and y > 0:
            f *= y / x
    return f if f != 1.0 else None


def corporate_actions(start: dt.date, end: dt.date) -> pd.DataFrame:
    """[symbol, isin, ex_date, factor, subject] for every split / bonus / consolidation (cached)."""
    from sources.http import NseClient

    cache = json.loads(CA_CACHE.read_text()) if CA_CACHE.exists() else {}
    client = None
    a = dt.date(start.year, 1, 1)
    while a <= end:
        b = min(dt.date(a.year, 12, 31), end)
        key = f"{a:%Y}"
        if key not in cache or b > dt.date.fromisoformat(cache[key]["to"]) and b >= dt.date.today() - dt.timedelta(1):
            client = client or NseClient(min_interval_s=1.0)
            rows = []
            for q in range(4):   # quarterly requests (the API caps large ranges)
                qa = dt.date(a.year, 3 * q + 1, 1)
                qb = min(dt.date(a.year + (q == 3), (3 * q + 4) % 12 or 12, 1) - dt.timedelta(1), b)
                if qa > b:
                    break
                rows += client.get(CA_URL.format(a=qa, b=qb)).json()
            cache[key] = {"to": b.isoformat(), "rows": rows}
            CA_CACHE.write_text(json.dumps(cache))
            log.info("corporate actions %s: %d rows", key, len(rows))
        a = dt.date(a.year + 1, 1, 1)
    out = []
    for v in cache.values():
        for r in v["rows"]:
            f = price_factor(r.get("subject") or "")
            if f and r.get("exDate") not in (None, "-"):
                out.append({"symbol": r["symbol"], "isin": r.get("isin"), "subject": r["subject"], "factor": f,
                            "ex_date": pd.to_datetime(r["exDate"], format="%d-%b-%Y")})
    df = pd.DataFrame(out).drop_duplicates(["symbol", "ex_date", "subject"])
    return df[(df["ex_date"] >= pd.Timestamp(start)) & (df["ex_date"] <= pd.Timestamp(end))].reset_index(drop=True)

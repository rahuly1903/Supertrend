"""NSE security-wise bhavcopy with delivery data (sec_bhavdata_full_DDMMYYYY.csv).

Quirks handled:
- On a holiday the archive serves the *previous* session's file (HTTP 200), so the
  DATE1 column must match the requested date.
- Not yet published / future dates return 404.
- Delivery columns are '-' for trade-to-trade (BE) rows.
- PREV_CLOSE is adjusted by NSE on split/bonus ex-dates: used for corporate-action
  detection and for scaling gap repairs onto adjusted history.
"""
from __future__ import annotations

import datetime as dt
import io
import logging

import pandas as pd
import requests

from sources.http import HttpClient, SourceError

log = logging.getLogger(__name__)

URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv"

_COLUMNS = {
    "SYMBOL": "symbol", "SERIES": "series", "DATE1": "date", "PREV_CLOSE": "prev_close",
    "OPEN_PRICE": "open", "HIGH_PRICE": "high", "LOW_PRICE": "low", "CLOSE_PRICE": "close",
    "TTL_TRD_QNTY": "volume", "DELIV_QTY": "delivery_qty", "DELIV_PER": "delivery_pct",
}
_NUMERIC = ["prev_close", "open", "high", "low", "close", "volume", "delivery_qty", "delivery_pct"]


class BhavResult:
    """status: 'ok' | 'holiday' (file is for another date) | 'unavailable' (404)."""

    def __init__(self, date: dt.date, status: str, df: pd.DataFrame | None = None):
        self.date, self.status, self.df = date, status, df


def parse(text: str, series: list[str]) -> pd.DataFrame:
    if not text.lstrip().startswith("SYMBOL"):
        raise SourceError("not a bhavcopy CSV")
    df = pd.read_csv(io.StringIO(text), skipinitialspace=True, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    missing = set(_COLUMNS) - set(df.columns)
    if missing:
        raise SourceError(f"bhavcopy missing columns {sorted(missing)}")
    df = df.rename(columns=_COLUMNS)[list(_COLUMNS.values())]
    df["symbol"] = df["symbol"].str.strip()
    df["series"] = df["series"].str.strip()
    for c in _NUMERIC:
        df[c] = pd.to_numeric(df[c].str.strip().replace("-", None), errors="coerce")
    df["date"] = pd.to_datetime(df["date"].str.strip(), format="%d-%b-%Y")
    df = df[df["series"].isin(series)]
    # one row per symbol, preferring series order from config (EQ before BE ...)
    rank = {s: i for i, s in enumerate(series)}
    df = df.assign(_r=df["series"].map(rank)).sort_values(["symbol", "_r"]).drop_duplicates("symbol")
    return df.drop(columns="_r").reset_index(drop=True)


def fetch(client: HttpClient, date: dt.date, series: list[str]) -> BhavResult:
    try:
        resp = client.get(URL.format(d=date))
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return BhavResult(date, "unavailable")
        raise
    df = parse(resp.text, series)
    file_dates = set(df["date"].dt.date)
    if file_dates != {date}:
        log.info("bhavcopy %s: file is for %s -> holiday", date, sorted(file_dates))
        return BhavResult(date, "holiday")
    return BhavResult(date, "ok", df)

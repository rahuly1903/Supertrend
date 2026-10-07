"""yfinance adapter: batched, threaded daily downloads + share counts."""
from __future__ import annotations

import datetime as dt
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yfinance as yf
from tenacity import Retrying, stop_after_attempt, wait_random_exponential

from config import DataCfg
from sources.base import DAILY_COLUMNS

log = logging.getLogger(__name__)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # we log our own summary of failures

_OHLCV = {"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}


def nse_ticker(symbol: str) -> str:
    return f"{symbol}.NS"


def to_long(raw: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """yf.download(group_by='ticker') wide frame -> long [ticker, date, open..volume]."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["ticker", "date", *_OHLCV.values()])
    if not isinstance(raw.columns, pd.MultiIndex):  # single ticker without multi-level index
        raw = pd.concat({tickers[0]: raw}, axis=1)
    long = raw.stack(level=0, future_stack=True)
    long.index.names = ["date", "ticker"]
    long = long.reset_index().rename(columns=_OHLCV)
    long = long.dropna(subset=["close"])
    long["date"] = pd.to_datetime(long["date"]).dt.tz_localize(None).dt.normalize()
    return long[["ticker", "date", *_OHLCV.values()]]


class YFinanceSource:
    name = "yf"

    def __init__(self, cfg: DataCfg):
        self.cfg = cfg

    def _download(self, tickers: list[str], start: dt.date, end: dt.date) -> pd.DataFrame:
        """One batch; retried as a whole on exceptions. `end` inclusive."""
        for attempt in Retrying(stop=stop_after_attempt(self.cfg.http_retries),
                                wait=wait_random_exponential(multiplier=2, max=60), reraise=True):
            with attempt:
                raw = yf.download(
                    tickers, start=start.isoformat(), end=(end + dt.timedelta(days=1)).isoformat(),
                    interval="1d", auto_adjust=self.cfg.yf_auto_adjust, actions=False,
                    threads=True, group_by="ticker", progress=False,
                )
                return to_long(raw, tickers)
        raise AssertionError("unreachable")

    def _download_batched(self, tickers: list[str], start: dt.date, end: dt.date) -> pd.DataFrame:
        bs = self.cfg.yf_batch_size
        frames = []
        for i in range(0, len(tickers), bs):
            batch = tickers[i:i + bs]
            frames.append(self._download(batch, start, end))
            if i + bs < len(tickers):
                time.sleep(self.cfg.yf_pause_s)
        out = pd.concat(frames, ignore_index=True) if frames else to_long(None, [])
        # one retry pass for tickers that came back empty (transient Yahoo hiccups)
        missing = sorted(set(tickers) - set(out["ticker"]))
        if missing and len(missing) < len(tickers):
            time.sleep(self.cfg.yf_pause_s)
            retry = self._download(missing, start, end)
            out = pd.concat([out, retry], ignore_index=True)
        return out

    def download_daily(self, symbols: list[str], start: dt.date, end: dt.date) -> pd.DataFrame:
        by_ticker = {nse_ticker(s): s for s in symbols}
        long = self._download_batched(list(by_ticker), start, end)
        long["symbol"] = long.pop("ticker").map(by_ticker)
        return long[DAILY_COLUMNS]

    def download_index(self, tickers: dict[str, str], start: dt.date, end: dt.date) -> pd.DataFrame:
        by_ticker = {t: name for name, t in tickers.items()}
        long = self._download(list(by_ticker), start, end)
        long["symbol"] = long.pop("ticker").map(by_ticker)
        return long[DAILY_COLUMNS]

    def shares_outstanding(self, symbols: list[str]) -> dict[str, int]:
        def one(sym: str):
            for attempt in range(3):
                try:
                    shares = yf.Ticker(nse_ticker(sym)).fast_info["shares"]
                    return sym, int(shares) if shares else None
                except Exception as exc:  # yfinance raises many types
                    if attempt == 2:
                        log.debug("shares %s failed: %s", sym, exc)
                    time.sleep(1 + attempt * 2)
            return sym, None

        with ThreadPoolExecutor(self.cfg.shares_workers) as ex:
            res = dict(ex.map(one, symbols))
        return {s: v for s, v in res.items() if v}

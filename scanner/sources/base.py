"""Price source interface. Swap yfinance for Upstox / Kite by implementing this."""
from __future__ import annotations

import datetime as dt
from typing import Protocol

import pandas as pd

# Long format every source must return (one row per symbol per session).
DAILY_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "volume"]


class PriceSource(Protocol):
    name: str  # stored in daily_candles.source

    def download_daily(self, symbols: list[str], start: dt.date, end: dt.date) -> pd.DataFrame:
        """Daily OHLCV for NSE symbols, start..end inclusive -> DAILY_COLUMNS.

        Symbols with no data are simply absent from the result.
        """
        ...

    def download_index(self, tickers: dict[str, str], start: dt.date, end: dt.date) -> pd.DataFrame:
        """Benchmark OHLCV. tickers = {our_name: source_ticker} -> DAILY_COLUMNS, symbol=our_name."""
        ...

    def shares_outstanding(self, symbols: list[str]) -> dict[str, int]:
        ...

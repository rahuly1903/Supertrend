"""Scanner CSV in the exact published column order (web/lib/export.js mirrors this)."""
from __future__ import annotations

import pandas as pd

BASE_COLUMNS = [
    ("Symbol", "symbol"), ("Price", "price"), ("Direction", "direction"),
    ("Weeks in Trend", "weeks_in_trend"), ("Flip Date", "flip_date"), ("Flip Price", "flip_price"),
    ("% Since Flip", "pct_since_flip"), ("52W High", "high_52w"), ("52W Low", "low_52w"),
    ("% from High", "pct_from_high"), ("SMA50", "sma50"), ("SMA150", "sma150"), ("SMA200", "sma200"),
    ("1M%", "ret_1m"), ("3M%", "ret_3m"), ("Avg Vol (20d)", "avg_vol_20d"),
    ("Market Cap (Cr)", "market_cap_cr"), ("Sector", "industry"), ("Index", "index_list"),
]
EXTENDED_COLUMNS = [
    ("RS Rating", "rs_rating"), ("Stock Score", "stock_score"), ("Grade", "grade"),
    ("Sector Rank", "sector_rank"), ("Sector Score", "sector_score"), ("ST Value (Stop)", "st_value"),
    ("Risk %", "risk_pct"), ("ATR%", "atr_pct"), ("Vol Ratio", "vol_ratio"),
    ("Delivery %", "delivery_pct_20d"), ("SMA100", "sma100"), ("6M%", "ret_6m"), ("12M%", "ret_12m"),
    ("Strict 8 Filter", "qualified"), ("Reasons", "reasons"),
]
INT_COLUMNS = {"weeks_in_trend", "avg_vol_20d", "rs_rating", "sector_rank"}


def to_csv(stocks: pd.DataFrame, extended: bool = False) -> str:
    cols = BASE_COLUMNS + (EXTENDED_COLUMNS if extended else [])
    df = stocks.sort_values(["direction", "stock_score"], ascending=[False, False])
    out = pd.DataFrame()
    for header, col in cols:
        s = df[col]
        if col in INT_COLUMNS:
            s = s.round().astype("Int64")
        elif col == "flip_date":
            s = pd.to_datetime(s).dt.strftime("%Y-%m-%d")
        elif col == "qualified":
            s = s.map({True: "Yes", False: "No"})
        elif pd.api.types.is_float_dtype(s):
            s = s.round(2)
        out[header] = s.values
    return out.to_csv(index=False)

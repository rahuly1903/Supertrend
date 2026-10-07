"""Daily candle validation: bad prices, OHLC consistency, missing sessions.

Everything is vectorised; issues come back as a DataFrame ready for data_quality_log.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ISSUE_COLUMNS = ["symbol", "date", "issue", "details"]


def _issues(df: pd.DataFrame, mask: pd.Series, issue: str, cols: list[str]) -> pd.DataFrame:
    bad = df.loc[mask]
    if bad.empty:
        return pd.DataFrame(columns=ISSUE_COLUMNS)
    details = bad[cols].astype(object).where(bad[cols].notna(), None).to_dict("records")
    return pd.DataFrame({"symbol": bad["symbol"].values, "date": bad["date"].values,
                         "issue": issue, "details": details})


def validate_daily(df: pd.DataFrame, tolerance: float = 0.005) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Clean daily candles. Returns (clean_df, issues_df).

    - drops rows with missing / non-positive open, high, low or close
    - drops phantom candles (volume 0 and open == high == low == close): Yahoo emits
      these for NSE holidays. Logged once per date, not per symbol.
    - clamps high/low to cover open & close (logs if off by more than `tolerance`)
    - negative volume -> NULL
    - duplicate (symbol, date) -> keep last
    """
    df = df.copy()
    px = ["open", "high", "low", "close"]
    issues = []

    df = df.drop_duplicates(["symbol", "date"], keep="last")

    bad = df[px].isna().any(axis=1) | (df[px] <= 0).any(axis=1)
    issues.append(_issues(df, bad, "bad_price", px))
    df = df.loc[~bad]

    phantom = (df["volume"] == 0) & df[px].eq(df["close"], axis=0).all(axis=1)
    if phantom.any():
        per_day = df.loc[phantom].groupby("date")["symbol"].agg(["count", "first"])
        issues.append(pd.DataFrame({
            "symbol": None, "date": per_day.index, "issue": "phantom_candle",
            "details": [{"count": int(c), "example": f} for c, f in zip(per_day["count"], per_day["first"])],
        }))
    df = df.loc[~phantom]

    hi = df[px].max(axis=1)
    lo = df[px].min(axis=1)
    off = (hi > df["high"] * (1 + tolerance)) | (lo < df["low"] * (1 - tolerance))
    issues.append(_issues(df, off, "ohlc_inconsistent", px))
    df["high"], df["low"] = hi, lo

    neg_vol = df["volume"] < 0
    issues.append(_issues(df, neg_vol, "negative_volume", ["volume"]))
    df.loc[neg_vol, "volume"] = np.nan

    issues = [i for i in issues if not i.empty]
    all_issues = pd.concat(issues, ignore_index=True) if issues else pd.DataFrame(columns=ISSUE_COLUMNS)
    return df.reset_index(drop=True), all_issues


def trading_calendar(df: pd.DataFrame, min_frac: float = 0.5) -> pd.DatetimeIndex:
    """Sessions = dates on which at least `min_frac` of the symbols traded."""
    counts = df.groupby("date")["symbol"].nunique()
    n = df["symbol"].nunique()
    return pd.DatetimeIndex(sorted(counts[counts >= min_frac * n].index))


def detect_gaps(df: pd.DataFrame, calendar: pd.DatetimeIndex, max_listed: int = 20) -> pd.DataFrame:
    """One issue row per symbol missing sessions between its first and last candle."""
    if df.empty or len(calendar) == 0:
        return pd.DataFrame(columns=ISSUE_COLUMNS)
    cal = calendar.values
    g = df.groupby("symbol")["date"].agg(["min", "max", "count"])
    expected = np.searchsorted(cal, g["max"].values, side="right") - np.searchsorted(cal, g["min"].values)
    g = g.loc[expected > g["count"].values]
    rows = []
    for sym, r in g.iterrows():
        have = set(df.loc[df["symbol"] == sym, "date"])
        span = calendar[(calendar >= r["min"]) & (calendar <= r["max"])]
        missing = [d for d in span if d not in have]
        if missing:
            rows.append({
                "symbol": sym, "date": missing[0], "issue": "missing_days",
                "details": {"count": len(missing),
                            "dates": [d.strftime("%Y-%m-%d") for d in missing[:max_listed]]},
            })
    return pd.DataFrame(rows, columns=ISSUE_COLUMNS)

"""Daily -> weekly candles.

Weeks are Monday-anchored calendar weeks; week_end_date is the last trading day in the
week (Thursday when Friday is a holiday). For Mon-Fri sessions this equals pandas W-FRI;
weekend special sessions (Budget Saturday, Muhurat) stay in their own week, as on
TradingView. A week is complete once its Friday session has closed.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

WEEKLY_COLUMNS = ["symbol_id", "week_end_date", "week_start_date", "open", "high", "low",
                  "close", "volume", "trading_days"]


def week_monday(dates: pd.Series | np.ndarray) -> np.ndarray:
    """Monday (datetime64[D]) of each date's week. 1970-01-05 (day 4) was a Monday."""
    d = np.asarray(dates, dtype="datetime64[D]")
    return d - ((d.astype("int64") - 4) % 7).astype("timedelta64[D]")


def resample_weekly(daily: pd.DataFrame, cutoff: dt.date | None = None) -> pd.DataFrame:
    """daily[symbol_id, date, open, high, low, close, volume] -> weekly candles.

    cutoff: last session whose close is final. Weeks whose Friday is after it are
    incomplete and dropped. None keeps every week.
    """
    if daily.empty:
        return pd.DataFrame(columns=WEEKLY_COLUMNS + ["monday"])
    d = daily.sort_values(["symbol_id", "date"], kind="stable")
    d = d.assign(monday=week_monday(d["date"]))
    w = d.groupby(["symbol_id", "monday"], sort=False).agg(
        week_start_date=("date", "min"),
        week_end_date=("date", "max"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        trading_days=("close", "size"),
    ).reset_index()
    if cutoff is not None:
        friday = w["monday"].values.astype("datetime64[D]") + np.timedelta64(4, "D")
        w = w[friday <= np.datetime64(cutoff, "D")]
    return w[WEEKLY_COLUMNS + ["monday"]].reset_index(drop=True)


def diff_weekly(new: pd.DataFrame, old: pd.DataFrame) -> pd.DataFrame:
    """First changed week per symbol between two weekly frames (keyed by symbol + Monday).

    Returns [symbol_id, first_changed] where first_changed is the new week_end_date
    (or the removed week's date). New and removed weeks count as changes.
    """
    key = ["symbol_id", "monday"]
    cols = ["week_end_date", "open", "high", "low", "close", "volume"]
    if old.empty:
        m = new[key + ["week_end_date"]].rename(columns={"week_end_date": "first_changed"})
        return m.groupby("symbol_id", as_index=False)["first_changed"].min()
    if "monday" not in old:
        old = old.assign(monday=week_monday(old["week_end_date"]))
    m = new[key + cols].merge(old[key + cols], on=key, how="outer", suffixes=("", "_old"), indicator=True)
    changed = m["_merge"] != "both"
    for c in ("open", "high", "low", "close"):
        changed |= ~np.isclose(m[c].astype(float), m[f"{c}_old"].astype(float), rtol=1e-9, equal_nan=True)
    changed |= m["volume"].fillna(-1).astype(float) != m["volume_old"].fillna(-1).astype(float)
    changed |= pd.to_datetime(m["week_end_date"]) != pd.to_datetime(m["week_end_date_old"])
    m = m[changed]
    first = pd.to_datetime(m["week_end_date"]).fillna(pd.to_datetime(m["week_end_date_old"]))
    return (m.assign(first_changed=first).groupby("symbol_id", as_index=False)["first_changed"].min())

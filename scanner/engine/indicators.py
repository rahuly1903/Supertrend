"""Per-stock indicators.

Everything is computed once over full history as wide panels (rows = sessions or week
Mondays, columns = symbol_id). All windows look backwards, so reading a panel row at
date T gives exactly what was knowable at T. Historical scans reuse the same panels.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine.weekly import week_monday

TRADING_DAYS = {"1m": 21, "3m": 63, "6m": 126, "9m": 189, "12m": 252}


def wide(long: pd.DataFrame, col: str, index: pd.Index, date_col: str = "date") -> pd.DataFrame:
    return long.pivot(index=date_col, columns="symbol_id", values=col).reindex(index)


def wilder_atr(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    prev = close.shift(1)
    tr = np.maximum(high - low, np.maximum((high - prev).abs(), (low - prev).abs()))
    tr = tr.where(prev.notna(), high - low)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


@dataclass
class DailyPanels:
    close: pd.DataFrame
    p: dict[str, pd.DataFrame]          # name -> panel aligned with close

    def asof(self, t: pd.Timestamp) -> pd.DataFrame:
        """One row per symbol with every panel's value at session t."""
        return pd.DataFrame({k: v.loc[t] for k, v in self.p.items()})


def build_daily_panels(daily: pd.DataFrame, calendar: pd.DatetimeIndex, ffill_limit: int = 5,
                       rising_days: int = 20, delivery_short: int = 5) -> DailyPanels:
    """daily: [symbol_id, date, open, high, low, close, volume, delivery_pct]."""
    idx = calendar.union(pd.DatetimeIndex(daily["date"].unique())).sort_values()
    close_raw = wide(daily, "close", idx)
    close = close_raw.ffill(limit=ffill_limit)
    high = wide(daily, "high", idx).fillna(close_raw)
    low = wide(daily, "low", idx).fillna(close_raw)
    volume = wide(daily, "volume", idx)
    delivery = wide(daily, "delivery_pct", idx)

    p: dict[str, pd.DataFrame] = {"price": close}
    for n in (50, 100, 150, 200):
        p[f"sma{n}"] = close.rolling(n, min_periods=n).mean()
    p["sma200_prev"] = p["sma200"].shift(rising_days)
    p["high_52w"] = high.rolling(252, min_periods=20).max()
    p["low_52w"] = low.rolling(252, min_periods=20).min()
    for k, n in TRADING_DAYS.items():
        p[f"ret_{k}"] = (close / close.shift(n) - 1) * 100
    p["avg_vol_20d"] = volume.rolling(20, min_periods=10).mean()
    p["avg_turnover_20d_cr"] = (close_raw * volume).rolling(20, min_periods=10).mean() / 1e7
    atr_pct = wilder_atr(high, low, close) / close * 100
    p["atr_pct"] = atr_pct
    p["atr_pct_prev"] = atr_pct.shift(rising_days)
    p["delivery_pct_20d"] = delivery.rolling(20, min_periods=5).mean()
    p["delivery_pct_latest"] = delivery.rolling(delivery_short, min_periods=3).mean()
    p["history_days"] = close_raw.notna().cumsum()
    return DailyPanels(close=close, p=p)


@dataclass
class WeeklyPanels:
    close: pd.DataFrame                  # index = week Monday
    p: dict[str, pd.DataFrame]

    def asof(self, monday: pd.Timestamp) -> pd.DataFrame:
        return pd.DataFrame({k: v.loc[monday] for k, v in self.p.items()})


def build_weekly_panels(weekly: pd.DataFrame, bench_weekly: pd.Series, rs_sma_weeks: int = 10) -> WeeklyPanels:
    """weekly: [symbol_id, monday, high, low, close, volume]; bench_weekly indexed by Monday."""
    idx = pd.DatetimeIndex(sorted(set(weekly["monday"]) | set(bench_weekly.index)))
    wc = wide(weekly, "close", idx, "monday")
    wh = wide(weekly, "high", idx, "monday")
    wl = wide(weekly, "low", idx, "monday")
    wv = wide(weekly, "volume", idx, "monday").astype(float)

    p: dict[str, pd.DataFrame] = {}
    p["ret_1w"] = (wc / wc.shift(1) - 1) * 100
    p["ret_13w"] = (wc / wc.shift(13) - 1) * 100
    p["week_volume"] = wv
    p["vol_ratio"] = wv / wv.shift(1).rolling(20, min_periods=10).mean()
    rng = (wh - wl) / wc
    p["tightness_ratio"] = rng.rolling(3, min_periods=3).mean() / rng.shift(3).rolling(3, min_periods=3).mean()
    ratio = wc.div(bench_weekly.reindex(idx).ffill(), axis=0)
    p["rs_vs_benchmark"] = ratio / ratio.rolling(rs_sma_weeks, min_periods=rs_sma_weeks).mean()
    return WeeklyPanels(close=wc, p=p)


def supertrend_asof(series: pd.DataFrame, week_end: pd.Timestamp) -> pd.DataFrame:
    """Supertrend fields per symbol as of `week_end`, from the stored weekly series.

    series: [symbol_id, week_end_date, st_value, direction, close, volume_ratio?]
    Returns index symbol_id: direction, st_value, st_week, flip_date, flip_price,
    weeks_in_trend, is_new_flip. Same definitions as supertrend_state.
    """
    s = series[series["week_end_date"] <= week_end].sort_values(["symbol_id", "week_end_date"])
    g = s.groupby("symbol_id", sort=False)
    prev = g["direction"].shift(1)
    s = s.assign(is_flip=prev.notna() & (s["direction"] != prev))
    s["run"] = s.groupby("symbol_id", sort=False)["is_flip"].cumsum()
    s["weeks_in_trend"] = s.groupby(["symbol_id", "run"], sort=False).cumcount() + 1
    last = s.groupby("symbol_id", sort=False).tail(1).set_index("symbol_id")
    flips = (s[s["is_flip"]].groupby("symbol_id", sort=False).tail(1).set_index("symbol_id")
             [["week_end_date", "close"]].rename(columns={"week_end_date": "flip_date", "close": "flip_price"}))
    out = last[["direction", "st_value", "week_end_date", "weeks_in_trend", "is_flip"]].rename(
        columns={"week_end_date": "st_week", "is_flip": "is_new_flip"}).join(flips)
    return out


def flip_volume_ratio(st: pd.DataFrame, weekly_panels: WeeklyPanels) -> pd.Series:
    """Volume of the flip week / 20-week average volume before it."""
    vr = weekly_panels.p["vol_ratio"]
    fd = st["flip_date"].dropna()
    if fd.empty:
        return pd.Series(dtype=float)
    mondays = pd.DatetimeIndex(week_monday(fd.values))
    rows = vr.index.get_indexer(mondays)
    cols = vr.columns.get_indexer(fd.index)
    ok = (rows >= 0) & (cols >= 0)
    vals = np.full(len(fd), np.nan)
    vals[ok] = vr.to_numpy()[rows[ok], cols[ok]]
    return pd.Series(vals, index=fd.index)

"""Market regime gate.

    bull    : Nifty 500 above SMA200, SMA200 rising, and market ST breadth >= min_breadth
    bear    : Nifty 500 below SMA200 and (SMA200 falling or breadth < min_breadth)
    neutral : anything else
"""
from __future__ import annotations

import pandas as pd

from config import RegimeCfg


def market_regime(bench_close: pd.Series, t: pd.Timestamp, breadth_pct: float,
                  pct_above_sma200: float, cfg: RegimeCfg) -> dict:
    s = bench_close.loc[:t].dropna()
    sma = s.rolling(cfg.sma_days, min_periods=cfg.sma_days).mean()
    close, sma_now = s.iloc[-1], sma.iloc[-1]
    sma_prev = sma.iloc[-1 - cfg.slope_days] if len(sma) > cfg.slope_days else float("nan")
    above = bool(close > sma_now) if pd.notna(sma_now) else None
    rising = bool(sma_now > sma_prev) if pd.notna(sma_prev) else None
    breadth_ok = breadth_pct >= cfg.min_breadth
    if above and rising and breadth_ok:
        regime = "bull"
    elif above is False and (rising is False or not breadth_ok):
        regime = "bear"
    else:
        regime = "neutral"
    return {
        "nifty500_close": float(close), "nifty500_sma200": float(sma_now) if pd.notna(sma_now) else None,
        "sma200_rising": rising, "above_200dma": above,
        "market_breadth_pct": float(breadth_pct), "pct_above_sma200": float(pct_above_sma200),
        "regime": regime,
    }

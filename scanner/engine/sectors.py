"""Sector (industry) metrics: equal-weight index, RS vs benchmark, RRG, breadth, score.

RRG uses the common open-source JdK approximation (weekly), with EMA smoothing so the
weekly tails show rotation rather than noise:
    rs          = EMA(100 * sector_index / benchmark, S)
    RS-Ratio    = 100 + zscore(rs, N)
    RS-Momentum = 100 + zscore(EMA(100 * RS-Ratio / RS-Ratio[1], S), N)
so both axes are centred on 100. Quadrants:
    Ratio >= 100, Momentum >= 100 Leading   | Ratio >= 100, Momentum < 100 Weakening
    Ratio <  100, Momentum <  100 Lagging   | Ratio <  100, Momentum >= 100 Improving
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import SectorsCfg
from engine.relative_strength import pct_rank
from engine.weekly import week_monday

RETURN_WEEKS = {"ret_1w": 1, "ret_4w": 4, "ret_13w": 13, "ret_26w": 26, "ret_52w": 52}


def _zscore(s: pd.DataFrame, n: int) -> pd.DataFrame:
    return (s - s.rolling(n, min_periods=n).mean()) / s.rolling(n, min_periods=n).std()


def quadrant(ratio: pd.Series, momentum: pd.Series) -> pd.Series:
    q = np.select(
        [(ratio >= 100) & (momentum >= 100), (ratio >= 100) & (momentum < 100),
         (ratio < 100) & (momentum < 100), (ratio < 100) & (momentum >= 100)],
        ["Leading", "Weakening", "Lagging", "Improving"], default=None)
    return pd.Series(q, index=ratio.index, dtype=object)


@dataclass
class SectorPanels:
    index: pd.DataFrame        # weekly equal-weight index, Monday x industry
    week_end: pd.Series        # Monday -> last session of that week
    bench: pd.Series           # weekly benchmark close
    p: dict[str, pd.DataFrame]  # weekly metric panels, Monday x industry


def build_sector_panels(close: pd.DataFrame, industry: pd.Series, bench_daily: pd.Series,
                        st_direction: pd.DataFrame, cfg: SectorsCfg) -> SectorPanels:
    """close: sessions x symbol (ffilled); industry: symbol_id -> industry;
    st_direction: Monday x symbol weekly Supertrend direction (1 / -1 / NaN)."""
    rets = close.pct_change(fill_method=None).clip(-0.5, 1.0)
    groups = industry.reindex(rets.columns).values
    by_ind = rets.T.groupby(groups).mean().T
    daily_idx = (1 + by_ind.fillna(0)).cumprod() * 100

    monday = pd.DatetimeIndex(week_monday(daily_idx.index))
    widx = daily_idx.groupby(monday).last()
    week_end = pd.Series(daily_idx.index, index=monday).groupby(level=0).max()
    bench = bench_daily.reindex(daily_idx.index).ffill().groupby(monday).last()

    raw = widx.div(bench, axis=0)
    rs = (100 * raw).ewm(span=cfg.rrg_smooth, adjust=False).mean()
    rs_ratio = 100 + _zscore(rs, cfg.rrg_window)
    roc = (100 * rs_ratio / rs_ratio.shift(1)).ewm(span=cfg.rrg_smooth, adjust=False).mean()
    rs_momentum = 100 + _zscore(roc, cfg.rrg_window)

    p = {
        "rs_ratio_raw": raw,
        "rs_trend": raw / raw.rolling(cfg.rs_sma_weeks, min_periods=cfg.rs_sma_weeks).mean(),
        "rs_ratio": rs_ratio,
        "rs_momentum": rs_momentum,
        "vol_26w": widx.pct_change(fill_method=None).rolling(26, min_periods=26).std() * np.sqrt(52) * 100,
    }
    for k, n in RETURN_WEEKS.items():
        p[k] = (widx / widx.shift(n) - 1) * 100

    d = st_direction.reindex(widx.index)
    bull = (d == 1).astype(float).where(d.notna())
    p["st_breadth"] = bull.T.groupby(industry.reindex(d.columns).values).mean().T.reindex(
        columns=widx.columns) * 100
    return SectorPanels(index=widx, week_end=week_end, bench=bench, p=p)


def sector_history(sp: SectorPanels, upto_monday: pd.Timestamp) -> pd.DataFrame:
    """Rows for sector_index_weekly, every week up to `upto_monday`."""
    sl = slice(None, upto_monday)
    cols = {"index_value": sp.index.loc[sl]}
    for k in ("rs_ratio_raw", "rs_ratio", "rs_momentum", "st_breadth"):
        cols[k] = sp.p[k].loc[sl]
    long = pd.concat({k: v.stack(future_stack=True) for k, v in cols.items()}, axis=1)
    long.index.names = ["monday", "industry"]
    long = long.reset_index()
    long["week_end_date"] = long["monday"].map(sp.week_end)
    long["bench_value"] = long["monday"].map(sp.bench)
    return long.drop(columns="monday").dropna(subset=["index_value"])


def sector_metrics(sp: SectorPanels, stocks: pd.DataFrame, monday: pd.Timestamp,
                   cfg: SectorsCfg, near_high_pct: float = 10.0) -> pd.DataFrame:
    """One row per industry for the scan week. `stocks` = the week's stock frame."""
    row = pd.DataFrame({k: v.loc[monday] for k, v in sp.p.items()})
    prev_b = sp.p["st_breadth"].shift(cfg.breadth_change_weeks).loc[monday]
    row["breadth_change_4w"] = row["st_breadth"] - prev_b
    row["risk_adj_return"] = row["ret_26w"] / row["vol_26w"]
    row["rrg_quadrant"] = quadrant(row["rs_ratio"], row["rs_momentum"])

    g = stocks.groupby("industry")
    agg = pd.DataFrame({
        "stock_count": g.size(),
        "bullish_count": g["direction"].apply(lambda s: (s == "Bullish").sum()),
        "pct_above_sma50": g.apply(lambda x: (x["price"] > x["sma50"])[x["sma50"].notna()].mean() * 100,
                                   include_groups=False),
        "pct_above_sma200": g.apply(lambda x: (x["price"] > x["sma200"])[x["sma200"].notna()].mean() * 100,
                                    include_groups=False),
        "pct_near_high": g["pct_from_high"].apply(lambda s: (s <= near_high_pct).mean() * 100),
    })
    out = agg.join(row, how="left")
    out.index.name = "industry"

    ranks = {
        "rank_ret_13w": pct_rank(out["ret_13w"]),
        "rank_rs_trend": pct_rank(out["rs_trend"]),
        "rank_st_breadth": pct_rank(out["st_breadth"]),
        "rank_risk_adj": pct_rank(out["risk_adj_return"]),
    }
    for k, v in ranks.items():
        out[k] = v
    w = cfg.weights
    out["sector_score"] = (w["ret_13w"] * out["rank_ret_13w"].fillna(0)
                           + w["rs_trend"] * out["rank_rs_trend"].fillna(0)
                           + w["st_breadth"] * out["rank_st_breadth"].fillna(0)
                           + w["risk_adj"] * out["rank_risk_adj"].fillna(0))
    out["sector_rank"] = out["sector_score"].rank(ascending=False, method="first").astype(int)
    t = cfg.tradeable
    out["tradeable"] = ((out["sector_rank"] <= t.max_rank)
                        & (out["st_breadth"] >= t.min_st_breadth)
                        & (out["rs_trend"] > t.min_rs_trend)
                        & out["rrg_quadrant"].isin(t.quadrants)
                        & (out["stock_count"] >= t.min_stocks))
    return out.reset_index().sort_values("sector_rank").reset_index(drop=True)


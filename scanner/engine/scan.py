"""Weekly scan engine (pure: DataFrames in, DataFrames out).

    scanner = Scanner(inputs, cfg)          # builds every panel once (~1-2 s)
    result  = scanner.run(week_end_date)    # any stored week, no lookahead (~0.3 s)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config import Config
from engine.indicators import (build_daily_panels, build_weekly_panels, flip_volume_ratio,
                               supertrend_asof)
from engine.regime import market_regime
from engine.relative_strength import rs_rating
from engine.scoring import score_stocks
from engine.sectors import build_sector_panels, sector_history, sector_metrics
from engine.weekly import week_monday

log = logging.getLogger(__name__)


@dataclass
class ScanInputs:
    daily: pd.DataFrame        # symbol_id, date, open, high, low, close, volume, delivery_pct
    weekly: pd.DataFrame       # symbol_id, week_end_date, high, low, close, volume
    st_series: pd.DataFrame    # symbol_id, week_end_date, st_value, direction
    bench: pd.DataFrame        # index_name, date, close
    symbols: pd.DataFrame      # id, symbol, name, industry, shares_outstanding
    index_lists: pd.Series     # symbol_id -> 'Nifty 100|Nifty Bank|...'
    exclusions: pd.DataFrame   # as_of_date, symbol_id
    members: pd.DataFrame | None = None  # snapshot_date, symbol_id: point-in-time universe (None = every symbol)


def member_mask(members: pd.DataFrame, index: pd.DatetimeIndex, columns) -> pd.DataFrame:
    """index x symbol: True where the symbol belongs to the latest membership snapshot on or before that date."""
    snap = (pd.crosstab(pd.DatetimeIndex(members["snapshot_date"]), members["symbol_id"])
            .reindex(columns=columns, fill_value=0).astype(float))
    out = snap.reindex(snap.index.union(index)).ffill().reindex(index)
    return out.fillna(0) > 0


@dataclass
class ScanResult:
    week_end_date: pd.Timestamp
    stocks: pd.DataFrame
    sectors: pd.DataFrame
    regime: dict
    sector_history: pd.DataFrame
    notes: list[str] = field(default_factory=list)


class Scanner:
    def __init__(self, inputs: ScanInputs, cfg: Config, capital: float, risk_pct: float):
        self.cfg, self.capital, self.risk_pct = cfg, capital, risk_pct
        self.inp = inputs
        ic = cfg.indicators

        bench = inputs.bench
        self.bench_rs = (bench[bench.index_name == cfg.relative_strength.benchmark]
                         .set_index("date")["close"].sort_index())
        self.bench_regime = (bench[bench.index_name == cfg.regime.benchmark]
                             .set_index("date")["close"].sort_index())
        self.nifty50 = bench[bench.index_name == "NIFTY50"].set_index("date")["close"].sort_index()
        cal_name = cfg.data.calendar_benchmark
        calendar = pd.DatetimeIndex(sorted(bench.loc[bench.index_name == cal_name, "date"]))

        self.dp = build_daily_panels(inputs.daily, calendar, ic.ffill_limit_days,
                                     cfg.filters.sma200_rising_days, ic.delivery_short_days)

        weekly = inputs.weekly.assign(monday=pd.DatetimeIndex(week_monday(inputs.weekly["week_end_date"])))
        bench_w = self.bench_rs.groupby(pd.DatetimeIndex(week_monday(self.bench_rs.index))).last()
        self.wp = build_weekly_panels(weekly, bench_w, cfg.relative_strength.sma_weeks)

        st = inputs.st_series.merge(inputs.weekly[["symbol_id", "week_end_date", "close"]],
                                    on=["symbol_id", "week_end_date"], how="left")
        self.st_series = st
        st_m = st.assign(monday=pd.DatetimeIndex(week_monday(st["week_end_date"])))
        st_dir = st_m.pivot(index="monday", columns="symbol_id", values="direction")

        self.symbols = inputs.symbols.set_index("id")
        self.industry = self.symbols["industry"]
        sector_close = self.dp.close
        if inputs.members is not None:
            # sector indices from universe members only (point-in-time mode)
            sector_close = sector_close.where(member_mask(inputs.members, sector_close.index, sector_close.columns))
        self.sp = build_sector_panels(sector_close, self.industry, self.bench_rs, st_dir, cfg.sectors)

    def scan_weeks(self) -> list[pd.Timestamp]:
        """Scan week = last session of each week that has a stored Supertrend."""
        s = self.st_series.assign(monday=week_monday(self.st_series["week_end_date"]))
        return sorted(pd.to_datetime(s.groupby("monday")["week_end_date"].max()))

    def run(self, week_end: pd.Timestamp) -> ScanResult:
        cfg, ic = self.cfg, self.cfg.indicators
        week_end = pd.Timestamp(week_end)
        monday = pd.Timestamp(week_monday([week_end])[0])
        notes: list[str] = []

        st = supertrend_asof(self.st_series, week_end)
        stale = st["st_week"] < week_end - pd.Timedelta(weeks=ic.stale_weeks)
        if stale.any():
            notes.append(f"{int(stale.sum())} stale symbols skipped")
        st = st[~stale]

        df = (self.symbols[["symbol", "name", "industry", "shares_outstanding"]]
              .join(st, how="inner")
              .join(self.dp.asof(week_end))
              .join(self.wp.asof(monday)))
        df = df[df["price"].notna()].copy()
        if self.inp.members is not None:
            m = self.inp.members
            snap = m["snapshot_date"][m["snapshot_date"] <= week_end]
            df = df[df.index.isin(m.loc[m["snapshot_date"] == snap.max(), "symbol_id"])] if len(snap) else df.iloc[:0]
        df.index.name = "symbol_id"

        df["direction"] = np.where(df["direction"] == 1, "Bullish", "Bearish")
        df["pct_from_high"] = (df["high_52w"] - df["price"]) / df["high_52w"] * 100
        df["pct_above_low"] = (df["price"] / df["low_52w"] - 1) * 100
        df["pct_since_flip"] = (df["price"] / df["flip_price"] - 1) * 100
        df["pct_from_st"] = (df["price"] - df["st_value"]) / df["price"] * 100
        df["sma200_slope_pct"] = (df["sma200"] / df["sma200_prev"] - 1) * 100
        df["sma200_rising"] = (df["sma200"] > df["sma200_prev"]).where(df["sma200_prev"].notna())
        df["market_cap_cr"] = df["price"] * df["shares_outstanding"] / 1e7
        df["flip_vol_ratio"] = flip_volume_ratio(st, self.wp).reindex(df.index)
        fresh = df["weeks_in_trend"] <= cfg.signals.fresh_flip_max_weeks
        df["confirm_vol_ratio"] = np.fmax(df["vol_ratio"], df["flip_vol_ratio"].where(fresh))

        df["rs_raw"], df["rs_rating"] = rs_rating(df, cfg.relative_strength.weights)
        sector_13w = self.sp.p["ret_13w"].loc[monday]
        df["rs_vs_sector"] = df["ret_13w"] - df["industry"].map(sector_13w)

        exc = self.inp.exclusions
        exc = exc[exc["as_of_date"] <= week_end + pd.Timedelta(days=7)]
        if exc.empty:
            notes.append("no ASM/GSM snapshot for this week")
            df["in_asm_gsm"] = False
        else:
            latest = exc[exc["as_of_date"] == exc["as_of_date"].max()]
            df["in_asm_gsm"] = df.index.isin(latest["symbol_id"])
        df["index_list"] = self.inp.index_lists.reindex(df.index)

        sectors = sector_metrics(self.sp, df, monday, cfg.sectors, ic.near_high_breadth_pct)
        sec = sectors.set_index("industry")
        df["sector_score"] = df["industry"].map(sec["sector_score"])
        df["sector_rank"] = df["industry"].map(sec["sector_rank"])
        df["sector_tradeable"] = df["industry"].map(sec["tradeable"])

        bull = df["direction"] == "Bullish"
        has200 = df["sma200"].notna()
        regime = market_regime(self.bench_regime, week_end, bull.mean() * 100,
                               (df["price"] > df["sma200"])[has200].mean() * 100, cfg.regime)
        n50 = self.nifty50.loc[:week_end]
        regime["nifty50_close"] = float(n50.iloc[-1]) if len(n50) else None

        scored = score_stocks(df, len(sectors), cfg, self.capital, self.risk_pct, regime["regime"])
        scored = scored.reset_index().sort_values(["direction", "stock_score"], ascending=[False, False])
        scored["week_end_date"] = week_end
        sectors["week_end_date"] = week_end
        regime["week_end_date"] = week_end
        return ScanResult(week_end, scored.reset_index(drop=True), sectors, regime,
                          sector_history(self.sp, monday), notes)

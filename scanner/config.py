"""Typed loader for config.yaml. Unknown keys fail fast (typo protection)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

ROOT = Path(__file__).resolve().parent


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IndexDef(_Strict):
    name: str
    file: str
    category: Literal["broad", "sectoral", "thematic"]


class UniverseCfg(_Strict):
    base_url: str
    universe_index: str
    exclude_symbol_regex: str | None = "^DUMMY"
    point_in_time: bool = False       # scan only the universe_index members of each week's snapshot
                                      # (index_member_snapshots; built by `main.py pit-build`)
    indices: list[IndexDef]

    @model_validator(mode="after")
    def _universe_listed(self):
        if self.universe_index not in {i.name for i in self.indices}:
            raise ValueError(f"universe_index {self.universe_index!r} missing from indices")
        return self

    @property
    def universe_def(self) -> IndexDef:
        return next(i for i in self.indices if i.name == self.universe_index)


class DataCfg(_Strict):
    backfill_years: int = 3
    yf_batch_size: int = 100
    yf_auto_adjust: bool = True
    yf_pause_s: float = 1.0
    shares_workers: int = 8
    http_retries: int = 4
    http_timeout_s: float = 30
    nse_min_interval_s: float = 1.0
    candle_ready_ist: str = "16:00"
    calendar_benchmark: str = "NIFTY50"
    gap_calendar_min_frac: float = 0.5
    ohlc_tolerance: float = 0.005
    benchmarks: dict[str, str] = {"NIFTY500": "^CRSLDX", "NIFTY50": "^NSEI"}


class DailyCfg(_Strict):
    series: list[str] = ["EQ", "BE", "BZ"]
    corp_action_threshold: float = 0.03
    max_catchup_days: int = 10
    wait_minutes: int = 60
    poll_minutes: int = 10
    min_coverage: float = 0.9


class WeeklyCfg(_Strict):
    rebuild_weeks: int = 3


class SupertrendCfg(_Strict):
    atr_period: int = 7
    multiplier: float = 3.0


class IndicatorsCfg(_Strict):
    ffill_limit_days: int = 5
    stale_weeks: int = 2
    near_high_breadth_pct: float = 10
    delivery_short_days: int = 5
    index_list_exclude: list[str] = ["Nifty 500"]


class RelativeStrengthCfg(_Strict):
    benchmark: str = "NIFTY500"
    weights: dict[str, float] = {"ret_3m": 0.4, "ret_6m": 0.2, "ret_9m": 0.2, "ret_12m": 0.2}
    sma_weeks: int = 10


class TradeableCfg(_Strict):
    max_rank: int = 5
    min_st_breadth: float = 60
    min_rs_trend: float = 1.0
    quadrants: list[str] = ["Leading", "Improving"]
    min_stocks: int = 3


class SectorsCfg(_Strict):
    weights: dict[str, float] = {"ret_13w": 0.30, "rs_trend": 0.30, "st_breadth": 0.20, "risk_adj": 0.20}
    rs_sma_weeks: int = 10
    rrg_window: int = 10
    rrg_smooth: int = 3
    breadth_change_weeks: int = 4
    tradeable: TradeableCfg = TradeableCfg()


class RegimeCfg(_Strict):
    benchmark: str = "NIFTY500"
    sma_days: int = 200
    slope_days: int = 20
    min_breadth: float = 50
    bear_size_factor: float = 0.5


class FiltersCfg(_Strict):
    min_rs_rating: float = 70
    min_above_low_pct: float = 30
    max_from_high_pct: float = 25
    min_turnover_cr: float = 10
    min_price: float = 50
    sma200_rising_days: int = 20


class SignalsCfg(_Strict):
    fresh_flip_max_weeks: int = 6
    tight_stop_pct: float = 10
    vol_confirm_ratio: float = 1.5
    vcp_max_tightness: float = 0.75
    near_high_pct: float = 5


class PenaltiesCfg(_Strict):
    overextended_sma50_pct: float = 25
    overextended_since_flip_pct: float = 40
    high_atr_percentile: float = 90
    late_stage_weeks: int = 20
    points: dict[str, float] = {"overextended": 10, "high_atr": 5, "late_stage": 10}


class ScoringCfg(_Strict):
    weights: dict[str, float] = {"rs": 0.25, "sector": 0.15, "freshness": 0.15, "near_high": 0.15,
                                 "volume": 0.10, "tightness": 0.10, "risk": 0.10}
    freshness_decay_per_week: float = 6
    volume_ratio_floor: float = 0.8
    volume_ratio_full: float = 2.0
    tightness_best: float = 0.5
    tightness_worst: float = 1.25
    risk_full_pct: float = 3
    risk_zero_pct: float = 15
    grades: dict[str, float] = {"A+": 80, "A": 70, "B": 60}


class TradePlanCfg(_Strict):
    max_position_pct: float = 25


class SweepCfg(_Strict):
    # signal-level axes (each combination rebuilds scanner signals)
    supertrend: list[tuple[int, float]] = [(7, 3.0)]
    sector_weights: dict[str, dict[str, float]] = {
        "default": {"ret_13w": 0.30, "rs_trend": 0.30, "st_breadth": 0.20, "risk_adj": 0.20}}
    # simulation axes: backtest parameter -> values (cartesian product)
    grid: dict[str, list[Any]] = {"top_n_sectors": [3, 5, 7], "min_rs": [60, 70, 80]}
    # named override sets per axis, e.g. exit: {st: {...}, stop10: {stop_pct: 10}} (product across axes)
    variants: dict[str, dict[str, dict[str, Any]]] = {}


class WalkForwardCfg(_Strict):
    enabled: bool = True
    train_years: float = 4          # look-back used to pick parameters
    test_years: float = 1           # then trade them unseen for this long, roll forward
    anchored: bool = False          # true = training window always starts at the beginning
    metric: Literal["mar", "sharpe", "cagr"] = "cagr"  # mar = CAGR / |max drawdown|
    top_k: int = 10                 # blend the k best parameter sets (less luck than the single best)
    min_trades_per_year: float = 4  # ignore sets that barely trade in the training window


class BacktestCfg(_Strict):
    capital: float = 1_000_000
    max_positions: int = 10
    top_n_sectors: int = 5
    min_rs: float = 70
    sizing: Literal["equal", "risk", "fixed"] = "equal"
    position_size: float = 100_000
    position_pct: float = 0
    add_capital: bool = False
    min_fill_pct: float = 0
    above_sma200: bool = False
    entry_ma: Literal["none", "template", "stack", "above50", "rising200"] = "none"
    max_per_sector: int = 0
    exit_score_below: float = 0
    take_profit: list[tuple[float, float]] = []
    tp_fill: Literal["intraweek", "close"] = "intraweek"
    trim_above_pct: float = 0
    trim_to_pct: float = 10
    swap_min_score: float = 0
    swap_below_score: float = 60
    shuffle_seed: int = 0
    skip_pct: float = 0
    tax: Literal["none", "current", "historical"] = "none"
    tax_cess_pct: float = 4.0
    risk_pct: float = 1.0
    max_position_pct: float = 20
    cost_round_trip_pct: float = 0.2
    slippage_pct: float = 0.05
    sector_exit: bool = False
    sector_exit_weeks: int = 2
    bear_mode: Literal["ignore", "half", "skip"] = "half"
    min_score: float = 0
    score_weights: dict[str, float] | None = None
    score_penalties: bool = True
    stop_pct: float = 0
    trail_pct: float = 0
    trail_ma_weeks: int = 0
    time_stop_weeks: int = 0
    time_stop_min_pct: float = 0
    entry_max_weeks_in_trend: int = 0
    entry_filters: Literal["all", "tradeable"] = "all"
    warmup_min_sma200_pct: float = 80
    start: str | None = None
    end: str | None = None
    sweep: SweepCfg = SweepCfg()
    walk_forward: WalkForwardCfg = WalkForwardCfg()


class Config(_Strict):
    universe: UniverseCfg
    data: DataCfg = DataCfg()
    daily: DailyCfg = DailyCfg()
    weekly: WeeklyCfg = WeeklyCfg()
    supertrend: SupertrendCfg = SupertrendCfg()
    indicators: IndicatorsCfg = IndicatorsCfg()
    relative_strength: RelativeStrengthCfg = RelativeStrengthCfg()
    sectors: SectorsCfg = SectorsCfg()
    regime: RegimeCfg = RegimeCfg()
    filters: FiltersCfg = FiltersCfg()
    signals: SignalsCfg = SignalsCfg()
    penalties: PenaltiesCfg = PenaltiesCfg()
    scoring: ScoringCfg = ScoringCfg()
    trade_plan: TradePlanCfg = TradePlanCfg()
    backtest: BacktestCfg = BacktestCfg()


def load_config(path: Path | str | None = None) -> Config:
    """config.yaml next to this file, or the file named by $SCANNER_CONFIG (e.g. an experiment copy)."""
    path = path or os.environ.get("SCANNER_CONFIG") or ROOT / "config.yaml"
    if not Path(path).is_absolute():
        path = ROOT / path
    with open(path) as fh:
        return Config.model_validate(yaml.safe_load(fh))


@lru_cache
def get_config() -> Config:
    return load_config()

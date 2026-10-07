"""Weekly-rebalance backtest of the scanner.

Signals come from the same `Scanner` the live job uses: scanning a past week uses only
data up to that week (tests/test_scan.py::test_no_lookahead). Every week W:

    1. exits   held stock whose weekly Supertrend is bearish at W, or that dropped out of the
               data; optionally (sector_exit=true) also when its sector has been outside the
               top-N for `sector_exit_weeks` weeks in a row, when the close is below its
               `trail_ma_weeks` weekly SMA, or when it has gone nowhere after `time_stop_weeks`
    2. entries eligible stocks (hard filters, RS >= min_rs, rank score >= min_score, sector in
               the top-N tradeable sectors; top_n_sectors=0 = no sector gate) not already held,
               best rank score first, until K positions (max_positions=0 = no limit). The rank
               score is the scanner's stock score, or the same components re-weighted
               (`score_weights`). sizing=fixed buys `position_size` rupees per stock (or
               `position_pct` of equity when larger, so the size grows with the account); with
               add_capital=true a buy that cash cannot cover adds the shortfall as new capital,
               but only up to `position_size` (bigger sizes are funded from own cash only)
    3. fills   all orders execute at the OPEN of week W+1; costs + slippage per side
    4. stops   optional protective stop (stop_pct below entry, ratcheted by trail_pct below the
               highest weekly close) fills during week W+1 when its low touches the stop, at
               the stop or at the open if the week gaps through it
    5. mark    equity valued at the CLOSE of week W+1

Added capital is an external cash flow, not a return: CAGR, drawdown, Sharpe and volatility come
from `nav`, the time-weighted curve (equity with each week's added capital taken out). `equity` is
the rupee value; the benchmark curve buys the index with the same cash flows on the same weeks.

Signal building (the expensive part) is separated from simulation, so a sweep over
top-N / min RS / sizing reuses one signal set per (Supertrend params, sector weights).
"""
from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field, replace

import numpy as np
import pandas as pd

from backtest.tax import TaxBook, fy_of
from config import Config
from engine.scan import ScanInputs, Scanner, member_mask
from engine.supertrend import compute_supertrend
from engine.weekly import week_monday

log = logging.getLogger(__name__)

SCORE_PARTS = ["rs", "sector", "freshness", "near_high", "volume", "tightness", "risk"]
STOCK_COLS = ["symbol", "industry", "direction", "st_value", "stock_score", "rs_rating", "price", "sma50", "sma150",
              "sma200", "sma200_rising",
              "f_supertrend", "f_trend_template", "f_52w_range", "f_liquidity", "f_price", "f_not_asm",
              "penalty", "weeks_in_trend", *[f"score_{k}" for k in SCORE_PARTS]]
SECTOR_COLS = ["sector_rank", "st_breadth", "rs_trend", "rrg_quadrant", "stock_count"]


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------
@dataclass
class WeekSignal:
    week_end: pd.Timestamp
    monday: pd.Timestamp
    stocks: pd.DataFrame      # index symbol_id, STOCK_COLS
    sectors: pd.DataFrame     # index industry, SECTOR_COLS
    regime: str


@dataclass
class SignalSet:
    key: str
    st_params: tuple[int, float]
    sector_weights: str
    weeks: list[WeekSignal]
    tradeable: dict           # tradeable thresholds used at simulation time


def config_variant(cfg: Config, st_params: tuple[int, float], sector_weights: dict[str, float]) -> Config:
    c = cfg.model_copy(deep=True)
    c.supertrend.atr_period, c.supertrend.multiplier = int(st_params[0]), float(st_params[1])
    c.sectors.weights = dict(sector_weights)
    return c


def warmup_start(scanner: Scanner, min_pct: float) -> pd.Timestamp:
    """First session where >= min_pct % of the stocks trading that day have an SMA200."""
    trading = scanner.dp.close.notna().sum(axis=1)
    frac = scanner.dp.p["sma200"].notna().sum(axis=1) / trading.where(trading > 0) * 100
    ok = frac[frac >= min_pct]
    return ok.index[0] if len(ok) else scanner.dp.close.index[-1]


def build_signals(inputs: ScanInputs, cfg: Config, st_params: tuple[int, float], weights_name: str,
                  weights: dict[str, float], capital: float, start: str | None = None,
                  end: str | None = None) -> SignalSet:
    c = config_variant(cfg, st_params, weights)
    series, _ = compute_supertrend(inputs.weekly, c.supertrend.atr_period, c.supertrend.multiplier)
    inp = ScanInputs(**{**inputs.__dict__,
                        "st_series": series[["symbol_id", "week_end_date", "st_value", "direction"]]})
    scanner = Scanner(inp, c, capital, 1.0)
    first = warmup_start(scanner, cfg.backtest.warmup_min_sma200_pct)
    weeks = [w for w in scanner.scan_weeks() if w >= first
             and (start is None or w >= pd.Timestamp(start)) and (end is None or w <= pd.Timestamp(end))]
    out = []
    for w in weeks:
        res = scanner.run(w)
        stocks = res.stocks.set_index("symbol_id")[STOCK_COLS]
        sectors = res.sectors.set_index("industry")[SECTOR_COLS]
        out.append(WeekSignal(w, pd.Timestamp(week_monday([w])[0]), stocks, sectors, res.regime["regime"]))
    key = f"ST({st_params[0]},{st_params[1]:g})/{weights_name}"
    log.info("signals %s: %d weeks (%s .. %s)", key, len(out), weeks[0].date() if weeks else "-",
             weeks[-1].date() if weeks else "-")
    return SignalSet(key, (int(st_params[0]), float(st_params[1])), weights_name, out,
                     c.sectors.tradeable.model_dump())


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------
@dataclass
class PricePanel:
    open: pd.DataFrame     # index week Monday, columns symbol_id
    close: pd.DataFrame
    bench: pd.Series       # weekly benchmark close by Monday
    low: pd.DataFrame | None = None
    high: pd.DataFrame | None = None
    members: pd.DataFrame | None = None   # Monday x symbol: in the point-in-time universe (None = all)
    _sma: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_inputs(cls, inputs: ScanInputs, benchmark: str) -> "PricePanel":
        w = inputs.weekly.assign(monday=pd.DatetimeIndex(week_monday(inputs.weekly["week_end_date"])))
        op = w.pivot(index="monday", columns="symbol_id", values="open")
        cl = w.pivot(index="monday", columns="symbol_id", values="close")
        lo = w.pivot(index="monday", columns="symbol_id", values="low")
        hi = w.pivot(index="monday", columns="symbol_id", values="high")
        b = inputs.bench[inputs.bench.index_name == benchmark].set_index("date")["close"].sort_index()
        bw = b.groupby(pd.DatetimeIndex(week_monday(b.index))).last()
        mem = member_mask(inputs.members, cl.index, cl.columns) if inputs.members is not None else None
        return cls(op, cl, bw, lo, hi, mem)

    def universe_ew(self) -> pd.Series:
        """Equal-weight, weekly-rebalanced buy-and-hold of every stock in the panel (same survivor
        list the strategy trades): separates stock-picking skill from survivorship bias. With a
        point-in-time universe, each week holds that week's members only (no survivorship)."""
        if "ew" not in self._sma:
            r = (self.close / self.close.shift(1) - 1).clip(-0.5, 1.0)
            if self.members is not None:
                r = r.where(self.members.shift(1, fill_value=False))
            self._sma["ew"] = (1 + r.mean(axis=1).fillna(0)).cumprod()
        return self._sma["ew"]

    def sma(self, weeks: int) -> pd.DataFrame:
        if weeks not in self._sma:
            self._sma[weeks] = self.close.rolling(weeks, min_periods=weeks).mean()
        return self._sma[weeks]

    def px(self, frame: pd.DataFrame, monday: pd.Timestamp, sid: int) -> float:
        try:
            v = frame.at[monday, sid]
        except KeyError:
            return math.nan
        return float(v) if pd.notna(v) else math.nan

    def last_close(self, monday: pd.Timestamp, sid: int) -> float:
        if sid not in self.close.columns:
            return math.nan
        s = self.close.loc[:monday, sid].dropna()
        return float(s.iloc[-1]) if len(s) else math.nan


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
@dataclass
class Params:
    capital: float = 1_000_000
    max_positions: int = 10
    top_n_sectors: int = 5
    min_rs: float = 70
    sizing: str = "equal"               # equal | risk | fixed (position_size rupees per stock)
    position_size: float = 100_000
    position_pct: float = 0             # (fixed) size = max(position_size, this % of equity); 0 = off
    add_capital: bool = False           # add new capital when cash cannot cover a buy
    min_fill_pct: float = 0             # (fixed) skip a buy that cash covers below this % of its size
    risk_pct: float = 1.0
    max_position_pct: float = 20
    cost_round_trip_pct: float = 0.2
    slippage_pct: float = 0.05
    sector_exit: bool = False
    sector_exit_weeks: int = 2
    bear_mode: str = "half"
    min_score: float = 0
    score_weights: dict | None = None   # None = scanner stock_score
    score_penalties: bool = True        # subtract scanner penalties from a re-weighted score
    stop_pct: float = 0                 # protective stop below entry (0 = off)
    trail_pct: float = 0                # ratchet the stop to this % below the highest weekly close
    trail_ma_weeks: int = 0             # exit on a weekly close below this SMA (0 = off)
    time_stop_weeks: int = 0            # exit after N weeks if gain <= time_stop_min_pct (0 = off)
    time_stop_min_pct: float = 0
    entry_max_weeks_in_trend: int = 0   # 1 = enter only on the week the Supertrend flips bullish (0 = any)
    entry_filters: str = "all"          # all = every hard filter | tradeable = liquidity, price, not ASM only
    above_sma200: bool = False          # enter only when the price is above its 200-day SMA
    entry_ma: str = "none"              # moving-average entry rule: none | template (price > SMA50 > SMA150 >
                                        # SMA200, SMA200 rising) | stack (same, without "rising") | above50 |
                                        # rising200 (SMA200 rising)
    max_per_sector: int = 0             # hold at most this many stocks of one industry (0 = no cap)
    exit_score_below: float = 0         # also exit when the stock's current rank score falls below this (0 = off)
    take_profit: tuple = ()             # ((gain %, fraction of the current position to sell), ...) in order
    tp_fill: str = "intraweek"          # intraweek: limit order filled when the weekly high reaches the target |
                                        # close: decided at the weekly close, sold at the next open
    trim_above_pct: float = 0           # sell a position back to trim_to_pct of equity once it grows above this
    trim_to_pct: float = 10
    swap_min_score: float = 0           # out of cash: a signal scoring >= this replaces the weakest holding...
    swap_below_score: float = 60        # ...if that holding's current score is below this (0 = off)
    shuffle_seed: int = 0               # Monte Carlo: buy each week's eligible signals in a random order (0 = by score)
    skip_pct: float = 0                 # Monte Carlo: miss this % of signals at random (needs shuffle_seed)
    tax: str = "none"                   # capital-gains tax paid each financial year: none | current | historical
                                        # (backtest/tax.py); positions still open at the end are taxed as sold
    tax_cess_pct: float = 4.0

    @property
    def side_cost(self) -> float:
        return (self.cost_round_trip_pct / 2 + self.slippage_pct) / 100

    @classmethod
    def from_cfg(cls, cfg: Config, **over) -> "Params":
        b = cfg.backtest.model_dump()
        return cls(**{k: over.get(k, b[k]) for k in cls.__dataclass_fields__})


@dataclass
class Position:
    symbol_id: int
    symbol: str
    industry: str
    qty: int
    entry_price: float
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_score: float
    entry_idx: int
    stop: float = 0.0
    peak: float = 0.0
    tp_done: int = 0


@dataclass
class Result:
    equity: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict
    benchmark_metrics: dict
    params: dict = field(default_factory=dict)


def eligible_sectors(sectors: pd.DataFrame, top_n: int, t: dict) -> set[str] | None:
    """Tradeable sectors within the top N; None = no sector gate (top_n = 0)."""
    if top_n <= 0:
        return None
    ok = ((sectors["sector_rank"] <= top_n) & (sectors["st_breadth"] >= t["min_st_breadth"])
          & (sectors["rs_trend"] > t["min_rs_trend"]) & sectors["rrg_quadrant"].isin(t["quadrants"])
          & (sectors["stock_count"] >= t["min_stocks"]))
    return set(sectors.index[ok])


def rank_score(stocks: pd.DataFrame, p: "Params") -> pd.Series:
    if not p.score_weights:
        return stocks["stock_score"]
    total = sum(float(v) for v in p.score_weights.values())
    s = sum(float(v) * stocks[f"score_{k}"].fillna(0) for k, v in p.score_weights.items()) / total
    if p.score_penalties:
        s = s - stocks["penalty"].fillna(0)
    return s.clip(0, 100)


def eligible_stocks(stocks: pd.DataFrame, sectors_ok: set[str] | None, min_rs: float,
                    p: "Params | None" = None) -> pd.DataFrame:
    f = (stocks["f_supertrend"] & stocks["f_liquidity"] & stocks["f_price"] & stocks["f_not_asm"]
         & (stocks["rs_rating"] >= min_rs))
    if p is None or p.entry_filters == "all":
        f &= stocks["f_trend_template"] & stocks["f_52w_range"]
    if p is not None and p.above_sma200:
        f &= stocks["price"] > stocks["sma200"]
    if p is not None and p.entry_ma != "none":
        stack = ((stocks["price"] > stocks["sma50"]) & (stocks["sma50"] > stocks["sma150"])
                 & (stocks["sma150"] > stocks["sma200"]))
        rising = stocks["sma200_rising"].astype("boolean").fillna(False).astype(bool)
        f &= {"template": stocks["f_trend_template"].astype(bool), "stack": stack, "rising200": rising,
              "above50": stocks["price"] > stocks["sma50"]}[p.entry_ma]
    if p is not None and p.entry_max_weeks_in_trend > 0:
        f &= stocks["weeks_in_trend"] <= p.entry_max_weeks_in_trend
    if sectors_ok is not None:
        f &= stocks["industry"].isin(sectors_ok)
    out = stocks[f.fillna(False).astype(bool)]
    score = rank_score(out, p) if p is not None else out["stock_score"]
    out = out.assign(rank_score=score)
    if p is not None and p.min_score > 0:
        out = out[out["rank_score"] >= p.min_score]
    return out.sort_values("rank_score", ascending=False, kind="stable")


def simulate(sig: SignalSet, prices: PricePanel, p: Params) -> Result:
    if p.max_positions <= 0 and p.sizing == "equal":
        raise ValueError("max_positions = 0 (no limit) needs sizing fixed or risk; equal sizing divides by K")
    weeks = sig.weeks
    c = p.side_cost
    cash = p.capital
    contributed = p.capital
    held: dict[int, Position] = {}
    out_weeks: dict[str, int] = {}
    trades, curve = [], []
    rng = np.random.default_rng(p.shuffle_seed) if p.shuffle_seed else None
    book = TaxBook(p.tax, p.tax_cess_pct) if p.tax != "none" else None

    def equity_at(monday: pd.Timestamp) -> tuple[float, float]:
        inv = 0.0
        for pos in held.values():
            px = prices.px(prices.close, monday, pos.symbol_id)
            if math.isnan(px):
                px = prices.last_close(monday, pos.symbol_id)
            inv += pos.qty * (px if not math.isnan(px) else pos.entry_price)
        return cash + inv, inv

    def close_trade(pos: Position, price: float, when: pd.Timestamp, idx: int, reason: str):
        nonlocal cash
        cash += pos.qty * price * (1 - c)
        pnl = pos.qty * (price * (1 - c) - pos.entry_price * (1 + c))
        trades.append({
            "symbol_id": pos.symbol_id, "symbol": pos.symbol, "industry": pos.industry,
            "signal_date": pos.signal_date, "entry_date": pos.entry_date, "entry_price": pos.entry_price,
            "exit_date": when, "exit_price": price, "qty": pos.qty, "pnl": pnl,
            "pnl_pct": (price * (1 - c) / (pos.entry_price * (1 + c)) - 1) * 100,
            "weeks_held": idx - pos.entry_idx, "exit_reason": reason, "entry_score": pos.entry_score,
        })
        if book is not None:
            book.add(pos.entry_date, when, pnl)

    eq0, _ = equity_at(weeks[0].monday)
    curve.append({"date": weeks[0].week_end, "equity": eq0, "invested": 0.0, "positions": 0,
                  "flow": 0.0, "contributed": contributed})

    for i in range(len(weeks) - 1):
        w, nxt = weeks[i], weeks[i + 1]
        # sector top-N streaks (rank only, as specified: "drops out of the top N")
        for ind, rank in w.sectors["sector_rank"].items():
            out_weeks[ind] = out_weeks.get(ind, 0) + 1 if rank > p.top_n_sectors else 0

        # 1. exits decided at W's close, filled at next open
        scores_now = rank_score(w.stocks, p) if p.exit_score_below > 0 and held else None
        for sid, pos in list(held.items()):
            row = w.stocks.loc[sid] if sid in w.stocks.index else None
            if row is None:
                reason = "stale"
            elif row["direction"] == "Bearish":
                reason = "supertrend"
            elif scores_now is not None and scores_now.get(sid, math.inf) < p.exit_score_below:
                reason = "score"
            elif p.sector_exit and p.top_n_sectors > 0 and out_weeks.get(pos.industry, 0) >= p.sector_exit_weeks:
                reason = "sector"
            elif p.trail_ma_weeks and prices.px(prices.close, w.monday, sid) < prices.px(
                    prices.sma(p.trail_ma_weeks), w.monday, sid):
                reason = "ma"
            elif (p.time_stop_weeks and i - pos.entry_idx + 1 >= p.time_stop_weeks
                  and prices.px(prices.close, w.monday, sid) <= pos.entry_price * (1 + p.time_stop_min_pct / 100)):
                reason = "time"
            else:
                continue
            px = prices.px(prices.open, nxt.monday, sid)
            if math.isnan(px):
                px = prices.last_close(w.monday, sid)
            close_trade(pos, px, nxt.week_end, i + 1, reason)
            del held[sid]

        def sell_open(sid: int) -> float:
            px = prices.px(prices.open, nxt.monday, sid)
            return prices.last_close(w.monday, sid) if math.isnan(px) else px

        # 1a. profit targets checked on W's close, sold at the next open (tp_fill = close)
        if p.take_profit and p.tp_fill == "close":
            for sid, pos in held.items():
                cl = prices.px(prices.close, w.monday, sid)
                while pos.tp_done < len(p.take_profit) and not math.isnan(cl) \
                        and cl >= pos.entry_price * (1 + p.take_profit[pos.tp_done][0] / 100):
                    cut = math.floor(pos.qty * p.take_profit[pos.tp_done][1])
                    pos.tp_done += 1
                    if 0 < cut < pos.qty:
                        close_trade(replace(pos, qty=cut), sell_open(sid), nxt.week_end, i + 1, f"tp{pos.tp_done}")
                        pos.qty -= cut

        # 1b. trim winners that outgrew trim_above_pct of equity (the rest of the position stays)
        if p.trim_above_pct > 0 and held:
            equity, _ = equity_at(w.monday)
            for sid, pos in held.items():
                cl = prices.px(prices.close, w.monday, sid)
                if math.isnan(cl) or pos.qty * cl <= equity * p.trim_above_pct / 100:
                    continue
                cut = pos.qty - math.floor(equity * p.trim_to_pct / 100 / cl)
                if 0 < cut < pos.qty:
                    close_trade(replace(pos, qty=cut), sell_open(sid), nxt.week_end, i + 1, "trim")
                    pos.qty -= cut

        # 2. entries
        flow = 0.0
        slots = math.inf if p.max_positions <= 0 else p.max_positions - len(held)
        if slots > 0 and not (w.regime == "bear" and p.bear_mode == "skip"):
            size_factor = 0.5 if (w.regime == "bear" and p.bear_mode == "half") else 1.0
            equity, _ = equity_at(w.monday)
            cands = eligible_stocks(w.stocks, eligible_sectors(w.sectors, p.top_n_sectors, sig.tradeable),
                                    p.min_rs, p)
            if rng is not None and len(cands):
                cands = cands.iloc[rng.permutation(len(cands))]
                if p.skip_pct > 0:
                    cands = cands[rng.random(len(cands)) >= p.skip_pct / 100]
            for sid, row in cands.iterrows():
                if slots == 0:
                    break
                if sid in held:
                    continue
                if p.max_per_sector and sum(h.industry == row["industry"] for h in held.values()) >= p.max_per_sector:
                    continue
                px = prices.px(prices.open, nxt.monday, sid)
                if math.isnan(px) or px <= 0:
                    continue
                cap_value = equity * p.max_position_pct / 100
                if p.sizing == "risk":
                    per_share = px - row["st_value"]
                    if not per_share > 0:
                        continue
                    qty = math.floor(equity * p.risk_pct / 100 * size_factor / per_share)
                    qty = min(qty, math.floor(cap_value / px))
                elif p.sizing == "fixed":
                    size = max(p.position_size, equity * p.position_pct / 100)
                    qty = max(1, math.floor(size * size_factor / px))
                    target_qty = qty
                else:
                    qty = math.floor(min(equity / p.max_positions * size_factor, cap_value) / px)
                if p.add_capital:
                    # new money covers at most position_size: a larger size comes from own cash only
                    floor_qty = max(1, math.floor(p.position_size * size_factor / px)) if p.sizing == "fixed" else qty
                    short = min(qty, floor_qty) * px * (1 + c) - cash
                    if short > 0:
                        cash += short
                        flow += short
                        contributed += short
                if (p.swap_min_score > 0 and row["rank_score"] >= p.swap_min_score
                        and qty * px * (1 + c) > cash and held):
                    # out of cash: replace the weakest holding (current score below swap_below_score)
                    held_scores = {h: rank_score(w.stocks.loc[[h]], p).iloc[0] if h in w.stocks.index else -1.0
                                   for h, hp in held.items() if hp.entry_idx <= i}
                    if held_scores:
                        weak = min(held_scores, key=held_scores.get)
                        if held_scores[weak] < p.swap_below_score:
                            close_trade(held[weak], sell_open(weak), nxt.week_end, i + 1, "swap")
                            del held[weak]
                            slots += 1
                qty = min(qty, math.floor(cash / (px * (1 + c))))
                if qty <= 0 or (p.sizing == "fixed" and qty < target_qty * p.min_fill_pct / 100):
                    continue
                cash -= qty * px * (1 + c)
                stop = px * (1 - p.stop_pct / 100) if p.stop_pct else 0.0
                held[sid] = Position(int(sid), row["symbol"], row["industry"], int(qty), px, w.week_end,
                                     nxt.week_end, float(row["rank_score"]), i + 1, stop, px)
                slots -= 1

        # 3. protective stops during week W+1 (entries of this week included)
        if (p.stop_pct or p.trail_pct) and prices.low is not None:
            for sid, pos in list(held.items()):
                lo = prices.px(prices.low, nxt.monday, sid)
                if pos.stop > 0 and not math.isnan(lo) and lo <= pos.stop:
                    op = prices.px(prices.open, nxt.monday, sid)
                    close_trade(pos, min(op, pos.stop) if not math.isnan(op) else pos.stop,
                                nxt.week_end, i + 1, "stop")
                    del held[sid]
                    continue
                cl = prices.px(prices.close, nxt.monday, sid)
                if not math.isnan(cl):
                    pos.peak = max(pos.peak, cl)
                    if p.trail_pct:
                        pos.stop = max(pos.stop, pos.peak * (1 - p.trail_pct / 100))

        # 3b. profit targets as resting limit orders during week W+1 (tp_fill = intraweek)
        if p.take_profit and p.tp_fill == "intraweek" and prices.high is not None:
            for sid, pos in held.items():
                hi = prices.px(prices.high, nxt.monday, sid)
                op = prices.px(prices.open, nxt.monday, sid)
                while pos.tp_done < len(p.take_profit) and not math.isnan(hi):
                    gain, frac = p.take_profit[pos.tp_done]
                    target = pos.entry_price * (1 + gain / 100)
                    if hi < target:
                        break
                    # a gap above the target fills at the open (not in the entry week: bought at that open)
                    fill = op if (pos.entry_idx < i + 1 and not math.isnan(op) and op >= target) else target
                    cut = math.floor(pos.qty * frac)
                    pos.tp_done += 1
                    if 0 < cut < pos.qty:
                        close_trade(replace(pos, qty=cut), fill, nxt.week_end, i + 1, f"tp{pos.tp_done}")
                        pos.qty -= cut

        # 3c. capital-gains tax for every financial year that has ended, paid from cash
        if book is not None:
            cash -= book.settle(before_fy=fy_of(nxt.week_end))

        # 4. mark to market at next week's close
        eq, inv = equity_at(nxt.monday)
        curve.append({"date": nxt.week_end, "equity": eq, "invested": inv, "positions": len(held),
                      "flow": flow, "contributed": contributed})

    # close what is still open at the final close (reported as 'end')
    last = weeks[-1]
    for sid, pos in list(held.items()):
        px = prices.px(prices.close, last.monday, sid)
        if math.isnan(px):
            px = prices.last_close(last.monday, sid)
        close_trade(pos, px, last.week_end, len(weeks) - 1, "end")
        del held[sid]
    if book is not None:
        # the rest is taxed as if sold at the end (like selling an index fund at the end)
        curve[-1]["equity"] -= book.settle()

    equity = pd.DataFrame(curve)
    bench = prices.bench.reindex([w.monday for w in weeks]).ffill().to_numpy()
    flows = equity["flow"].to_numpy(float)
    # added capital lands at the week's open (~ the previous close): buy index units with it there
    units = p.capital / bench[0] + np.concatenate([[0.0], np.cumsum(flows[1:] / bench[:-1])])
    equity["benchmark"] = units * bench
    equity["nav"] = time_weighted(equity["equity"].to_numpy(float), flows, p.capital)
    equity["invested_pct"] = equity["invested"] / equity["equity"] * 100
    equity["drawdown"] = (equity["nav"] / equity["nav"].cummax() - 1) * 100
    trades_df = pd.DataFrame(trades)
    bm = curve_metrics(benchmark_nav(equity, p.capital), equity["date"])
    ew = prices.universe_ew().reindex([w.monday for w in weeks]).ffill()
    bm["universe_ew"] = curve_metrics(ew, equity["date"])
    m = metrics(equity, trades_df, p.capital)
    if book is not None:
        m["tax_paid"] = float(sum(book.paid.values()))
        m["tax_by_fy"] = {f"FY{fy}-{(fy + 1) % 100:02d}": round(v) for fy, v in sorted(book.paid.items())}
    return Result(equity, trades_df, m, bm, asdict(p))


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def time_weighted(equity: np.ndarray, flows: np.ndarray, start: float) -> np.ndarray:
    """Equity with external cash flows removed: each week's return is close / (prev close + flow)."""
    r = equity[1:] / (equity[:-1] + flows[1:])
    return start * np.concatenate([[1.0], np.cumprod(r)])


def benchmark_nav(equity: pd.DataFrame, capital: float) -> pd.Series:
    """Time-weighted benchmark (the plain index scaled to capital), with any added capital taken out."""
    if "flow" not in equity:
        return equity["benchmark"]
    return pd.Series(time_weighted(equity["benchmark"].to_numpy(float), equity["flow"].to_numpy(float), capital),
                     index=equity.index)


def xirr(dates: pd.Series, amounts: np.ndarray) -> float | None:
    """Annual money-weighted return of dated cash flows (negative = paid in)."""
    t = (pd.DatetimeIndex(dates) - pd.Timestamp(dates.iloc[0])).days.to_numpy() / 365.25
    npv = lambda r: float((amounts / (1 + r) ** t).sum())  # noqa: E731
    lo, hi = -0.99, 10.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2 * 100


def curve_metrics(values: pd.Series, dates: pd.Series) -> dict:
    v = values.to_numpy(float)
    years = max((pd.Timestamp(dates.iloc[-1]) - pd.Timestamp(dates.iloc[0])).days / 365.25, 1e-9)
    rets = np.diff(v) / v[:-1]
    dd = v / np.maximum.accumulate(v) - 1
    return {
        "total_return_pct": (v[-1] / v[0] - 1) * 100,
        "cagr_pct": ((v[-1] / v[0]) ** (1 / years) - 1) * 100,
        "max_drawdown_pct": float(dd.min() * 100),
        "sharpe": float(rets.mean() / rets.std() * np.sqrt(52)) if rets.std() > 0 else None,
        "volatility_pct": float(rets.std() * np.sqrt(52) * 100),
        "years": years,
    }


def yearly_returns(values: pd.Series, dates: pd.Series) -> dict[str, float]:
    s = pd.Series(values.to_numpy(float), index=pd.DatetimeIndex(dates))
    last = s.groupby(s.index.year).last()
    prev = last.shift(1)
    prev.iloc[0] = s.iloc[0]
    return {str(y): float(v) for y, v in ((last / prev - 1) * 100).items()}


def metrics(equity: pd.DataFrame, trades: pd.DataFrame, capital: float) -> dict:
    nav = equity["nav"] if "nav" in equity else equity["equity"]
    m = curve_metrics(nav, equity["date"])
    m["mar"] = m["cagr_pct"] / -m["max_drawdown_pct"] if m["max_drawdown_pct"] < 0 else None
    m["yearly"] = yearly_returns(nav, equity["date"])
    if "benchmark" in equity:
        m["yearly_benchmark"] = yearly_returns(benchmark_nav(equity, capital), equity["date"])
    m["final_equity"] = float(equity["equity"].iloc[-1])
    if "contributed" in equity and equity["contributed"].iloc[-1] > capital:
        added = equity["contributed"].iloc[-1]
        flows = -equity["flow"].to_numpy(float).copy()
        flows[0] -= capital
        flows[-1] += m["final_equity"]
        m["capital_contributed"] = float(added)
        m["capital_added"] = float(added - capital)
        m["net_profit"] = m["final_equity"] - added
        m["return_on_capital_pct"] = (m["final_equity"] / added - 1) * 100
        m["xirr_pct"] = xirr(equity["date"], flows)
        if "benchmark" in equity:
            m["benchmark_final"] = float(equity["benchmark"].iloc[-1])
            bflows = flows.copy()
            bflows[-1] += m["benchmark_final"] - m["final_equity"]
            m["benchmark_xirr_pct"] = xirr(equity["date"], bflows)
    m["exposure_pct"] = float(equity["invested_pct"].iloc[1:].mean()) if len(equity) > 1 else 0.0
    n = len(trades)
    m["trades"] = n
    m["trades_per_year"] = n / m["years"]
    if n:
        wins, losses = trades[trades.pnl > 0], trades[trades.pnl <= 0]
        m["win_rate_pct"] = len(wins) / n * 100
        m["avg_win_pct"] = float(wins.pnl_pct.mean()) if len(wins) else 0.0
        m["avg_loss_pct"] = float(losses.pnl_pct.mean()) if len(losses) else 0.0
        m["profit_factor"] = float(wins.pnl.sum() / -losses.pnl.sum()) if losses.pnl.sum() < 0 else None
        m["expectancy_pct"] = float(trades.pnl_pct.mean())
        m["expectancy_inr"] = float(trades.pnl.mean())
        m["avg_weeks_held"] = float(trades.weeks_held.mean())
        m["exit_reasons"] = trades.exit_reason.value_counts().to_dict()
    else:
        m.update(win_rate_pct=None, avg_win_pct=None, avg_loss_pct=None, profit_factor=None,
                 expectancy_pct=None, expectancy_inr=None, avg_weeks_held=None, exit_reasons={})
    return m

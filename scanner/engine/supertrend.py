"""Supertrend, bit-for-bit with TradingView Pine v5 `ta.supertrend(factor, atrPeriod)`.

Pine reference:
    atr        = ta.rma(ta.tr(true), atrPeriod)      # RMA seeded with SMA of first N TRs
    upperBand  = hl2 + factor * atr ; lowerBand = hl2 - factor * atr
    lowerBand := lowerBand > nz(lowerBand[1]) or close[1] < nz(lowerBand[1]) ? lowerBand : nz(lowerBand[1])
    upperBand := upperBand < nz(upperBand[1]) or close[1] > nz(upperBand[1]) ? upperBand : nz(upperBand[1])
    direction  = na(atr[1]) ? down
               : prev was down (st == upperBand) ? (close > upperBand ? up : down)
               : (close < lowerBand ? down : up)
    superTrend = up ? lowerBand : upperBand

Convention here: direction 1 = bullish (TradingView -1), -1 = bearish (TradingView 1).

One numba kernel processes every symbol in a single call. Each segment (symbol) can
start from scratch (warm-up) or from a saved state (incremental weekly update); both
paths produce identical numbers.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numba import njit

STATE_COLUMNS = ["symbol_id", "week_end_date", "atr_period", "multiplier", "close", "atr",
                 "upper_band", "lower_band", "st_value", "direction", "flip_date", "flip_price",
                 "weeks_in_trend"]
SERIES_COLUMNS = ["symbol_id", "week_end_date", "atr", "upper_band", "lower_band", "st_value",
                  "direction"]


@njit(cache=True)
def _kernel(high, low, close, starts, period, mult,
            s_has, s_close, s_atr, s_upper, s_lower, s_dir, s_weeks):
    n = high.shape[0]
    atr = np.full(n, np.nan)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    st = np.full(n, np.nan)
    direction = np.zeros(n, np.int8)
    weeks = np.zeros(n, np.int32)
    flip = np.zeros(n, np.bool_)

    for s in range(starts.shape[0] - 1):
        a, b = starts[s], starts[s + 1]
        if s_has[s]:
            prev_close, prev_atr = s_close[s], s_atr[s]
            prev_up, prev_lo = s_upper[s], s_lower[s]
            prev_dir, prev_weeks = s_dir[s], s_weeks[s]
        else:
            prev_close, prev_atr = np.nan, np.nan
            prev_up, prev_lo = 0.0, 0.0          # Pine nz(band[1]) while bands are na
            prev_dir, prev_weeks = 0, 0
        tr_sum, tr_count = 0.0, 0

        for i in range(a, b):
            h, l, c = high[i], low[i], close[i]
            if np.isnan(prev_close):
                tr = h - l
            else:
                tr = max(h - l, abs(h - prev_close), abs(l - prev_close))

            if np.isnan(prev_atr):                 # RMA warm-up: SMA of first `period` TRs
                tr_sum += tr
                tr_count += 1
                cur_atr = tr_sum / period if tr_count == period else np.nan
            else:
                cur_atr = (prev_atr * (period - 1) + tr) / period

            if np.isnan(cur_atr):
                prev_close = c
                continue

            hl2 = (h + l) / 2.0
            up = hl2 + mult * cur_atr
            lo = hl2 - mult * cur_atr
            if not (lo > prev_lo or prev_close < prev_lo):
                lo = prev_lo
            if not (up < prev_up or prev_close > prev_up):
                up = prev_up

            if np.isnan(prev_atr):
                d = -1
            elif prev_dir == -1:
                d = 1 if c > up else -1
            else:
                d = -1 if c < lo else 1

            if prev_dir == 0:
                w = 1
            elif d != prev_dir:
                w = 1
                flip[i] = True
            else:
                w = prev_weeks + 1

            atr[i], upper[i], lower[i] = cur_atr, up, lo
            st[i] = lo if d == 1 else up
            direction[i], weeks[i] = d, w
            prev_close, prev_atr, prev_up, prev_lo, prev_dir, prev_weeks = c, cur_atr, up, lo, d, w

    return atr, upper, lower, st, direction, weeks, flip


def supertrend_arrays(high, low, close, period: int = 10, mult: float = 3.0):
    """Single series, from scratch. Returns (atr, upper, lower, st, direction, weeks, flip)."""
    starts = np.array([0, len(close)], dtype=np.int64)
    z = np.zeros(1)
    return _kernel(np.asarray(high, float), np.asarray(low, float), np.asarray(close, float),
                   starts, period, float(mult), np.zeros(1, np.bool_), z, z, z, z,
                   np.zeros(1, np.int8), np.zeros(1, np.int32))


def compute_supertrend(weekly: pd.DataFrame, period: int, mult: float,
                       state: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Supertrend for many symbols in one kernel call.

    weekly: [symbol_id, week_end_date, high, low, close]; for symbols present in `state`
            it must contain only the weeks after state.week_end_date.
    state:  supertrend_state rows (STATE_COLUMNS); symbols absent start from scratch.

    Returns (series, final_state):
      series      SERIES_COLUMNS + [weeks_in_trend, is_flip, close], warm-up rows dropped
      final_state STATE_COLUMNS, one row per symbol with a valid Supertrend
    """
    w = weekly.sort_values(["symbol_id", "week_end_date"], kind="stable").reset_index(drop=True)
    sym = w["symbol_id"].to_numpy()
    bounds = np.flatnonzero(np.diff(sym)) + 1
    starts = np.concatenate([[0], bounds, [len(w)]]).astype(np.int64)
    seg_syms = sym[starts[:-1]] if len(w) else np.array([], dtype=sym.dtype)

    nseg = len(seg_syms)
    s_has = np.zeros(nseg, np.bool_)
    s_close, s_atr, s_up, s_lo = (np.zeros(nseg) for _ in range(4))
    s_dir, s_weeks = np.zeros(nseg, np.int8), np.zeros(nseg, np.int32)
    if state is not None and not state.empty:
        st = state.set_index("symbol_id").reindex(seg_syms)
        s_has = st["atr"].notna().to_numpy()
        s_close = st["close"].fillna(0).to_numpy(float)
        s_atr = st["atr"].fillna(0).to_numpy(float)
        s_up = st["upper_band"].fillna(0).to_numpy(float)
        s_lo = st["lower_band"].fillna(0).to_numpy(float)
        s_dir = st["direction"].fillna(0).to_numpy(np.int8)
        s_weeks = st["weeks_in_trend"].fillna(0).to_numpy(np.int32)

    atr, up, lo, stv, d, wk, fl = _kernel(
        w["high"].to_numpy(float), w["low"].to_numpy(float), w["close"].to_numpy(float),
        starts, int(period), float(mult), s_has, s_close, s_atr, s_up, s_lo, s_dir, s_weeks)

    series = pd.DataFrame({
        "symbol_id": sym, "week_end_date": w["week_end_date"].to_numpy(),
        "atr": atr, "upper_band": up, "lower_band": lo, "st_value": stv, "direction": d,
        "weeks_in_trend": wk, "is_flip": fl, "close": w["close"].to_numpy(float),
    })
    series = series[series["direction"] != 0].reset_index(drop=True)
    return series, final_state(series, period, mult, state)


def final_state(series: pd.DataFrame, period: int, mult: float,
                prior: pd.DataFrame | None = None) -> pd.DataFrame:
    """Last row per symbol + flip_date/flip_price of the latest flip (carried from prior state)."""
    if series.empty:
        return pd.DataFrame(columns=STATE_COLUMNS)
    last = series.groupby("symbol_id", sort=False).tail(1).set_index("symbol_id")
    flips = (series[series["is_flip"]].groupby("symbol_id", sort=False).tail(1)
             .set_index("symbol_id")[["week_end_date", "close"]]
             .rename(columns={"week_end_date": "flip_date", "close": "flip_price"}))
    out = last.join(flips)
    if prior is not None and not prior.empty:
        p = prior.set_index("symbol_id")[["flip_date", "flip_price"]].reindex(out.index)
        no_new_flip = out["flip_date"].isna()
        out.loc[no_new_flip, "flip_date"] = p.loc[no_new_flip, "flip_date"]
        out.loc[no_new_flip, "flip_price"] = p.loc[no_new_flip, "flip_price"]
    out = out.reset_index().assign(atr_period=period, multiplier=mult)
    return out[STATE_COLUMNS]

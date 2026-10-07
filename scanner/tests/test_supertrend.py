"""Supertrend tests.

`pine_supertrend` below is an independent, line-by-line transcription of Pine v5
ta.tr / ta.rma / ta.supertrend with explicit `na` handling, used as the oracle.

Real TradingView readings: put rows in tests/fixtures/tradingview_supertrend.csv
(symbol,week_end_date,st_value,direction[,period,multiplier]) — direction 1 = bullish.
Compare with `data.yf_auto_adjust: false` (TradingView charts are split-adjusted only).
"""
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from engine.supertrend import compute_supertrend, supertrend_arrays

NA = None


def _nz(x):
    return 0.0 if x is None else x


def pine_supertrend(high, low, close, period, factor):
    n = len(close)
    tr = []
    for i in range(n):
        if i == 0:
            tr.append(high[i] - low[i])  # ta.tr(true): na close[1] -> high - low
        else:
            tr.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    atr = [NA] * n
    for i in range(n):  # ta.rma
        if i < period - 1:
            continue
        if atr[i - 1] is None if i > 0 else True:
            atr[i] = sum(tr[i - period + 1:i + 1]) / period
        else:
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    upper, lower, st, dirn = [NA] * n, [NA] * n, [NA] * n, [NA] * n
    for i in range(n):
        if atr[i] is None:
            continue
        src = (high[i] + low[i]) / 2
        ub, lb = src + factor * atr[i], src - factor * atr[i]
        plb = _nz(lower[i - 1] if i > 0 else None)
        pub = _nz(upper[i - 1] if i > 0 else None)
        pc = close[i - 1] if i > 0 else math.nan
        lb = lb if (lb > plb or pc < plb) else plb
        ub = ub if (ub < pub or pc > pub) else pub
        prev_atr = atr[i - 1] if i > 0 else None
        prev_st = st[i - 1] if i > 0 else None
        if prev_atr is None:
            d = 1
        elif prev_st == upper[i - 1]:
            d = -1 if close[i] > ub else 1
        else:
            d = 1 if close[i] < lb else -1
        upper[i], lower[i] = ub, lb
        st[i] = lb if d == -1 else ub
        dirn[i] = d
    return atr, st, dirn


def random_ohlc(n, seed=0, start=100.0):
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(0.002, 0.04, n)))
    open_ = np.r_[start, close[:-1]] * np.exp(rng.normal(0, 0.01, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.03, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.03, n))
    return high, low, close


@pytest.mark.parametrize("period,mult,seed", [(10, 3.0, 0), (10, 2.0, 1), (7, 3.0, 2), (3, 1.0, 3)])
def test_matches_pine_transcription(period, mult, seed):
    h, l, c = random_ohlc(300, seed)
    atr, _, _, st, d, _, _ = supertrend_arrays(h, l, c, period, mult)
    ref_atr, ref_st, ref_dir = pine_supertrend(list(h), list(l), list(c), period, mult)
    valid = np.array([x is not None for x in ref_atr])
    assert (~np.isnan(atr) == valid).all()
    np.testing.assert_allclose(atr[valid], np.array(ref_atr, dtype=float)[valid], rtol=1e-12)
    np.testing.assert_allclose(st[valid], np.array(ref_st, dtype=float)[valid], rtol=1e-12)
    # Pine: -1 up / 1 down  ->  ours: 1 bullish / -1 bearish
    assert (d[valid] == -np.array(ref_dir, dtype=float)[valid]).all()
    assert (d[~valid] == 0).all()
    assert len(set(d[valid])) == 2  # the series actually flips


def test_hand_computed_atr_seed_and_rma():
    # period 3: ATR at bar 2 = SMA(TR0..TR2); bar 3 = (ATR2*2 + TR3)/3
    h = [10, 11, 12, 13]
    l = [9, 10, 10, 12]
    c = [9.5, 10.5, 11.5, 12.5]
    atr, *_ = supertrend_arrays(h, l, c, 3, 1.0)
    tr = [1, max(1, 1.5, 0.5), max(2, 1.5, 0.5), max(1, 1.5, 0.5)]  # [1, 1.5, 2, 1.5]
    assert np.isnan(atr[:2]).all()
    assert atr[2] == pytest.approx(sum(tr[:3]) / 3)
    assert atr[3] == pytest.approx((atr[2] * 2 + tr[3]) / 3)


def _panel(n_by_symbol, seed=0):
    frames = []
    for i, n in enumerate(n_by_symbol):
        h, l, c = random_ohlc(n, seed + i)
        frames.append(pd.DataFrame({
            "symbol_id": i + 1,
            "week_end_date": pd.date_range("2020-01-03", periods=n, freq="W-FRI"),
            "high": h, "low": l, "close": c}))
    return pd.concat(frames, ignore_index=True)


def test_panel_segments_independent():
    panel = _panel([120, 5, 80])  # symbol 2 never finishes warm-up
    series, state = compute_supertrend(panel, 10, 3.0)
    for sid in (1, 3):
        g = panel[panel.symbol_id == sid]
        _, _, _, st, d, _, _ = supertrend_arrays(g.high, g.low, g.close, 10, 3.0)
        s = series[series.symbol_id == sid]
        np.testing.assert_allclose(s["st_value"], st[d != 0])
    assert 2 not in set(series.symbol_id) and 2 not in set(state.symbol_id)


@pytest.mark.parametrize("split", [11, 60, 119])
def test_incremental_equals_full(split):
    panel = _panel([150, 150], seed=7)
    full_series, full_state = compute_supertrend(panel, 10, 3.0)

    head = panel.groupby("symbol_id").head(split)
    tail = panel.drop(head.index)
    _, state = compute_supertrend(head, 10, 3.0)
    # incremental one week at a time, like the weekly job
    for _, week in tail.groupby("week_end_date"):
        inc_series, new_state = compute_supertrend(week, 10, 3.0, state)
        state = pd.concat([state[~state.symbol_id.isin(new_state.symbol_id)], new_state])

    state = state.sort_values("symbol_id").reset_index(drop=True)
    exp = full_state.sort_values("symbol_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(state, exp, check_dtype=False)


def test_flip_tracking():
    panel = _panel([200], seed=3)
    series, state = compute_supertrend(panel, 10, 3.0)
    flips = series[series.is_flip]
    last_flip = flips.iloc[-1]
    s = state.iloc[0]
    assert s.flip_date == last_flip.week_end_date and s.flip_price == last_flip.close
    assert s.weeks_in_trend == (series.week_end_date >= last_flip.week_end_date).sum()
    assert s.direction == series.direction.iloc[-1]


def test_no_flip_yet_leaves_flip_null():
    n = 30
    close = np.linspace(100, 200, n)  # steady uptrend after warm-up -> never flips back
    panel = pd.DataFrame({"symbol_id": 1, "week_end_date": pd.date_range("2020-01-03", periods=n, freq="W-FRI"),
                          "high": close * 1.01, "low": close * 0.99, "close": close})
    series, state = compute_supertrend(panel, 10, 3.0)
    # first valid bar is bearish by definition, so exactly one flip to bullish is allowed
    assert series.is_flip.sum() <= 1
    if series.is_flip.sum() == 0:
        assert pd.isna(state.iloc[0].flip_date)


FIXTURE = Path(__file__).parent / "fixtures" / "tradingview_supertrend.csv"


@pytest.mark.skipif(not FIXTURE.exists(), reason="no TradingView fixture (see module docstring)")
def test_tradingview_reference_values():
    import db

    ref = pd.read_csv(FIXTURE, parse_dates=["week_end_date"])
    with db.connection() as conn:
        for (symbol, period, mult), g in ref.groupby(
                ["symbol", ref.get("period", 10), ref.get("multiplier", 3.0)]):
            w = db.read_frame(conn, """SELECT w.symbol_id, w.week_end_date, w.high, w.low, w.close
                FROM weekly_candles w JOIN symbols s ON s.id = w.symbol_id
                WHERE s.symbol = %s ORDER BY w.week_end_date""", (symbol,))
            series, _ = compute_supertrend(w, int(period), float(mult))
            got = series.assign(week_end_date=pd.to_datetime(series.week_end_date)).set_index("week_end_date")
            for _, r in g.iterrows():
                row = got.loc[r.week_end_date]
                assert row.direction == r.direction, (symbol, r.week_end_date)
                assert row.st_value == pytest.approx(r.st_value, rel=2e-3), (symbol, r.week_end_date)

import numpy as np
import pandas as pd
import pytest

from config import load_config
from engine.regime import market_regime
from engine.relative_strength import rs_rating
from engine.scoring import apply_filters, apply_signals, lin, score, score_stocks, trade_plan
from engine.sectors import quadrant

CFG = load_config()


def stock(**over):
    """A stock that passes every hard filter."""
    base = dict(
        symbol="GOOD", industry="IT", direction="Bullish", price=110.0, st_value=100.0,
        weeks_in_trend=3, is_new_flip=False, flip_price=100.0, pct_since_flip=10.0, pct_from_st=9.09,
        sma50=100.0, sma150=90.0, sma200=80.0, sma200_prev=78.0,
        pct_above_low=60.0, pct_from_high=3.0, rs_rating=90.0, avg_turnover_20d_cr=50.0,
        in_asm_gsm=False, sector_tradeable=True, sector_rank=1, confirm_vol_ratio=2.0,
        tightness_ratio=0.6, atr_pct=2.0, atr_pct_prev=3.0, delivery_pct_latest=60.0,
        delivery_pct_20d=50.0, rs_vs_sector=5.0,
    )
    base.update(over)
    return base


def frame(*rows):
    return pd.DataFrame(list(rows))


def test_lin_both_directions():
    x = pd.Series([0.5, 1.0, 2.0, 3.0])
    assert lin(x, 0.8, 2.0).round(1).tolist() == [0.0, 16.7, 100.0, 100.0]
    assert lin(pd.Series([3.0, 9.0, 15.0, 20.0]), 15, 3).tolist() == [100.0, 50.0, 0.0, 0.0]


def test_all_filters_pass():
    f = apply_filters(frame(stock()), CFG)
    assert f.iloc[0].all() and bool(f.qualified.iloc[0])


@pytest.mark.parametrize("over,failed", [
    ({"direction": "Bearish"}, "f_supertrend"),
    ({"sector_tradeable": False}, "f_sector"),
    ({"sma50": 120.0}, "f_trend_template"),                 # close below SMA50
    ({"sma200_prev": 81.0}, "f_trend_template"),            # SMA200 falling
    ({"sma200": np.nan}, "f_trend_template"),               # new listing: no SMA200
    ({"pct_above_low": 20.0}, "f_52w_range"),
    ({"pct_from_high": 30.0}, "f_52w_range"),
    ({"rs_rating": 69.0}, "f_rs"),
    ({"avg_turnover_20d_cr": 5.0}, "f_liquidity"),
    ({"price": 40.0, "st_value": 35.0, "sma50": 39.0, "sma150": 38.0, "sma200": 37.0, "sma200_prev": 36.0}, "f_price"),
    ({"in_asm_gsm": True}, "f_not_asm"),
])
def test_each_filter_fails(over, failed):
    f = apply_filters(frame(stock(**over)), CFG).iloc[0]
    assert not f[failed] and not f["qualified"]
    others = [c for c in f.index if c not in (failed, "qualified")]
    assert f[others].all(), f[others][~f[others]]


def test_signals_and_penalties():
    rows = frame(
        stock(),
        stock(symbol="LATE", weeks_in_trend=25, pct_since_flip=50.0, price=140.0, sma50=100.0, atr_pct=9.0),
    )
    s = apply_signals(rows, CFG)
    good, late = s.iloc[0], s.iloc[1]
    for k in ("sig_fresh_flip", "sig_tight_stop", "sig_vol_confirm", "sig_vcp", "sig_accumulation",
              "sig_near_high", "sig_sector_leader"):
        assert good[k], k
    assert not good[["pen_overextended", "pen_late_stage"]].any()
    assert late["pen_overextended"] and late["pen_late_stage"] and late["pen_high_atr"]
    assert not late["sig_fresh_flip"]


def test_freshness_decay_and_bearish_zero():
    rows = frame(stock(weeks_in_trend=1), stock(weeks_in_trend=6), stock(weeks_in_trend=10),
                 stock(weeks_in_trend=30), stock(direction="Bearish", weeks_in_trend=2))
    sc = score(rows, apply_signals(rows, CFG), 20, CFG)
    assert sc.score_freshness.tolist() == [100, 100, 76, 0, 0]
    assert sc.score_risk.iloc[4] == 0 and sc.grade.iloc[4] is None


def test_grades():
    rows = frame(*[stock() for _ in range(4)])
    sig = apply_signals(rows, CFG)
    sc = score(rows, sig, 20, CFG)
    sc["stock_score"] = [85, 75, 65, 10]
    g = CFG.scoring.grades
    got = np.select([sc.stock_score >= g["A+"], sc.stock_score >= g["A"], sc.stock_score >= g["B"]],
                    ["A+", "A", "B"], "Watch")
    assert got.tolist() == ["A+", "A", "B", "Watch"]


def test_score_bounds_and_penalty():
    cfg = CFG.model_copy(deep=True)     # live config sets the points to 0; test the mechanism
    cfg.penalties.points = {"overextended": 10, "high_atr": 5, "late_stage": 10}
    rows = frame(stock(), stock(symbol="LATE", weeks_in_trend=25, price=140.0, atr_pct=9.0))
    sc = score(rows, apply_signals(rows, cfg), 20, cfg)
    assert sc.stock_score.between(0, 100).all()
    assert sc.penalty.iloc[1] == 25 and sc.stock_score.iloc[0] > sc.stock_score.iloc[1]


def test_trade_plan_sizing_cap_and_bear_factor():
    rows = frame(stock(price=110.0, st_value=100.0), stock(price=100.0, st_value=99.9),
                 stock(direction="Bearish"))
    tp = trade_plan(rows, capital=1_000_000, risk_pct=1.0, max_position_pct=25)
    assert tp.position_qty.iloc[0] == 1000            # 10,000 risk / 10 per share
    assert tp.position_qty.iloc[1] == 2500            # capped at 25% of capital
    assert tp.risk_pct.iloc[0] == pytest.approx(100 * 10 / 110)
    assert pd.isna(tp.stop_price.iloc[2]) and pd.isna(tp.position_qty.iloc[2])
    bear = trade_plan(rows, 1_000_000, 1.0, 25, size_factor=0.5)
    assert bear.position_qty.iloc[0] == 500


def test_reasons_text():
    rows = frame(stock(), stock(symbol="BAD", rs_rating=55.0, sector_tradeable=False))
    out = score_stocks(rows, 20, CFG, 1_000_000, 1.0, "bear")
    assert out.reasons.iloc[0].startswith("Fresh flip 3w, Near high 3.0%, Vol 2.0x, Stop 9.1%")
    assert "✗ Sector, RS 55" in out.reasons.iloc[1]
    assert out.reasons.iloc[0].endswith("Bear regime: reduce size")


def test_rs_rating():
    df = pd.DataFrame({"ret_3m": [10, 20, 30, 5, np.nan], "ret_6m": [10, 20, 30, 5, 1],
                       "ret_9m": [10, 20, 30, np.nan, 1], "ret_12m": [10, 20, 30, np.nan, 1]})
    raw, rating = rs_rating(df, CFG.relative_strength.weights)
    assert raw.iloc[3] == pytest.approx((0.4 * 5 + 0.2 * 5) / 0.6)   # renormalised weights
    assert np.isnan(raw.iloc[4]) and np.isnan(rating.iloc[4])        # ret_3m required
    assert rating.iloc[:4].tolist() == [50, 75, 99, 25]
    assert rating.dropna().between(1, 99).all()


def test_quadrants():
    q = quadrant(pd.Series([101, 101, 99, 99, np.nan]), pd.Series([101, 99, 99, 101, 100]))
    assert q.tolist() == ["Leading", "Weakening", "Lagging", "Improving", None]


def _bench(trend):
    idx = pd.bdate_range("2024-01-01", periods=300)
    return pd.Series(np.linspace(100, 100 + trend, 300), index=idx), idx[-1]


def test_regime_rules():
    up, t = _bench(50)
    assert market_regime(up, t, 60, 60, CFG.regime)["regime"] == "bull"
    assert market_regime(up, t, 40, 60, CFG.regime)["regime"] == "neutral"   # breadth fails
    down, t = _bench(-50)
    r = market_regime(down, t, 60, 30, CFG.regime)
    assert r["regime"] == "bear" and r["above_200dma"] is False and r["sma200_rising"] is False

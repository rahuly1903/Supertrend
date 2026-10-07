"""Backtest simulator on a hand-built market (no DB, no scanner)."""
import math

import numpy as np
import pandas as pd
import pytest

from backtest.engine import (Params, PricePanel, SignalSet, WeekSignal, curve_metrics, eligible_sectors,
                             simulate)

MONDAYS = pd.date_range("2026-01-05", periods=5, freq="W-MON")
FRIDAYS = MONDAYS + pd.Timedelta(days=4)
TRADEABLE = {"min_st_breadth": 60, "min_rs_trend": 1.0, "quadrants": ["Leading", "Improving"], "min_stocks": 1}
BEAR = {"direction": "Bearish", "f_supertrend": False}   # always together in real scans
GOOD = dict(weeks_in_trend=3, f_supertrend=True, f_trend_template=True, f_52w_range=True, f_liquidity=True, f_price=True,
            f_not_asm=True, rs_rating=90.0, direction="Bullish", st_value=90.0, price=100.0)


def sectors(rank_a=1, rank_b=2):
    return pd.DataFrame({"sector_rank": [rank_a, rank_b], "st_breadth": [80.0, 80.0], "rs_trend": [1.1, 1.1],
                         "rrg_quadrant": ["Leading", "Leading"], "stock_count": [5, 5]}, index=["A", "B"])


def stocks(per_symbol=None):
    rows = {1: {**GOOD, "symbol": "S1", "industry": "A", "stock_score": 90.0},
            2: {**GOOD, "symbol": "S2", "industry": "A", "stock_score": 80.0},
            3: {**GOOD, "symbol": "S3", "industry": "B", "stock_score": 70.0}}
    for sid, over in (per_symbol or {}).items():
        rows[sid] = {**rows[sid], **over}
    return pd.DataFrame.from_dict(rows, orient="index")


def signal_set(weeks):
    return SignalSet("test", (10, 3.0), "default",
                     [WeekSignal(FRIDAYS[i], MONDAYS[i], s, sec, reg) for i, (s, sec, reg) in enumerate(weeks)],
                     TRADEABLE)


def prices(open_=None, close=None, low=None):
    # every symbol opens at 100 + week, dips 0.5 below the open and closes at 101 + week unless overridden
    op = pd.DataFrame({sid: 100.0 + np.arange(5) for sid in (1, 2, 3)}, index=MONDAYS)
    cl = op + 1
    lo = op - 0.5
    for frame, over in ((op, open_), (cl, close), (lo, low)):
        for (w, sid), v in (over or {}).items():
            frame.loc[MONDAYS[w], sid] = v
    return PricePanel(op, cl, pd.Series(1000.0 + np.arange(5) * 10, index=MONDAYS), lo)


def P(**kw):
    base = dict(capital=100_000, max_positions=2, top_n_sectors=1, min_rs=70, sizing="equal",
                cost_round_trip_pct=0.2, slippage_pct=0.0, sector_exit_weeks=2, bear_mode="ignore",
                max_position_pct=100)
    return Params(**{**base, **kw})


def test_entry_at_next_open_exit_on_bearish_with_costs():
    wk = [(stocks(), sectors(), "bull")] * 2 + [(stocks({1: BEAR}), sectors(), "bull")] \
        + [(stocks(), sectors(), "bull")] * 2
    res = simulate(signal_set(wk), prices(), P(max_positions=1))
    t = res.trades.iloc[0]
    # signal week 0 -> filled at open of week 1 (101); bearish at week 2 -> exit at open of week 3 (103)
    assert (t.symbol, t.entry_price, t.exit_price, t.exit_reason) == ("S1", 101.0, 103.0, "supertrend")
    assert t.entry_date == FRIDAYS[1] and t.exit_date == FRIDAYS[3] and t.weeks_held == 2
    qty = math.floor(100_000 / (101 * 1.001))   # target 100k, capped by cash incl. entry cost
    assert t.qty == qty == 989
    assert t.pnl == pytest.approx(qty * (103 * 0.999 - 101 * 1.001))


def test_only_top_n_sectors_and_max_positions_by_score():
    res = simulate(signal_set([(stocks(), sectors(), "bull")] * 3), prices(), P(max_positions=2, top_n_sectors=1))
    first = res.trades[res.trades.entry_date == FRIDAYS[1]]
    assert sorted(first.symbol) == ["S1", "S2"]            # sector B (rank 2) excluded; best scores first
    assert res.equity.positions.max() <= 2


def test_filters_and_min_rs():
    wk = [(stocks({1: {"rs_rating": 60.0}, 2: {"f_trend_template": False}}), sectors(), "bull")] * 3
    res = simulate(signal_set(wk), prices(), P(top_n_sectors=2))
    assert set(res.trades.symbol) == {"S3"}


def test_sector_exit_after_consecutive_weeks_out():
    wk = [(stocks(), sectors(1, 2), "bull"),
          (stocks(), sectors(2, 1), "bull"),     # A out of top-1: streak 1
          (stocks(), sectors(2, 1), "bull"),     # streak 2 -> exit at next open
          (stocks(), sectors(2, 1), "bull"),
          (stocks(), sectors(2, 1), "bull")]
    res = simulate(signal_set(wk), prices(), P(max_positions=1, sector_exit=True))
    a = res.trades[res.trades.symbol == "S1"].iloc[0]
    assert a.exit_reason == "sector" and a.exit_date == FRIDAYS[3]


def test_supertrend_only_exit_ignores_sector_rotation():
    wk = [(stocks(), sectors(1, 2), "bull")] + [(stocks(), sectors(2, 1), "bull")] * 4
    res = simulate(signal_set(wk), prices(), P(max_positions=1, sector_exit=False))
    s1 = res.trades[res.trades.symbol == "S1"]
    assert len(s1) == 1 and s1.iloc[0].exit_reason == "end"        # held despite sector leaving top N


def test_hard_stop_fills_at_stop_or_gap_open():
    wk = [(stocks(), sectors(), "bull")] * 5
    # entered at 101 (week 1); 10% stop = 90.9; week 2 low 90 -> filled at the stop
    t = simulate(signal_set(wk), prices(low={(2, 1): 90.0}), P(max_positions=1, stop_pct=10)).trades.iloc[0]
    assert (t.exit_reason, t.exit_date, t.exit_price) == ("stop", FRIDAYS[2], pytest.approx(90.9))
    # week 2 gaps below the stop: filled at the open
    t = simulate(signal_set(wk), prices(open_={(2, 1): 85.0}, low={(2, 1): 84.0}),
                 P(max_positions=1, stop_pct=10)).trades.iloc[0]
    assert (t.exit_reason, t.exit_price) == ("stop", 85.0)


def test_trailing_stop_ratchets_from_highest_close():
    wk = [(stocks(), sectors(), "bull")] * 5
    # closes 102 (w1), 150 (w2) -> stop 150 * 0.9 = 135; week 3 low 130 -> stopped at 135
    res = simulate(signal_set(wk), prices(close={(2, 1): 150.0}, open_={(3, 1): 148.0}, low={(3, 1): 130.0}),
                   P(max_positions=1, trail_pct=10))
    t = res.trades.iloc[0]
    assert (t.exit_reason, t.exit_date, t.exit_price) == ("stop", FRIDAYS[3], 135.0)


def test_ma_exit_and_time_stop():
    wk = [(stocks(), sectors(), "bull")] * 5
    px = prices(close={(1, 1): 50.0})       # week-1 close far below its 2-week SMA
    t = simulate(signal_set(wk), px, P(max_positions=1, trail_ma_weeks=2)).trades.iloc[0]
    assert (t.exit_reason, t.exit_date) == ("ma", FRIDAYS[2])
    # held 2 weeks with gain <= 5% -> time stop at the next open
    t = simulate(signal_set(wk), prices(), P(max_positions=1, time_stop_weeks=2, time_stop_min_pct=5)).trades.iloc[0]
    assert (t.exit_reason, t.exit_date) == ("time", FRIDAYS[3])


def test_min_score_reweighting_and_no_sector_gate():
    parts = {f"score_{k}": 0.0 for k in ("rs", "sector", "freshness", "near_high", "volume", "tightness", "risk")}
    st = stocks({1: {**parts, "penalty": 0.0, "score_rs": 10.0}, 2: {**parts, "penalty": 0.0, "score_rs": 50.0},
                 3: {**parts, "penalty": 0.0, "score_rs": 90.0}})
    wk = [(st, sectors(), "bull")] * 3
    # scanner score: S1 90, S2 80, S3 70 -> min_score 75 keeps S1, S2 (sector B also gated out)
    assert set(simulate(signal_set(wk), prices(), P(min_score=75)).trades.symbol) == {"S1", "S2"}
    # re-weighted on RS only, no sector gate: S3 (90) then S2 (50) are the best two
    res = simulate(signal_set(wk), prices(), P(top_n_sectors=0, score_weights={"rs": 1.0}))
    assert sorted(res.trades.symbol) == ["S2", "S3"]


def test_fresh_flip_entry_and_tradeable_filters():
    wk = [(stocks({1: {"weeks_in_trend": 1}, 2: {"f_trend_template": False, "weeks_in_trend": 1}}),
           sectors(), "bull")] * 3
    res = simulate(signal_set(wk), prices(), P(entry_max_weeks_in_trend=1))
    assert set(res.trades.symbol) == {"S1"}                   # S2 fails trend template; S3 not a fresh flip
    res = simulate(signal_set(wk), prices(), P(entry_max_weeks_in_trend=1, entry_filters="tradeable"))
    assert set(res.trades.symbol) == {"S1", "S2"}


def test_bear_mode():
    wk = [(stocks(), sectors(), "bear")] * 3
    assert simulate(signal_set(wk), prices(), P(bear_mode="skip")).trades.empty
    half = simulate(signal_set(wk), prices(), P(bear_mode="half", max_positions=1)).trades.iloc[0]
    assert half.qty == math.floor(100_000 * 0.5 / 101)


def test_accounting_final_equity_equals_capital_plus_pnl():
    wk = [(stocks(), sectors(), "bull")] * 2 + [(stocks({1: BEAR, 2: BEAR}),
                                                 sectors(), "bull")] + [(stocks({1: BEAR, 2: BEAR}), sectors(), "bull")] * 2
    res = simulate(signal_set(wk), prices(), P())
    assert (res.trades.exit_reason == "supertrend").all()
    assert res.equity.equity.iloc[-1] == pytest.approx(100_000 + res.trades.pnl.sum())


def test_risk_sizing_uses_stop_distance():
    res = simulate(signal_set([(stocks(), sectors(), "bull")] * 3), prices(),
                   P(sizing="risk", risk_pct=1.0, max_positions=1))
    # risk 1,000 / (101 - 90) = 90 shares
    assert res.trades.iloc[0].qty == 90


def test_eligible_sectors_rules():
    s = sectors(1, 2)
    s.loc["B", "rrg_quadrant"] = "Weakening"
    assert eligible_sectors(s, 5, TRADEABLE) == {"A"}


def test_curve_metrics():
    dates = pd.Series(pd.to_datetime(["2024-01-01", "2025-01-01"]))
    m = curve_metrics(pd.Series([100.0, 121.0]), dates)
    assert m["cagr_pct"] == pytest.approx(21.0, abs=0.1) and m["max_drawdown_pct"] == 0
    m = curve_metrics(pd.Series([100.0, 150.0, 75.0, 90.0]), pd.Series(pd.date_range("2024-01-01", periods=4)))
    assert m["max_drawdown_pct"] == pytest.approx(-50.0)


def test_fixed_size_unlimited_positions_adds_capital():
    wk = [(stocks(), sectors(), "bull")] * 5
    p = P(capital=150_000, max_positions=0, top_n_sectors=0, sizing="fixed", position_size=50_000,
          add_capital=True, cost_round_trip_pct=0.0)
    res = simulate(signal_set(wk), prices(), p)
    first = res.trades[res.trades.entry_date == FRIDAYS[1]]
    assert sorted(first.symbol) == ["S1", "S2", "S3"]               # no position limit
    assert (first.qty == 50_000 // 101).all()                       # fixed rupees per stock
    # 3 x 495 x 101 = 149,985 fits in 150k: nothing added
    assert res.metrics.get("capital_added") is None
    p = P(**{**p.__dict__, "capital": 100_000})
    res = simulate(signal_set(wk), prices(), p)
    added = 3 * 495 * 101 - 100_000
    assert res.metrics["capital_added"] == pytest.approx(added)
    assert res.equity["contributed"].iloc[-1] == pytest.approx(100_000 + added)
    # time-weighted: week 1 = all three bought at open 101, close 102 -> nav return excludes the inflow
    eq = res.equity
    assert eq["nav"].iloc[1] / eq["nav"].iloc[0] == pytest.approx(eq["equity"].iloc[1] / (eq["equity"].iloc[0] + added))
    assert res.metrics["final_equity"] == pytest.approx(eq["equity"].iloc[-1])


def test_entry_window_weeks_in_trend():
    wk = [(stocks({1: {"weeks_in_trend": 4}, 2: {"weeks_in_trend": 3}, 3: {"weeks_in_trend": 1}}),
           sectors(), "bull")] * 3
    res = simulate(signal_set(wk), prices(), P(max_positions=0, top_n_sectors=0, entry_max_weeks_in_trend=3,
                                               entry_filters="tradeable", sizing="fixed", position_size=10_000))
    assert set(res.trades.symbol) == {"S2", "S3"}


def test_fixed_size_grows_with_equity():
    wk = [(stocks(), sectors(), "bull")] * 5
    p = P(capital=1_000_000, max_positions=0, top_n_sectors=0, sizing="fixed", position_size=10_000,
          position_pct=5, cost_round_trip_pct=0.0)
    first = simulate(signal_set(wk), prices(), p).trades
    assert (first.qty == 50_000 // 101).all()                    # 5% of 1M beats the 10k floor
    first = simulate(signal_set(wk), prices(), P(**{**p.__dict__, "position_pct": 0.5})).trades
    assert (first.qty == 10_000 // 101).all()                    # 0.5% = 5k: the floor wins


def test_added_capital_covers_only_the_floor():
    wk = [(stocks(), sectors(), "bull")] * 5
    # 100k capital, size = max(10k, 60% of equity) = 60k: S1 gets 60k, S2 the remaining 40k, S3 the 10k floor
    p = P(capital=100_000, max_positions=0, top_n_sectors=0, sizing="fixed", position_size=10_000,
          position_pct=60, add_capital=True, cost_round_trip_pct=0.0)
    res = simulate(signal_set(wk), prices(), p)
    first = res.trades[res.trades.entry_date == FRIDAYS[1]].set_index("symbol")
    assert first.qty["S1"] == 60_000 // 101
    assert first.qty["S3"] == 10_000 // 101
    assert res.metrics["capital_added"] < 10_000 + 101


def test_min_fill_skips_scrap_positions():
    wk = [(stocks(), sectors(), "bull")] * 5
    # 100k, 45% each: S1, S2 get 45k; 10k left covers 22% of S3's size -> skipped at min_fill 50
    p = P(capital=100_000, max_positions=0, top_n_sectors=0, sizing="fixed", position_size=0, position_pct=45,
          cost_round_trip_pct=0.0, min_fill_pct=50)
    first = simulate(signal_set(wk), prices(), p).trades
    assert set(first[first.entry_date == FRIDAYS[1]].symbol) == {"S1", "S2"}
    first = simulate(signal_set(wk), prices(), P(**{**p.__dict__, "min_fill_pct": 0})).trades
    assert set(first[first.entry_date == FRIDAYS[1]].symbol) == {"S1", "S2", "S3"}


def test_sector_cap_and_above_sma200():
    wk = [(stocks(), sectors(), "bull")] * 3
    base = dict(max_positions=0, top_n_sectors=0, sizing="fixed", position_size=10_000, entry_filters="tradeable")
    first = lambda r: set(r.trades[r.trades.entry_date == FRIDAYS[1]].symbol)  # noqa: E731
    assert first(simulate(signal_set(wk), prices(), P(**base, max_per_sector=1))) == {"S1", "S3"}   # S2 = 2nd in A
    wk = [(stocks({1: {"sma200": 120.0}, 2: {"sma200": 80.0}, 3: {"sma200": 80.0}}), sectors(), "bull")] * 3
    assert first(simulate(signal_set(wk), prices(), P(**base, above_sma200=True))) == {"S2", "S3"}  # S1 below


def test_trim_sells_winner_back_to_target():
    wk = [(stocks(), sectors(), "bull")] * 5
    # S1 doubles at week 1 close -> above 40% of equity -> trimmed to 25% at week 2's open
    p = P(capital=100_000, max_positions=1, top_n_sectors=0, sizing="fixed", position_size=0, position_pct=30,
          cost_round_trip_pct=0.0, trim_above_pct=40, trim_to_pct=25)
    res = simulate(signal_set(wk), prices(close={(1, 1): 300.0}), p)
    trim = res.trades[res.trades.exit_reason == "trim"]
    assert len(trim) == 1 and trim.iloc[0].exit_date == FRIDAYS[2]
    rest = res.trades[(res.trades.symbol == "S1") & (res.trades.exit_reason == "end")].iloc[0]
    eq1 = 100_000 - 297 * 101 + 297 * 300                     # equity at week 1 close
    assert rest.qty == math.floor(eq1 * 0.25 / 300)


def test_swap_replaces_weakest_holding():
    # week 0: only S3 qualifies -> bought with all cash. week 1: S3's score drops to 50, S1 (90) arrives
    w0 = stocks({1: {"weeks_in_trend": 5}, 2: {"weeks_in_trend": 5}, 3: {"weeks_in_trend": 1}})
    w1 = stocks({3: {"stock_score": 50.0, "weeks_in_trend": 2}, 2: {"weeks_in_trend": 5}})
    w1.loc[1, "weeks_in_trend"] = 1
    wk = [(w0, sectors(), "bull"), (w1, sectors(), "bull")] + [(w1, sectors(), "bull")] * 3
    p = P(capital=100_000, max_positions=0, top_n_sectors=0, sizing="fixed", position_size=0, position_pct=100,
          cost_round_trip_pct=0.0, entry_max_weeks_in_trend=1, entry_filters="tradeable",
          swap_min_score=80, swap_below_score=60, min_fill_pct=50)
    t = simulate(signal_set(wk), prices(), p).trades
    s3 = t[t.symbol == "S3"].iloc[0]
    assert s3.exit_reason == "swap" and s3.exit_date == FRIDAYS[2]
    assert t[t.symbol == "S1"].iloc[0].entry_date == FRIDAYS[2]


def test_exit_when_score_falls_below():
    wk = [(stocks(), sectors(), "bull"), (stocks({1: {"stock_score": 25.0}}), sectors(), "bull")] \
        + [(stocks({1: {"stock_score": 25.0}}), sectors(), "bull")] * 3
    t = simulate(signal_set(wk), prices(), P(max_positions=1, exit_score_below=30)).trades
    s1 = t[t.symbol == "S1"].iloc[0]
    assert (s1.exit_reason, s1.exit_date) == ("score", FRIDAYS[2])


def test_entry_ma_rules():
    ma = {"sma50": 95.0, "sma150": 90.0, "sma200": 85.0, "sma200_rising": True, "f_trend_template": True}
    wk = [(stocks({1: ma, 2: {**ma, "sma200_rising": False, "f_trend_template": False},
                   3: {**ma, "sma50": 80.0, "f_trend_template": False}}), sectors(), "bull")] * 3
    base = dict(max_positions=0, top_n_sectors=0, sizing="fixed", position_size=10_000, entry_filters="tradeable")
    first = lambda r: set(r.trades[r.trades.entry_date == FRIDAYS[1]].symbol)  # noqa: E731
    assert first(simulate(signal_set(wk), prices(), P(**base, entry_ma="template"))) == {"S1"}
    assert first(simulate(signal_set(wk), prices(), P(**base, entry_ma="stack"))) == {"S1", "S2"}
    assert first(simulate(signal_set(wk), prices(), P(**base, entry_ma="rising200"))) == {"S1", "S3"}


def test_take_profit_intraweek_and_close():
    wk = [(stocks(), sectors(), "bull")] * 5
    px = prices(close={(2, 1): 180.0, (3, 1): 230.0})
    # weekly highs: 160 in week 2 (+58% on the 101 entry), 210 in week 3 (+108%)
    px.high = px.close * 0 + 101.0
    px.high.loc[MONDAYS[2], 1] = 160.0
    px.high.loc[MONDAYS[3], 1] = 210.0
    base = dict(capital=100_000, max_positions=1, top_n_sectors=0, sizing="fixed", position_size=0, position_pct=100,
                cost_round_trip_pct=0.0, take_profit=((50, 0.4), (100, 0.4)))
    t = simulate(signal_set(wk), px, P(**base)).trades.set_index("exit_reason")
    qty = 100_000 // 101
    assert t.loc["tp1", "exit_price"] == pytest.approx(151.5) and t.loc["tp1", "qty"] == math.floor(qty * 0.4)
    rest = qty - math.floor(qty * 0.4)
    assert t.loc["tp2", "exit_price"] == pytest.approx(202.0) and t.loc["tp2", "qty"] == math.floor(rest * 0.4)
    assert t.loc["end", "qty"] == rest - math.floor(rest * 0.4)
    # close mode: week-2 close 180 >= 151.5 -> sell at week-3 open (103); week-3 close 230 >= 202 -> week-4 open
    t = simulate(signal_set(wk), px, P(**base, tp_fill="close")).trades.set_index("exit_reason")
    assert (t.loc["tp1", "exit_date"], t.loc["tp1", "exit_price"]) == (FRIDAYS[3], 103.0)
    assert t.loc["tp2", "exit_date"] == FRIDAYS[4]


def test_shuffle_seed_randomizes_which_signal_gets_the_slot():
    wk = [(stocks(), sectors(), "bull")] * 3
    picks = {seed: simulate(signal_set(wk), prices(), P(max_positions=1, top_n_sectors=0, shuffle_seed=seed))
             .trades.symbol.iloc[0] for seed in range(1, 21)}
    assert simulate(signal_set(wk), prices(), P(max_positions=1, top_n_sectors=0)).trades.symbol.iloc[0] == "S1"
    assert len(set(picks.values())) > 1                    # not always the best score
    again = simulate(signal_set(wk), prices(), P(max_positions=1, top_n_sectors=0, shuffle_seed=7))
    assert again.trades.symbol.iloc[0] == picks[7]         # reproducible per seed


def test_skip_pct_misses_signals():
    wk = [(stocks(), sectors(), "bull")] * 3
    res = simulate(signal_set(wk), prices(), P(max_positions=3, top_n_sectors=0, shuffle_seed=1, skip_pct=100))
    assert res.trades.empty


def test_tax_is_taken_from_final_equity():
    wk = [(stocks(), sectors(), "bull")] * 2 + [(stocks({1: BEAR}), sectors(), "bull")] \
        + [(stocks(), sectors(), "bull")] * 2
    plain = simulate(signal_set(wk), prices(), P(max_positions=1))
    taxed = simulate(signal_set(wk), prices(), P(max_positions=1, tax="current", tax_cess_pct=4))
    profit = plain.trades.pnl.sum()
    assert profit > 0
    assert taxed.metrics["tax_paid"] == pytest.approx(profit * 0.20 * 1.04)
    assert taxed.equity.equity.iloc[-1] == pytest.approx(plain.equity.equity.iloc[-1] - profit * 0.20 * 1.04)

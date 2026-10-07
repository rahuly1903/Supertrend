"""Walk-forward selection uses only training-window returns."""
import numpy as np
import pandas as pd
import pytest

from backtest.walkforward import RunRecord, folds, walk_forward

DATES = pd.date_range("2020-01-03", periods=157, freq="W-FRI")      # ~3 years of Fridays


def run(i, weekly_ret_before, weekly_ret_after, switch="2021-01-01"):
    r = np.where(DATES[1:] < pd.Timestamp(switch), weekly_ret_before, weekly_ret_after)
    eq = 100.0 * np.concatenate([[1.0], np.cumprod(1 + r)])
    trades = pd.DataFrame({"entry_date": DATES[::4], "pnl": 1.0, "qty": 10, "pnl_pct": 1.0, "weeks_held": 4,
                           "exit_reason": "supertrend"})
    return RunRecord(i, f"run{i}", pd.DataFrame({"date": DATES, "equity": eq, "invested_pct": 100.0,
                                                 "positions": 5, "benchmark": 100.0}), trades)


def test_folds_roll_forward():
    f = list(folds(pd.DatetimeIndex(DATES), 1, 1, anchored=False))
    assert [(a.year, b.year) for a, b, _ in f] == [(2020, 2021), (2021, 2022)]


def test_picks_from_training_window_only():
    # A wins in 2020 then loses; B the opposite. The 2021 test must trade A (chosen on 2020) and lose.
    a, b = run(0, 0.01, -0.01), run(1, -0.01, 0.01)
    res = walk_forward([a, b], 100.0, 1, 1, False, "cagr", 1, 0)
    first = res["folds"][0]
    assert first["chosen"][0]["name"] == "run0" and first["return_pct"] < 0
    # next fold's training year (2021) favours B
    assert res["folds"][1]["chosen"][0]["name"] == "run1"


def test_top_k_blends_returns():
    a, b = run(0, 0.02, 0.02), run(1, 0.01, 0.0)
    res = walk_forward([a, b], 100.0, 1, 1, False, "cagr", 2, 0)
    eq = res["equity"]["equity"].to_numpy()
    assert eq[1] / eq[0] - 1 == pytest.approx((0.02 + 0.0) / 2)

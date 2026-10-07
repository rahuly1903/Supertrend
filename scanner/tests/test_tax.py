"""Capital-gains tax book (backtest/tax.py) and its use in the simulator."""
import pandas as pd
import pytest

from backtest.tax import TaxBook, buy_and_hold_after_tax, fy_of, rates

T = pd.Timestamp


def test_financial_year_and_rates():
    assert fy_of(T("2025-03-31")) == 2024 and fy_of(T("2025-04-01")) == 2025
    assert rates("historical", 2016) == (15.0, 0.0, 0.0)
    assert rates("historical", 2019) == (15.0, 10.0, 100_000.0)
    assert rates("historical", 2024) == rates("current", 2016) == (20.0, 12.5, 125_000.0)


def test_short_and_long_term_with_exemption_and_cess():
    b = TaxBook("current", cess_pct=4)
    b.add(T("2025-05-01"), T("2025-09-01"), 100_000)      # short-term
    b.add(T("2024-04-01"), T("2025-10-01"), 225_000)      # long-term: 100k above the 1.25 L exemption
    assert b.settle() == pytest.approx((20_000 + 12_500) * 1.04)


def test_short_term_loss_offsets_long_term_gain_and_carries_forward():
    b = TaxBook("current", cess_pct=0)
    b.add(T("2025-05-01"), T("2025-09-01"), -400_000)
    b.add(T("2024-04-01"), T("2025-10-01"), 300_000)
    assert b.settle() == 0 and b.carry_st == pytest.approx(100_000)
    b.add(T("2026-05-01"), T("2026-09-01"), 150_000)      # next year: 100k loss brought forward
    assert b.settle() == pytest.approx(50_000 * 0.20)


def test_long_term_loss_does_not_offset_short_term_gain():
    b = TaxBook("current", cess_pct=0)
    b.add(T("2024-04-01"), T("2025-10-01"), -100_000)
    b.add(T("2025-05-01"), T("2025-09-01"), 100_000)
    assert b.settle() == pytest.approx(20_000) and b.carry_lt == pytest.approx(100_000)


def test_settle_only_finished_years():
    b = TaxBook("current", cess_pct=0)
    b.add(T("2024-05-01"), T("2024-09-01"), 10_000)
    b.add(T("2025-05-01"), T("2025-09-01"), 10_000)
    assert b.settle(before_fy=2025) == pytest.approx(2_000)
    assert list(b.realized) == [2025]


def test_buy_and_hold_after_tax():
    assert buy_and_hold_after_tax(1_000_000, 2_125_000, cess_pct=0) == pytest.approx(2_125_000 - 1_000_000 * 0.125)

"""End-to-end scan on a synthetic universe (no DB)."""
import numpy as np
import pandas as pd
import pytest

from config import load_config
from engine.indicators import build_daily_panels, supertrend_asof
from engine.scan import ScanInputs, Scanner
from engine.supertrend import compute_supertrend
from engine.weekly import resample_weekly

CFG = load_config()
N_DAYS = 500


def synthetic_inputs(n_symbols=12, seed=0, new_listing=True) -> ScanInputs:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=N_DAYS)
    rows = []
    for sid in range(1, n_symbols + 1):
        drift = rng.normal(0.0005, 0.001)
        close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.02, N_DAYS)))
        start = 350 if (new_listing and sid == n_symbols) else 0   # last symbol: new listing
        d = pd.DataFrame({"symbol_id": sid, "date": dates, "close": close})
        d["open"] = d["close"].shift(1).fillna(d["close"])
        d["high"] = d[["open", "close"]].max(axis=1) * 1.01
        d["low"] = d[["open", "close"]].min(axis=1) * 0.99
        d["volume"] = rng.integers(100_000, 1_000_000, N_DAYS).astype(float)
        d["delivery_pct"] = np.nan
        rows.append(d.iloc[start:])
    daily = pd.concat(rows, ignore_index=True)
    bench_close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, N_DAYS)))
    bench = pd.concat([pd.DataFrame({"index_name": n, "date": dates, "close": bench_close})
                       for n in ("NIFTY500", "NIFTY50")])

    wk = resample_weekly(daily)
    series, _ = compute_supertrend(wk, 10, 3.0)
    symbols = pd.DataFrame({"id": range(1, n_symbols + 1),
                            "symbol": [f"S{i}" for i in range(1, n_symbols + 1)],
                            "name": [f"Stock {i}" for i in range(1, n_symbols + 1)],
                            "industry": ["A", "B", "C"] * (n_symbols // 3),
                            "shares_outstanding": 1e8})
    return ScanInputs(
        daily=daily, weekly=wk[["symbol_id", "week_end_date", "high", "low", "close", "volume"]],
        st_series=series[["symbol_id", "week_end_date", "st_value", "direction"]], bench=bench,
        symbols=symbols, index_lists=pd.Series("Nifty 100", index=symbols["id"]),
        exclusions=pd.DataFrame({"as_of_date": [dates[-1]], "symbol_id": [2]}))


@pytest.fixture(scope="module")
def inputs():
    return synthetic_inputs()


@pytest.fixture(scope="module")
def scanner(inputs):
    return Scanner(inputs, CFG, 1_000_000, 1.0)


def test_scan_shapes(scanner):
    week = scanner.scan_weeks()[-1]
    res = scanner.run(week)
    assert len(res.stocks) == 12 and len(res.sectors) == 3
    assert set(res.sectors.sector_rank) == {1, 2, 3}
    assert res.stocks.stock_score.between(0, 100).all()
    assert res.regime["regime"] in ("bull", "neutral", "bear")
    assert res.stocks.loc[res.stocks.symbol_id == 2, "in_asm_gsm"].item()
    bull = res.stocks.direction == "Bullish"
    assert res.stocks.grade[bull].notna().all() and res.stocks.grade[~bull].isna().all()


def test_new_listing_has_null_long_smas(scanner):
    res = scanner.run(scanner.scan_weeks()[-1])
    new = res.stocks[res.stocks.symbol_id == 12].iloc[0]   # 150 sessions of history
    assert pd.isna(new.sma200) and pd.notna(new.sma100)
    assert not new.f_trend_template and pd.isna(new.ret_12m)
    assert new.history_days == N_DAYS - 350


def test_asof_supertrend_matches_state(inputs, scanner):
    wk = resample_weekly(inputs.daily)
    _, state = compute_supertrend(wk, 10, 3.0)
    last = scanner.scan_weeks()[-1]
    got = supertrend_asof(scanner.st_series, last)
    exp = state.set_index("symbol_id")
    assert (got.direction == exp.direction.reindex(got.index)).all()
    assert (got.weeks_in_trend == exp.weeks_in_trend.reindex(got.index)).all()
    pd.testing.assert_series_equal(got.flip_date, exp.flip_date.reindex(got.index), check_names=False,
                                   check_dtype=False)


def test_no_lookahead(inputs, scanner):
    """Scanning week W with all data == scanning with data truncated at W."""
    week = scanner.scan_weeks()[-15]
    cut = ScanInputs(
        daily=inputs.daily[inputs.daily.date <= week],
        weekly=inputs.weekly[inputs.weekly.week_end_date <= week],
        st_series=inputs.st_series[inputs.st_series.week_end_date <= week],
        bench=inputs.bench[inputs.bench.date <= week],
        symbols=inputs.symbols, index_lists=inputs.index_lists, exclusions=inputs.exclusions)
    a = scanner.run(week)
    b = Scanner(cut, CFG, 1_000_000, 1.0).run(week)
    num = a.stocks.select_dtypes("number").columns.drop("symbol_id")
    pd.testing.assert_frame_equal(a.stocks.set_index("symbol_id")[num].sort_index(),
                                  b.stocks.set_index("symbol_id")[num].sort_index(), check_like=True)
    pd.testing.assert_frame_equal(a.sectors.select_dtypes("number"), b.sectors.select_dtypes("number"))
    assert a.regime == b.regime


def test_daily_panels_values():
    dates = pd.bdate_range("2024-01-01", periods=260)
    close = np.arange(1, 261, dtype=float)
    daily = pd.DataFrame({"symbol_id": 1, "date": dates, "open": close, "high": close + 1, "low": close - 0.5,
                          "close": close, "volume": 10.0, "delivery_pct": 50.0})
    p = build_daily_panels(daily, dates).asof(dates[-1]).iloc[0]
    assert p.sma50 == pytest.approx(np.arange(211, 261).mean())
    assert p.sma200 == pytest.approx(np.arange(61, 261).mean())
    assert p.ret_1m == pytest.approx((260 / 239 - 1) * 100)
    assert p.high_52w == 261 and p.low_52w == 260 - 252 + 1 - 0.5   # 252-session window
    assert p.history_days == 260

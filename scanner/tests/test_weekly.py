import datetime as dt

import numpy as np
import pandas as pd

from engine.weekly import diff_weekly, resample_weekly, week_monday


def _daily(dates, symbol_id=1, base=100.0):
    dates = pd.to_datetime(dates)
    n = len(dates)
    close = base + np.arange(n, dtype=float)
    return pd.DataFrame({"symbol_id": symbol_id, "date": dates, "open": close - 0.5,
                         "high": close + 1, "low": close - 1, "close": close,
                         "volume": np.full(n, 10.0)})


def test_week_monday():
    d = pd.to_datetime(["2026-09-21", "2026-09-25", "2026-09-26", "2026-09-27", "2026-09-28"])
    assert [str(x) for x in week_monday(d)] == ["2026-09-21"] * 4 + ["2026-09-28"]


def test_basic_ohlcv_aggregation():
    w = resample_weekly(_daily(pd.bdate_range("2026-09-14", "2026-09-25")))
    assert len(w) == 2
    first = w.iloc[0]
    assert first.week_start_date == pd.Timestamp("2026-09-14")
    assert first.week_end_date == pd.Timestamp("2026-09-18")
    assert (first.open, first.high, first.low, first.close) == (99.5, 105.0, 99.0, 104.0)
    assert first.volume == 50 and first.trading_days == 5


def test_holiday_friday_week_ends_thursday():
    # 2026-10-02 (Fri) Gandhi Jayanti holiday
    w = resample_weekly(_daily(["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"]))
    assert w.iloc[0].week_end_date == pd.Timestamp("2026-10-01") and w.iloc[0].trading_days == 4


def test_saturday_special_session_stays_in_its_week():
    # Budget day Saturday 2025-02-01 belongs to the week of Mon 2025-01-27 (TradingView)
    w = resample_weekly(_daily(["2025-01-30", "2025-01-31", "2025-02-01", "2025-02-03"]))
    assert w.week_end_date.tolist() == [pd.Timestamp("2025-02-01"), pd.Timestamp("2025-02-03")]
    assert w.iloc[0].trading_days == 3


def test_incomplete_week_excluded():
    daily = _daily(pd.bdate_range("2026-09-21", "2026-09-30"))  # Wed 30th: current week partial
    assert len(resample_weekly(daily, cutoff=dt.date(2026, 9, 30))) == 1
    # Friday holiday: week is complete once Friday has passed, even with no Friday candle
    hol = _daily(["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"])
    assert len(resample_weekly(hol, cutoff=dt.date(2026, 10, 1))) == 0
    assert len(resample_weekly(hol, cutoff=dt.date(2026, 10, 2))) == 1


def test_multi_symbol():
    d = pd.concat([_daily(pd.bdate_range("2026-09-14", "2026-09-25"), 1),
                   _daily(pd.bdate_range("2026-09-21", "2026-09-25"), 2, 50)])
    w = resample_weekly(d)
    assert w.groupby("symbol_id").size().to_dict() == {1: 2, 2: 1}


def test_diff_weekly_detects_changes():
    old = resample_weekly(_daily(pd.bdate_range("2026-09-07", "2026-09-25")))
    new = old.copy()
    assert diff_weekly(new, old).empty
    new.loc[1, "close"] += 1  # week 2 changed
    new = pd.concat([new, new.iloc[[2]].assign(monday=np.datetime64("2026-09-28"),
                                                week_end_date=pd.Timestamp("2026-10-02"))])
    d = diff_weekly(new, old)
    assert d.to_dict("records") == [{"symbol_id": 1, "first_changed": pd.Timestamp("2026-09-18")}]


def test_diff_weekly_week_end_moves():
    # Friday candle repaired later: week_end_date moves Thu -> Fri
    old = resample_weekly(_daily(["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"]))
    new = resample_weekly(_daily(["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]))
    assert diff_weekly(new, old).iloc[0].first_changed == pd.Timestamp("2026-09-25")


def test_plan_supertrend_routes_symbols():
    from jobs.weekly_scan import plan_supertrend

    ts = pd.Timestamp
    state = pd.DataFrame({
        "symbol_id": [1, 2, 3, 4, 5],
        "week_end_date": [ts("2026-09-18")] * 5,
        "atr_period": [10, 10, 10, 7, 10],
        "multiplier": [3.0, 3.0, 3.0, 3.0, 3.0],
        "atr": [1.0, 1.0, 1.0, 1.0, np.nan],
    })
    latest = pd.DataFrame({"symbol_id": [1, 2, 3, 4, 5, 6],
                           "latest_week": [ts("2026-09-25"), ts("2026-09-18"), ts("2026-09-25"),
                                           ts("2026-09-25"), ts("2026-09-25"), ts("2026-09-25")]})
    changed = pd.DataFrame({"symbol_id": [1, 3], "first_changed": [ts("2026-09-25"), ts("2026-09-11")]})
    full, inc = plan_supertrend(state, latest, changed, 10, 3.0, full=False)
    # 1: new week -> incremental; 2: nothing; 3: history rewrite -> full
    # 4: params changed -> full; 5: state still in warm-up -> full; 6: no state -> full
    assert sorted(full) == [3, 4, 5, 6] and inc == [1]
    full, inc = plan_supertrend(state, latest, changed, 10, 3.0, full=True)
    assert sorted(full) == [1, 2, 3, 4, 5, 6] and inc == []

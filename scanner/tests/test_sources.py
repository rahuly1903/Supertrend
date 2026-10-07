import datetime as dt

import numpy as np
import pandas as pd
import pytest

from config import load_config
from jobs.backfill import plan_downloads, plan_extend
from jobs.universe import map_symbol_ids, plan_symbol_changes
from sources.http import SourceError
from sources.nse_lists import parse_asm_gsm, parse_constituents
from sources.validate import detect_gaps, trading_calendar, validate_daily
from sources.yfinance_src import to_long


def test_config_loads():
    cfg = load_config()
    assert cfg.universe.universe_def.file == "ind_niftytotalmarket_list.csv"
    assert cfg.supertrend.atr_period == 7


CSV = """Company Name,Industry,Symbol,Series,ISIN Code
Reliance Industries Ltd.,Oil Gas & Consumable Fuels,RELIANCE,EQ,INE002A01018
 Tata Consultancy Services Ltd. ,Information Technology, TCS ,EQ,INE467B01029
"""


def test_parse_constituents():
    df = parse_constituents(CSV)
    assert df.columns.tolist() == ["symbol", "name", "industry", "series", "isin"]
    assert df.loc[1, "symbol"] == "TCS" and df.loc[1, "name"] == "Tata Consultancy Services Ltd."


def test_parse_constituents_rejects_html():
    with pytest.raises(SourceError):
        parse_constituents("<!DOCTYPE html><html>...</html>")


def test_parse_asm_gsm():
    asm = {"longterm": {"data": [{"symbol": "AAA", "isin": "I1", "asmSurvIndicator": "Stage I"}]},
           "shortterm": {"data": [{"symbol": "BBB", "isin": "I2", "asmSurvIndicator": "Stage II"}]}}
    gsm = [{"symbol": "CCC", "isin": "I3", "gsmStage": "0"}]
    df = parse_asm_gsm(asm, gsm)
    assert df.values.tolist() == [["ASM", "AAA", "I1", "LT Stage I"], ["ASM", "BBB", "I2", "ST Stage II"],
                                  ["GSM", "CCC", "I3", "0"]]


def test_plan_symbol_changes_rename_and_collision():
    existing = pd.DataFrame({"id": [1, 2], "symbol": ["OLDNAME", "XYZ"], "isin": ["I1", "I2"]})
    # I1 renamed OLDNAME -> NEWNAME; ticker XYZ now belongs to a new ISIN I3
    listing = pd.DataFrame({"symbol": ["NEWNAME", "XYZ"], "isin": ["I1", "I3"]})
    renames, collisions = plan_symbol_changes(listing, existing)
    assert renames.values.tolist() == [[1, "OLDNAME", "NEWNAME"]]
    assert collisions == [2]


def test_map_symbol_ids_isin_then_symbol():
    symbols = pd.DataFrame({"id": [1, 2], "symbol": ["A", "B"], "isin": ["I1", "I2"]})
    df = pd.DataFrame({"symbol": ["A2", "B", "C"], "isin": ["I1", "IX", None]})
    assert map_symbol_ids(df, symbols).tolist()[:2] == [1, 2]
    assert pd.isna(map_symbol_ids(df, symbols).iloc[2])


def _candles(**over):
    base = {"symbol": ["A"] * 3, "date": pd.to_datetime(["2026-09-23", "2026-09-24", "2026-09-25"]),
            "open": [10.0, 11, 12], "high": [11.0, 12, 13], "low": [9.0, 10, 11],
            "close": [10.5, 11.5, 12.5], "volume": [100.0, 200, 300]}
    base.update(over)
    return pd.DataFrame(base)


def test_validate_drops_bad_prices():
    clean, issues = validate_daily(_candles(close=[10.5, -1, np.nan]))
    assert len(clean) == 1
    assert (issues["issue"] == "bad_price").sum() == 2


def test_validate_clamps_high_low():
    clean, issues = validate_daily(_candles(high=[10.0, 12, 13]))  # high < close on day 1
    assert clean.loc[0, "high"] == 10.5
    assert issues["issue"].tolist() == ["ohlc_inconsistent"]


def test_validate_small_ohlc_noise_not_logged():
    clean, issues = validate_daily(_candles(high=[10.49, 12, 13]))
    assert clean.loc[0, "high"] == 10.5 and issues.empty


def test_gap_detection():
    days = pd.to_datetime(["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"])
    df = pd.DataFrame({"symbol": ["A"] * 4 + ["B"] * 4 + ["C"] * 3,
                       "date": list(days) * 2 + [days[0], days[1], days[3]]})
    cal = trading_calendar(df)
    assert len(cal) == 4
    gaps = detect_gaps(df, cal)
    assert gaps["symbol"].tolist() == ["C"]
    assert gaps.loc[0, "details"] == {"count": 1, "dates": ["2026-09-23"]}


def test_plan_downloads_incremental():
    end = dt.date(2026, 9, 25)
    state = pd.DataFrame({"symbol": ["NEW", "OLD", "DONE"],
                          "last_date": [None, dt.date(2026, 9, 22), end]})
    plan = plan_downloads(state, dt.date(2023, 9, 25), end)
    assert plan == {(dt.date(2023, 9, 25), end): ["NEW"], (dt.date(2026, 9, 23), end): ["OLD"]}


def test_plan_extend_backwards():
    start = dt.date(2014, 9, 25)
    state = pd.DataFrame({"symbol": ["A", "B", "LISTED", "EMPTY", "COVERED"],
                          "first_date": [dt.date(2023, 9, 29), dt.date(2023, 9, 29), dt.date(2025, 1, 6), None,
                                         dt.date(2014, 9, 25)],
                          "listed_date": [None, None, dt.date(2025, 1, 6), None, None]})
    assert plan_extend(state, start) == {(start, dt.date(2023, 9, 28)): ["A", "B"]}


def test_to_long_multiindex():
    idx = pd.to_datetime(["2026-09-24", "2026-09-25"])
    cols = pd.MultiIndex.from_product([["A.NS", "B.NS"], ["Open", "High", "Low", "Close", "Volume"]])
    raw = pd.DataFrame(np.arange(20, dtype=float).reshape(2, 10) + 1, index=idx, columns=cols)
    raw.loc[idx[0], ("B.NS", "Close")] = np.nan
    long = to_long(raw, ["A.NS", "B.NS"])
    assert len(long) == 3 and set(long["ticker"]) == {"A.NS", "B.NS"}
    assert long.columns.tolist() == ["ticker", "date", "open", "high", "low", "close", "volume"]


def test_validate_drops_phantom_holiday_candles():
    df = _candles(open=[10.0, 11.5, 12], high=[11.0, 11.5, 13], low=[9.0, 11.5, 11], volume=[100.0, 0, 300])
    clean, issues = validate_daily(df)
    assert clean["date"].dt.day.tolist() == [23, 25]
    assert issues.loc[0, "issue"] == "phantom_candle" and issues.loc[0, "details"]["count"] == 1


def test_validate_keeps_zero_volume_with_range():
    clean, _ = validate_daily(_candles(volume=[100.0, 0, 300]))
    assert len(clean) == 3

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from jobs.daily_ingest import plan_rows
from sources.bhavcopy import parse
from sources.http import SourceError

BHAV = """SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
RELIANCE, EQ, 25-Sep-2026, 1219.20, 1210.50, 1227.40, 1210.50, 1226.00, 1226.00, 1220.44, 13138735, 160350.76, 212698, 8311348, 63.26
3IINFOLTD, BE, 25-Sep-2026, 24.96, 24.94, 25.50, 23.90, 24.40, 24.35, 24.38, 233223, 56.85, 818, -, -
DUAL, BE, 25-Sep-2026, 10, 10, 11, 9, 10.5, 10.5, 10.2, 100, 1, 1, 50, 50.00
DUAL, EQ, 25-Sep-2026, 10, 10, 11, 9, 10.4, 10.4, 10.2, 200, 1, 1, 60, 30.00
GOLDBEES, ETF, 25-Sep-2026, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1
"""


def test_parse_bhavcopy():
    df = parse(BHAV, ["EQ", "BE", "BZ"])
    assert df["symbol"].tolist() == ["3IINFOLTD", "DUAL", "RELIANCE"]      # ETF series dropped
    r = df.set_index("symbol").loc["RELIANCE"]
    assert (r.prev_close, r.close, r.volume, r.delivery_pct) == (1219.2, 1226.0, 13138735, 63.26)
    assert r.date == pd.Timestamp("2026-09-25")
    assert np.isnan(df.set_index("symbol").loc["3IINFOLTD", "delivery_pct"])  # '-' -> NaN
    assert df.set_index("symbol").loc["DUAL", "series"] == "EQ"               # EQ preferred


def test_parse_rejects_html():
    with pytest.raises(SourceError):
        parse("<!DOCTYPE html>", ["EQ"])


D = dt.date(2026, 9, 25)
PREV = dt.date(2026, 9, 24)


def _bhav(**over):
    base = dict(symbol_id=[1, 2, 3, 4, 5, 6], open=100.0, high=110.0, low=90.0, close=105.0,
                prev_close=[100.0, 100.0, 50.0, 100.0, 100.0, 100.0],
                volume=1000.0, delivery_qty=500.0, delivery_pct=50.0)
    base.update(over)
    return pd.DataFrame(base)


def _state():
    return pd.DataFrame({
        "symbol_id":     [1, 2, 3, 4, 5, 6],
        "prev_close_db": [100.0, 100.0, 100.0, 98.0, 100.0, None],
        "prev_date_db":  [PREV, PREV, PREV, PREV, dt.date(2026, 9, 10), None],
        "exists_d":      [False, True, False, False, False, False],
        "has_after":     [False, False, False, True, False, False],
        "has_any":       [True, True, True, True, True, False],
    })


def test_plan_rows_routes_each_case():
    p = plan_rows(_bhav(), _state(), D, PREV, 0.03)
    # 1: plain append; 3: append + split detected (stored 100 vs NSE-adjusted prev 50)
    # 5: append after a gap -> no CA check (not contiguous); 6: no history -> skipped
    assert sorted(p["append"].symbol_id) == [1, 3, 5]
    assert p["delivery"].symbol_id.tolist() == [2]
    assert p["corporate_actions"].symbol_id.tolist() == [3]
    assert p["corporate_actions"].ratio.iloc[0] == 2.0
    # 4: gap repair, scaled by 98/100 onto adjusted history
    rep = p["repair"].iloc[0]
    assert rep.symbol_id == 4 and rep.close == pytest.approx(105 * 0.98)
    assert rep.volume == pytest.approx(1000 / 0.98) and rep.source == "bhav"
    assert (p["append"].close == 105.0).all()  # appends stay raw


def test_repair_skipped_without_contiguous_prev():
    st = _state()
    st.loc[st.symbol_id == 4, "prev_date_db"] = dt.date(2026, 9, 1)
    p = plan_rows(_bhav(), st, D, PREV, 0.03)
    assert p["repair"].empty and p["repair_skipped"].symbol_id.tolist() == [4]


def test_dividend_sized_ratio_is_not_a_corporate_action():
    p = plan_rows(_bhav(prev_close=[99.0] * 6), _state(), D, PREV, 0.03)
    assert p["corporate_actions"].empty

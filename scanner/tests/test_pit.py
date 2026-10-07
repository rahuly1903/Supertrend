"""Point-in-time universe build (jobs/pit_universe.py) and the scanner's membership mask."""
import pandas as pd
import pytest

from engine.scan import member_mask
from jobs.pit_universe import adjust, link_entities, map_events, rebalance_schedule, snapshots


def _rows(items):
    return pd.DataFrame(items, columns=["date", "symbol", "isin"]).assign(date=lambda d: pd.to_datetime(d["date"]))


def test_rename_keeps_isin_split_keeps_symbol():
    raw = _rows([
        ("2020-01-01", "OLDNAME", "INE000A01011"),
        ("2020-01-02", "NEWNAME", "INE000A01011"),   # rename: same ISIN
        ("2020-01-03", "NEWNAME", "INE000A01029"),   # face-value split: new ISIN, same symbol
        ("2020-01-01", "OTHER", "INE999Z01010"),
    ])
    e = link_entities(raw)
    assert e[0] == e[1] == e[2]
    assert e[3] != e[0]


def test_reused_symbol_after_long_gap_is_a_new_company():
    raw = _rows([("2015-01-01", "ABC", "INE111A01010"), ("2015-01-02", "ABC", "INE111A01010"),
                 ("2019-06-01", "ABC", "INE222B01010")])
    e = link_entities(raw)
    assert e[0] == e[1] != e[2]


def _split_frame():
    # 1:2 split on day 3: close 200 -> opens 101
    return pd.DataFrame({
        "entity": 0, "date": pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-06"]),
        "open": [198.0, 200, 101, 102], "high": [202.0, 204, 103, 104], "low": [196.0, 198, 99, 100],
        "close": [200.0, 200, 102, 103], "volume": [10.0, 10, 20, 20],
    })


def test_adjust_back_adjusts_a_split():
    ev = pd.DataFrame({"entity": [0], "date": pd.to_datetime(["2020-01-03"]), "factor": [0.5]})
    out, ca = adjust(_split_frame(), ev)
    assert out["close"].tolist() == pytest.approx([100, 100, 102, 103])
    assert out["volume"].tolist() == pytest.approx([20, 20, 20, 20])
    assert ca["factor"].tolist() == pytest.approx([0.5])


def test_adjust_skips_an_event_the_prices_do_not_show():
    ev = pd.DataFrame({"entity": [0], "date": pd.to_datetime(["2020-01-02"]), "factor": [0.5]})  # wrong date
    out, ca = adjust(_split_frame(), ev)
    assert ca.empty and out["close"].tolist() == [200, 200, 102, 103]


def test_map_events_by_isin_then_symbol_to_next_session():
    raw = pd.DataFrame({"entity": [0, 0, 1], "symbol": ["AAA", "AAA", "BBB"],
                        "isin": ["INE000A01011", "INE000A01011", None],
                        "date": pd.to_datetime(["2020-01-03", "2020-01-06", "2020-01-06"])})
    events = pd.DataFrame({"symbol": ["XXX", "BBB"], "isin": ["INE000A01011", "INE999"],
                           "ex_date": pd.to_datetime(["2020-01-04", "2020-01-06"]), "factor": [0.5, 0.2]})
    m = map_events(events, raw).sort_values("entity")
    assert m["entity"].tolist() == [0, 1]
    assert m["date"].tolist() == list(pd.to_datetime(["2020-01-06", "2020-01-06"]))


def test_schedule_uses_only_data_before_the_effective_date():
    sessions = pd.bdate_range("2013-01-01", "2015-12-31")
    sched = rebalance_schedule(sessions)
    for eff, w0, w1 in sched:
        assert w1 < eff and (w1 - w0).days > 170
        assert eff.month in (3, 9)
    assert sched[0][0].year == 2013 and sched[0][0].month == 9


def test_snapshots_rank_by_traded_value_and_skip_etfs_and_thin_trading():
    dates = pd.bdate_range("2019-01-01", "2020-03-31")
    rows = []
    for d in dates:
        rows += [(0, d, 5e7, "INE000A01011"), (1, d, 9e7, "INF000A01011"),  # ETF: excluded
                 (2, d, 1e7, "INE000B01011")]
        if d.day < 10:
            rows.append((3, d, 1e9, "INE000C01011"))                        # trades < 80% of days
    df = pd.DataFrame(rows, columns=["entity", "date", "value", "isin"])
    snap = snapshots(df, top_n=1, min_traded_pct=80)
    assert set(snap["entity"]) == {0}


def test_member_mask_follows_latest_snapshot():
    members = pd.DataFrame({"snapshot_date": pd.to_datetime(["2020-03-31", "2020-03-31", "2020-09-30"]),
                            "symbol_id": [1, 2, 2]})
    idx = pd.to_datetime(["2020-01-06", "2020-04-06", "2020-10-05"])
    m = member_mask(members, idx, [1, 2, 3])
    assert m.loc["2020-01-06"].tolist() == [False, False, False]
    assert m.loc["2020-04-06"].tolist() == [True, True, False]
    assert m.loc["2020-10-05"].tolist() == [False, True, False]

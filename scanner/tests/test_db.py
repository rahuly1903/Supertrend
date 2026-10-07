import datetime as dt

import numpy as np
import pandas as pd

import db


def _make_table(conn):
    conn.execute(
        """CREATE TEMP TABLE t_upsert (
               k integer, d date, v double precision, n bigint, b boolean, s text, j jsonb,
               PRIMARY KEY (k, d))"""
    )
    db._column_types.pop("t_upsert", None)


def _rows(conn):
    return conn.execute("SELECT k, d, v, n, b, s, j FROM t_upsert ORDER BY k").fetchall()


def test_copy_handles_nulls_ints_bools_json(pg_conn):
    _make_table(pg_conn)
    df = pd.DataFrame(
        {
            "k": [1, 2],
            "d": [dt.date(2026, 9, 25), pd.Timestamp("2026-09-26")],
            "v": [1.5, np.inf],               # inf -> NULL
            "n": [10.0, np.nan],              # float w/ NaN into bigint
            "b": [True, None],
            "s": ["a,b \"quoted\"", ""],     # CSV escaping, empty string != NULL
            "j": [{"x": 1}, None],
        }
    )
    assert db.copy_frame(pg_conn, "t_upsert", df) == 2
    r = _rows(pg_conn)
    assert r[0] == (1, dt.date(2026, 9, 25), 1.5, 10, True, 'a,b "quoted"', {"x": 1})
    assert r[1] == (2, dt.date(2026, 9, 26), None, None, None, "", None)


def test_upsert_updates_and_dedupes(pg_conn):
    _make_table(pg_conn)
    d = dt.date(2026, 9, 25)
    db.upsert_frame(pg_conn, "t_upsert", pd.DataFrame({"k": [1], "d": [d], "v": [1.0]}), ["k", "d"])
    df = pd.DataFrame({"k": [1, 1, 2], "d": [d, d, d], "v": [2.0, 3.0, 4.0]})
    db.upsert_frame(pg_conn, "t_upsert", df, ["k", "d"])
    assert [(r[0], r[2]) for r in _rows(pg_conn)] == [(1, 3.0), (2, 4.0)]


def test_upsert_do_nothing(pg_conn):
    _make_table(pg_conn)
    d = dt.date(2026, 9, 25)
    db.upsert_frame(pg_conn, "t_upsert", pd.DataFrame({"k": [1], "d": [d], "v": [1.0]}), ["k", "d"])
    db.upsert_frame(pg_conn, "t_upsert", pd.DataFrame({"k": [1], "d": [d], "v": [9.0]}), ["k", "d"], update_cols=[])
    assert _rows(pg_conn)[0][2] == 1.0


def test_replace_rows(pg_conn):
    _make_table(pg_conn)
    d = dt.date(2026, 9, 25)
    db.copy_frame(pg_conn, "t_upsert", pd.DataFrame({"k": [1, 1], "d": [d, d + dt.timedelta(1)], "v": [1.0, 2.0]}))
    db.replace_rows(pg_conn, "t_upsert", pd.DataFrame({"k": [1], "d": [d], "v": [5.0]}), {"k": 1})
    assert [(r[1], r[2]) for r in _rows(pg_conn)] == [(d, 5.0)]

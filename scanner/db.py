"""Postgres access: connection pool, bulk COPY / upsert helpers, frame reads.

All writes go through COPY. Upserts COPY into a temp table, then do one
INSERT ... SELECT ... ON CONFLICT DO UPDATE. Never row-by-row.
"""
from __future__ import annotations

import io
import json
import logging
import time
from contextlib import contextmanager
from typing import Iterable, Iterator, Sequence

import numpy as np
import pandas as pd
import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool

from settings import get_settings

log = logging.getLogger(__name__)

_pool: ConnectionPool | None = None
_column_types: dict[str, dict[str, str]] = {}

_INT_TYPES = {"smallint", "integer", "bigint"}
_FLOAT_TYPES = {"double precision", "real", "numeric"}
_NULL = r"\N"


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            get_settings().database_url_psycopg, min_size=1, max_size=4, open=True
        )
    return _pool


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Pooled connection; commits on clean exit, rolls back on exception."""
    with get_pool().connection() as conn:
        yield conn


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def read_frame(conn: psycopg.Connection, query: str, params: Sequence | dict | None = None) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(query, params)
        cols = [d.name for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def read_frame_copy(conn: psycopg.Connection, query: str, params: Sequence | dict | None = None,
                    parse_dates: Sequence[str] = ()) -> pd.DataFrame:
    """Bulk read via COPY (...) TO STDOUT CSV: several times faster than fetchall for big frames."""
    buf = io.BytesIO()
    with conn.cursor() as cur:
        with cur.copy(f"COPY ({query}) TO STDOUT WITH (FORMAT csv, HEADER true)", params) as cp:
            for chunk in cp:
                buf.write(chunk)
    buf.seek(0)
    return pd.read_csv(buf, parse_dates=list(parse_dates))


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def table_columns(conn: psycopg.Connection, table: str) -> dict[str, str]:
    """{column: type} for a table on the search_path, temp tables included (cached)."""
    if table not in _column_types:
        rows = conn.execute(
            """SELECT attname, format_type(atttypid, NULL) FROM pg_attribute
               WHERE attrelid = to_regclass(%s) AND attnum > 0 AND NOT attisdropped
               ORDER BY attnum""",
            (sql.Identifier(table).as_string(conn),),
        ).fetchall()
        if not rows:
            raise ValueError(f"unknown table {table!r}")
        _column_types[table] = dict(rows)
    return _column_types[table]


def _to_csv(df: pd.DataFrame, types: dict[str, str]) -> str:
    """Coerce columns to what Postgres COPY expects, then serialise to CSV."""
    out = pd.DataFrame(index=df.index)
    for col in df.columns:
        s = df[col]
        t = types[col]
        if t in _INT_TYPES:
            s = pd.to_numeric(s, errors="coerce").round().astype("Int64")
        elif t in _FLOAT_TYPES:
            s = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan)
        elif t == "boolean":
            s = s.map({True: "t", False: "f"})
        elif t == "date":
            s = pd.to_datetime(s, errors="coerce").dt.strftime("%Y-%m-%d")
        elif t.startswith("timestamp"):
            s = pd.to_datetime(s, errors="coerce", utc=True).map(
                lambda v: v.isoformat() if pd.notna(v) else None
            )
        elif t in ("jsonb", "json"):
            s = s.map(lambda v: json.dumps(v, default=str) if v is not None and v is not pd.NA else None)
        out[col] = s
    return out.to_csv(index=False, header=False, na_rep=_NULL)


def copy_frame(conn: psycopg.Connection, table: str, df: pd.DataFrame) -> int:
    """COPY a DataFrame into `table` (columns matched by name). Returns rows."""
    if df.empty:
        return 0
    types = table_columns(conn, table)
    unknown = set(df.columns) - set(types)
    if unknown:
        raise ValueError(f"{table}: unknown columns {sorted(unknown)}")
    stmt = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv, NULL {})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, df.columns)),
        sql.Literal(_NULL),
    )
    payload = _to_csv(df, types)
    with conn.cursor() as cur, cur.copy(stmt) as cp:
        cp.write(payload)
    return len(df)


def upsert_frame(
    conn: psycopg.Connection,
    table: str,
    df: pd.DataFrame,
    key_cols: Sequence[str],
    update_cols: Iterable[str] | None = None,
) -> int:
    """Bulk upsert: COPY -> temp table -> INSERT ... ON CONFLICT (key) DO UPDATE.

    update_cols defaults to every non-key column in df; pass [] for DO NOTHING.
    Duplicate keys inside df keep the last row.
    """
    if df.empty:
        return 0
    t0 = time.perf_counter()
    df = df.drop_duplicates(subset=list(key_cols), keep="last")
    cols = list(df.columns)
    update_cols = [c for c in cols if c not in key_cols] if update_cols is None else list(update_cols)
    tmp = _stage(conn, table, df)

    col_list = sql.SQL(", ").join(map(sql.Identifier, cols))
    if update_cols:
        action = sql.SQL("DO UPDATE SET {}").format(
            sql.SQL(", ").join(
                sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c)) for c in update_cols
            )
        )
    else:
        action = sql.SQL("DO NOTHING")
    stmt = sql.SQL("INSERT INTO {t} ({c}) SELECT {c} FROM {tmp} ON CONFLICT ({k}) {a}").format(
        t=sql.Identifier(table),
        c=col_list,
        tmp=sql.Identifier(tmp),
        k=sql.SQL(", ").join(map(sql.Identifier, key_cols)),
        a=action,
    )
    n = conn.execute(stmt).rowcount
    conn.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(tmp)))
    log.debug("upsert %s: %d rows in %.0f ms", table, n, (time.perf_counter() - t0) * 1000)
    return n


def _stage(conn: psycopg.Connection, table: str, df: pd.DataFrame) -> str:
    """COPY df into a temp table shaped like `table`'s columns; returns its name."""
    cols = list(df.columns)
    tmp = f"_tmp_{table}"
    conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(tmp)))
    conn.execute(
        sql.SQL("CREATE TEMP TABLE {} ON COMMIT DROP AS SELECT {} FROM {} WITH NO DATA").format(
            sql.Identifier(tmp),
            sql.SQL(", ").join(map(sql.Identifier, cols)),
            sql.Identifier(table),
        )
    )
    _column_types[tmp] = {c: table_columns(conn, table)[c] for c in cols}
    copy_frame(conn, tmp, df)
    return tmp


def update_from_frame(conn: psycopg.Connection, table: str, df: pd.DataFrame, key_cols: Sequence[str]) -> int:
    """Bulk UPDATE existing rows (partial columns) via COPY -> temp -> UPDATE ... FROM.

    Use instead of upsert_frame when df lacks NOT NULL columns: Postgres checks NOT NULL
    on the proposed insert row before ON CONFLICT arbitration.
    """
    if df.empty:
        return 0
    df = df.drop_duplicates(subset=list(key_cols), keep="last")
    tmp = _stage(conn, table, df)
    set_cols = [c for c in df.columns if c not in key_cols]
    stmt = sql.SQL("UPDATE {t} AS t SET {s} FROM {tmp} AS x WHERE {w}").format(
        t=sql.Identifier(table),
        s=sql.SQL(", ").join(sql.SQL("{0} = x.{0}").format(sql.Identifier(c)) for c in set_cols),
        tmp=sql.Identifier(tmp),
        w=sql.SQL(" AND ").join(sql.SQL("t.{0} = x.{0}").format(sql.Identifier(k)) for k in key_cols),
    )
    n = conn.execute(stmt).rowcount
    conn.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(tmp)))
    return n


def replace_rows(conn: psycopg.Connection, table: str, df: pd.DataFrame, where: dict) -> int:
    """Idempotent snapshot write: DELETE rows matching `where`, then COPY df.

    Runs inside the caller's transaction, so readers never see a half-written week.
    """
    cond = sql.SQL(" AND ").join(
        sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder()) for k in where
    )
    conn.execute(sql.SQL("DELETE FROM {} WHERE {}").format(sql.Identifier(table), cond), list(where.values()))
    return copy_frame(conn, table, df)

"""Backtest results: sweeps, runs, weekly equity, trades.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE TABLE backtest_sweeps (
    id           serial PRIMARY KEY,
    created_at   timestamptz NOT NULL DEFAULT now(),
    name         text,
    grid         jsonb,
    duration_ms  integer,
    notes        text
);

CREATE TABLE backtest_runs (
    id                 serial PRIMARY KEY,
    sweep_id           integer REFERENCES backtest_sweeps(id) ON DELETE CASCADE,
    created_at         timestamptz NOT NULL DEFAULT now(),
    name               text NOT NULL,
    params             jsonb NOT NULL,
    start_date         date NOT NULL,
    end_date           date NOT NULL,
    survivorship_bias  boolean NOT NULL,          -- true when only the current universe was available
    metrics            jsonb NOT NULL,
    benchmark_metrics  jsonb NOT NULL,
    -- denormalised for sorting the sweep table
    cagr               double precision,
    max_drawdown       double precision,
    sharpe             double precision,
    profit_factor      double precision,
    win_rate           double precision,
    trades             integer
);
CREATE INDEX backtest_runs_sweep_idx ON backtest_runs (sweep_id, cagr DESC);

CREATE TABLE backtest_equity (
    backtest_id   integer NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    date          date NOT NULL,
    equity        double precision NOT NULL,
    benchmark     double precision,
    invested_pct  double precision,
    drawdown      double precision,
    positions     integer,
    PRIMARY KEY (backtest_id, date)
);

CREATE TABLE backtest_trades (
    id            bigserial PRIMARY KEY,
    backtest_id   integer NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    symbol_id     integer NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    symbol        text NOT NULL,
    industry      text,
    signal_date   date NOT NULL,              -- week whose close produced the entry signal
    entry_date    date NOT NULL,              -- filled at the open of the following week
    entry_price   double precision NOT NULL,
    exit_date     date,
    exit_price    double precision,
    qty           integer NOT NULL,
    pnl           double precision,           -- after costs
    pnl_pct       double precision,
    weeks_held    integer,
    exit_reason   text,                       -- 'supertrend' | 'sector' | 'stale' | 'end'
    entry_score   double precision
);
CREATE INDEX backtest_trades_bt_idx ON backtest_trades (backtest_id, entry_date);
"""

DOWNGRADE_SQL = "DROP TABLE IF EXISTS backtest_trades, backtest_equity, backtest_runs, backtest_sweeps CASCADE;"


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)

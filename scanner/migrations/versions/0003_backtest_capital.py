"""Backtest equity: time-weighted nav and contributed capital (runs that add capital per buy).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-01
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE backtest_equity
            ADD COLUMN nav double precision,          -- equity with added capital taken out (returns, drawdown)
            ADD COLUMN contributed double precision;  -- starting capital + capital added so far
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE backtest_equity DROP COLUMN nav, DROP COLUMN contributed;")

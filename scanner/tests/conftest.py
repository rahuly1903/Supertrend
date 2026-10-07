import os
import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from settings import get_settings  # noqa: E402


@pytest.fixture
def pg_conn():
    """Connection to TEST_DATABASE_URL (or DATABASE_URL); always rolled back."""
    url = os.environ.get("TEST_DATABASE_URL") or get_settings().database_url_psycopg
    try:
        conn = psycopg.connect(url, connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.skip(f"postgres unavailable: {exc}")
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()

"""Environment settings (.env). Tunable strategy parameters live in config.yaml."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT.parent / ".env", ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://localhost:5432/supertrend"
    log_level: str = "INFO"

    capital: float = 1_000_000.0
    risk_per_trade_pct: float = 1.0

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    @property
    def database_url_psycopg(self) -> str:
        """libpq-style URL for psycopg (Railway hands out postgres:// URLs)."""
        url = self.database_url
        for prefix in ("postgresql+psycopg://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql://" + url[len(prefix):]
        return url

    @property
    def database_url_sqlalchemy(self) -> str:
        return "postgresql+psycopg://" + self.database_url_psycopg[len("postgresql://"):]


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""
config.py

Env-driven settings. Everything secret comes from the environment — Railway
env vars in prod, a local .env (never committed) for dev.
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str
    telegram_webhook_secret: str = ""

    google_api_key: str
    finnhub_api_key: str

    database_url: str

    google_service_account_json: str = ""

    port: int = 8000

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg_dialect(cls, v: str) -> str:
        # Railway's injected Postgres URL uses the plain "postgresql://"
        # scheme; our async engine needs the asyncpg dialect explicitly.
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v


settings = Settings()

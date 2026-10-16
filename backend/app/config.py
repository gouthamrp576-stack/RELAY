"""Load RELAY's database configuration."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="RELAY_DB_",
        extra="ignore",
    )

    host: str = "localhost"
    port: int = 5432
    name: str = "relay"
    user: str = "relay_app"
    password: SecretStr


@lru_cache
def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings()
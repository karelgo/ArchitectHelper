"""Application settings, loaded from environment variables / .env.

All variables use the ``ARCHFLOW_`` prefix — see ``.env.example``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ARCHFLOW_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # BiZZdesign Horizzon connection (all optional: without them ArchFlow
    # falls back to ArchiMate Open Exchange file export at publication).
    horizzon_base_url: str = ""
    horizzon_client_id: str = ""
    horizzon_client_secret: str = ""
    horizzon_token_url: str = ""  # defaults to <base_url>/oauth/token when empty

    database_url: str = "sqlite:///archflow.db"
    artifacts_dir: Path = Path("artifacts")

    @property
    def horizzon_configured(self) -> bool:
        return bool(self.horizzon_base_url and self.horizzon_client_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()

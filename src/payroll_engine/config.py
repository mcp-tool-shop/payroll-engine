"""Configuration management for payroll engine."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment."""

    database_url: str
    database_url_sync: str
    engine_version: str
    host: str
    port: int
    debug: bool

    @property
    def HOST(self) -> str:
        """Alias for host."""
        return self.host

    @property
    def PORT(self) -> int:
        """Alias for port."""
        return self.port

    @property
    def DEBUG(self) -> bool:
        """Alias for debug."""
        return self.debug

    @classmethod
    def from_env(cls) -> Settings:
        """Load settings from environment variables."""
        load_dotenv()

        return cls(
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql+asyncpg://postgres:postgres@localhost:5432/payroll_dev",
            ),
            database_url_sync=os.getenv(
                "DATABASE_URL_SYNC",
                "postgresql://postgres:postgres@localhost:5432/payroll_dev",
            ),
            engine_version=os.getenv("ENGINE_VERSION", "1.0.0"),
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8000")),
            debug=os.getenv("DEBUG", "false").lower() == "true",
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings.from_env()

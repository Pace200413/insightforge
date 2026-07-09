"""Application configuration.

All runtime configuration comes from environment variables (or a .env file),
validated by pydantic-settings. Nothing else in the codebase reads os.environ
directly -- this module is the single source of truth.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[3] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_env: str = "development"
    app_name: str = "InsightForge"
    log_level: str = "INFO"

    # Database
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "insightforge"
    postgres_user: str = "insightforge"
    postgres_password: str = "insightforge_dev_password"

    # LLM
    anthropic_api_key: str = ""
    groq_api_key: str = ""
    llm_provider: str = "anthropic"  # anthropic | groq

    # Safety limits (consumed by the SQL firewall in later stages)
    query_timeout_seconds: int = 15
    query_max_rows: int = 10_000
    query_max_repair_attempts: int = 3

    @property
    def database_url(self) -> str:
        """Async SQLAlchemy URL (asyncpg driver)."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        """Sync URL, used by seeding scripts and offline tools."""
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

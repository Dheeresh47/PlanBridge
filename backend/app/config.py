from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# Resolved from this file's location so the backend package behaves the same
# whichever directory a command is run from.
BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = BACKEND_DIR / ".env"


class Settings(BaseSettings):
    database_url: str

    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the application settings, reading the environment once.

    Settings are resolved on first use rather than at import time, so
    importing the application package never requires a configured
    environment. A DATABASE_URL environment variable takes precedence over
    the .env file, which is how Alembic is pointed at another database.
    """
    return Settings()

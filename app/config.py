from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/cognitive"
    redis_url: str | None = None
    snapshot_interval_seconds: int = 30
    window_snapshots: int = 6
    dashboard_bucket_seconds: int = 60
    model_version: str = "rules-v1"
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openai/gpt-4o-mini"
    openrouter_http_referer: str | None = None
    openrouter_app_title: str | None = "Cognitive State Backend"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "cognitive-state-api"
    debug: bool = False

    database_url: str = "postgresql+asyncpg://nitin:1234@localhost:5432/cognitive_db"
    redis_url: str = "redis://localhost:6379/0"

    snapshot_buffer_size: int = 10
    redis_buffer_ttl_sec: int = 86400
    prediction_lock_ttl_sec: int = 5

    model_name: str = "rules_stub"
    model_version: str = "0.1.0"


settings = Settings()

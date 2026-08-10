"""Typed environment configuration shared by API and workers."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded exclusively from environment or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "influencer-outreach"
    app_env: Literal["development", "test", "production"] = "development"
    app_version: str = "0.1.0"
    app_master_key: SecretStr | None = None
    log_level: str = "INFO"
    business_timezone: str = "Asia/Shanghai"
    api_prefix: str = "/api/v1"

    session_cookie_name: str = "outreach_session"
    csrf_cookie_name: str = "outreach_csrf"
    session_default_hours: int = Field(default=12, ge=1)
    session_remember_days: int = Field(default=30, ge=1)
    login_max_failures: int = Field(default=5, ge=1)
    login_lock_seconds: int = Field(default=300, ge=1)

    database_url: str = "postgresql+psycopg://outreach@localhost:5432/outreach"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    import_data_dir: Path = Path("/data/imports")
    import_retention_days: int = Field(default=30, ge=1)

    @property
    def secure_cookies(self) -> bool:
        return self.app_env == "production"

    @field_validator("business_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @model_validator(mode="after")
    def require_production_master_key(self) -> "Settings":
        if self.app_env == "production" and self.app_master_key is None:
            raise ValueError("APP_MASTER_KEY is required in production")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the immutable process-wide settings instance."""

    return Settings()

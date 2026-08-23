"""Typed environment configuration shared by API and workers."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
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
    freshness_fresh_days: int = Field(default=7, ge=0)
    freshness_aging_days: int = Field(default=30, ge=0)
    freshness_stale_days: int = Field(default=90, ge=0)

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
    import_max_file_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    import_max_batch_files: int = Field(default=20, ge=1)
    import_max_batch_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    import_max_batch_rows: int = Field(default=10_000, ge=1)
    import_source_acquired_clock_skew_seconds: int = Field(default=300, ge=0)
    import_max_xlsx_uncompressed_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    import_max_xlsx_entries: int = Field(default=10_000, ge=1)
    import_max_xlsx_compression_ratio: int = Field(default=100, ge=1)
    import_max_rows: int = Field(default=100_000, ge=1)
    import_max_columns: int = Field(default=200, ge=1)
    import_max_cells: int = Field(default=5_000_000, ge=1)
    import_max_cell_chars: int = Field(default=100_000, ge=1)
    import_max_warnings: int = Field(default=10_000, ge=1)

    # PostgreSQL is the authoritative import-task clock and attempt ledger.
    # Celery/Redis delivery may be duplicated or lost and is reconciled from
    # these bounded policies instead of Celery retry metadata.
    import_task_reconcile_interval_seconds: int = Field(default=15, ge=1)
    import_task_reconcile_batch_size: int = Field(default=100, ge=1, le=1_000)
    import_task_dispatch_max_attempts: int = Field(default=8, ge=1)
    import_task_dispatch_backoff_base_seconds: int = Field(default=5, ge=1)
    import_task_dispatch_backoff_max_seconds: int = Field(default=300, ge=1)
    import_task_run_backoff_base_seconds: int = Field(default=5, ge=1)
    import_task_run_backoff_max_seconds: int = Field(default=300, ge=1)
    import_task_lease_seconds: int = Field(default=120, ge=2)
    import_task_heartbeat_seconds: int = Field(default=30, ge=1)
    import_task_legacy_parse_max_run_attempts: int = Field(default=3, ge=1)
    import_task_file_parse_max_run_attempts: int = Field(default=3, ge=1)
    import_task_preview_max_run_attempts: int = Field(default=3, ge=1)
    import_task_confirm_max_run_attempts: int = Field(default=3, ge=1)

    # Content Activity is disabled by default.  Enabling provider traffic requires
    # an explicit feature/config/governance gate and never happens incidentally.
    content_activity_enabled: bool = False
    content_activity_xhs_enabled: bool = False
    content_activity_provider_governance_approved: bool = False
    content_activity_provider_max_calls_per_run: int = Field(default=0, ge=0, le=1_000)
    content_activity_xhs_max_calls_per_account: int = Field(default=1, ge=1, le=1)
    content_activity_refresh_batch_size: int = Field(default=20, ge=1, le=100)
    content_activity_refresh_concurrency: int = Field(default=1, ge=1, le=1)
    # These govern the durable request ledger, rather than provider HTTP calls.
    # They remain deliberately small so a queue outage cannot turn into an
    # unbounded provider replay once governance has approved the feature.
    content_activity_refresh_max_attempts: int = Field(default=3, ge=1, le=8)
    content_activity_refresh_lease_seconds: int = Field(default=120, ge=2, le=3_600)
    content_activity_refresh_retry_backoff_base_seconds: int = Field(default=5, ge=1, le=300)
    content_activity_refresh_retry_backoff_max_seconds: int = Field(default=300, ge=1, le=3_600)
    content_activity_refresh_reconcile_interval_seconds: int = Field(default=30, ge=1, le=3_600)
    content_activity_refresh_reconcile_batch_size: int = Field(default=100, ge=1, le=1_000)
    content_activity_trusted_freshness_days: int = Field(default=7, ge=1)
    content_activity_http_connect_timeout_seconds: float = Field(default=2.0, gt=0, le=60)
    content_activity_http_read_timeout_seconds: float = Field(default=8.0, gt=0, le=60)
    content_activity_http_pool_timeout_seconds: float = Field(default=2.0, gt=0, le=60)
    content_activity_http_total_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    content_activity_http_max_response_bytes: int = Field(
        default=256 * 1024,
        ge=1024,
        le=2 * 1024 * 1024,
    )
    tikhub_base_url: str = ""
    tikhub_api_key: SecretStr | None = None

    @property
    def secure_cookies(self) -> bool:
        return self.app_env == "production"

    @field_validator("business_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @model_validator(mode="after")
    def validate_runtime_invariants(self) -> "Settings":
        if self.app_env == "production":
            master_key = (
                self.app_master_key.get_secret_value() if self.app_master_key is not None else ""
            )
            if not master_key.strip():
                raise ValueError("APP_MASTER_KEY is required and must be non-blank in production")
        if not (self.freshness_fresh_days < self.freshness_aging_days < self.freshness_stale_days):
            raise ValueError(
                "FRESHNESS_FRESH_DAYS, FRESHNESS_AGING_DAYS, and FRESHNESS_STALE_DAYS "
                "must be strictly increasing"
            )
        if self.import_task_heartbeat_seconds >= self.import_task_lease_seconds:
            raise ValueError(
                "IMPORT_TASK_HEARTBEAT_SECONDS must be less than IMPORT_TASK_LEASE_SECONDS"
            )
        if (
            self.import_task_dispatch_backoff_base_seconds
            > self.import_task_dispatch_backoff_max_seconds
        ):
            raise ValueError(
                "IMPORT_TASK_DISPATCH_BACKOFF_BASE_SECONDS must not exceed "
                "IMPORT_TASK_DISPATCH_BACKOFF_MAX_SECONDS"
            )
        if self.import_task_run_backoff_base_seconds > self.import_task_run_backoff_max_seconds:
            raise ValueError(
                "IMPORT_TASK_RUN_BACKOFF_BASE_SECONDS must not exceed "
                "IMPORT_TASK_RUN_BACKOFF_MAX_SECONDS"
            )
        if self.content_activity_xhs_enabled and not self.content_activity_enabled:
            raise ValueError("CONTENT_ACTIVITY_XHS_ENABLED requires CONTENT_ACTIVITY_ENABLED")
        if (
            self.content_activity_refresh_retry_backoff_base_seconds
            > self.content_activity_refresh_retry_backoff_max_seconds
        ):
            raise ValueError(
                "CONTENT_ACTIVITY_REFRESH_RETRY_BACKOFF_BASE_SECONDS must not exceed "
                "CONTENT_ACTIVITY_REFRESH_RETRY_BACKOFF_MAX_SECONDS"
            )
        if self.content_activity_enabled:
            if not self.content_activity_xhs_enabled:
                raise ValueError(
                    "D1A CONTENT_ACTIVITY_ENABLED requires CONTENT_ACTIVITY_XHS_ENABLED"
                )
            if not self.content_activity_provider_governance_approved:
                raise ValueError(
                    "CONTENT_ACTIVITY_ENABLED requires "
                    "CONTENT_ACTIVITY_PROVIDER_GOVERNANCE_APPROVED"
                )
            if self.content_activity_provider_max_calls_per_run < 1:
                raise ValueError(
                    "CONTENT_ACTIVITY_ENABLED requires "
                    "CONTENT_ACTIVITY_PROVIDER_MAX_CALLS_PER_RUN >= 1"
                )
            parsed_tikhub_url = urlsplit(self.tikhub_base_url)
            if (
                parsed_tikhub_url.scheme != "https"
                or not parsed_tikhub_url.netloc
                or parsed_tikhub_url.username is not None
                or parsed_tikhub_url.password is not None
                or parsed_tikhub_url.path not in ("", "/")
                or parsed_tikhub_url.query
                or parsed_tikhub_url.fragment
            ):
                raise ValueError(
                    "TIKHUB_BASE_URL must be an HTTPS origin when Content Activity is enabled"
                )
            tikhub_api_key = (
                self.tikhub_api_key.get_secret_value() if self.tikhub_api_key is not None else ""
            )
            if not tikhub_api_key.strip():
                raise ValueError("TIKHUB_API_KEY is required and must be non-blank when enabled")
            if self.content_activity_http_total_timeout_seconds < max(
                self.content_activity_http_connect_timeout_seconds,
                self.content_activity_http_read_timeout_seconds,
                self.content_activity_http_pool_timeout_seconds,
            ):
                raise ValueError(
                    "CONTENT_ACTIVITY_HTTP_TOTAL_TIMEOUT_SECONDS must be at least every "
                    "component timeout"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the immutable process-wide settings instance."""

    return Settings()

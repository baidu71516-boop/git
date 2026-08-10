"""Validated Phase 1B API and service contracts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend_core.imports.enums import (
    CollectionJobStatus,
    ImportJobStatus,
    ImportMatchType,
    ImportRowAction,
    ImportSourceType,
)


class CollectionJobCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    industry: str = Field(min_length=1, max_length=160)
    subdirection: str | None = Field(default=None, max_length=200)
    purpose: str = Field(min_length=1, max_length=2000)
    target_action: str = Field(min_length=1, max_length=160)
    follower_min: int | None = Field(default=None, ge=0)
    follower_max: int | None = Field(default=None, ge=0)
    target_count: int = Field(gt=0, le=1_000_000)
    source_type: ImportSourceType = ImportSourceType.MANUAL_HUITUN_EXPORT
    notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_follower_range(self) -> "CollectionJobCreate":
        if (
            self.follower_min is not None
            and self.follower_max is not None
            and self.follower_min > self.follower_max
        ):
            raise ValueError("follower_min must not exceed follower_max")
        return self


class CollectionJobPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    industry: str
    subdirection: str | None
    purpose: str
    target_action: str
    follower_min: int | None
    follower_max: int | None
    target_count: int
    department_id: UUID
    owner_operator_id: UUID
    source_type: ImportSourceType
    status: CollectionJobStatus
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ImportMappingUpdate(BaseModel):
    mapping: dict[str, str] = Field(min_length=1)


class ImportConfirmInput(BaseModel):
    preview_revision: int = Field(ge=1)


class ImportJobPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    collection_job_id: UUID
    department_id: UUID
    operator_id: UUID
    original_filename: str
    mime_type: str
    file_size: int
    sha256: str
    source_type: ImportSourceType
    status: ImportJobStatus
    detected_fields: list[str] | None
    field_mapping: dict[str, str] | None
    preview_revision: int
    preview_summary: dict[str, Any] | None
    result: dict[str, Any] | None
    total_rows: int
    valid_rows: int
    warning_rows: int
    error_rows: int
    created_rows: int
    updated_rows: int
    no_change_rows: int
    skipped_rows: int
    manual_review_rows: int
    confirmed_revision: int | None
    confirmed_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ImportRowPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    import_job_id: UUID
    row_number: int
    raw_data: dict[str, Any]
    normalized_data: dict[str, Any] | None
    matched_influencer_id: UUID | None
    matched_platform_account_id: UUID | None
    match_type: ImportMatchType
    action: ImportRowAction
    merge_plan: dict[str, Any] | None
    warnings: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    preview_revision: int
    plan_hash: str
    committed_action: ImportRowAction | None
    committed_at: datetime | None


class ImportRowsPage(BaseModel):
    items: list[ImportRowPublic]
    total: int
    offset: int
    limit: int


class ImportDispatchResult(BaseModel):
    import_job_id: UUID
    status: ImportJobStatus
    preview_revision: int
    task_id: str | None
    idempotent: bool = False

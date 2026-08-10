"""Collection jobs and persisted two-phase import plans."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend_core.db.base import Base
from backend_core.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from backend_core.db.types import JSON_DOCUMENT
from backend_core.imports.enums import (
    CollectionJobStatus,
    ImportJobStatus,
    ImportMatchType,
    ImportRowAction,
    ImportSourceType,
    StoredFileType,
)


def enum_values(enum_type: type[Any]) -> list[str]:
    return [item.value for item in enum_type]


class StoredImportFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "stored_import_files"
    __table_args__ = (
        CheckConstraint("size > 0", name="ck_stored_import_file_size"),
        Index("ix_stored_import_files_expires_at", "expires_at"),
    )

    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    detected_type: Mapped[StoredFileType] = mapped_column(
        Enum(StoredFileType, name="stored_file_type", values_callable=enum_values), nullable=False
    )
    detected_mime: Mapped[str] = mapped_column(String(160), nullable=False)
    encoding: Mapped[str | None] = mapped_column(String(40), nullable=True)
    parse_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CollectionJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "collection_jobs"
    __table_args__ = (
        UniqueConstraint("id", "department_id", name="uq_collection_job_department_pair"),
        ForeignKeyConstraint(
            ["owner_operator_id", "department_id"],
            ["operators.id", "operators.department_id"],
            name="fk_collection_job_owner_department",
            ondelete="RESTRICT",
        ),
        CheckConstraint("target_count > 0", name="ck_collection_job_target_count"),
        CheckConstraint(
            "follower_min IS NULL OR follower_min >= 0",
            name="ck_collection_job_follower_min",
        ),
        CheckConstraint(
            "follower_max IS NULL OR follower_max >= 0",
            name="ck_collection_job_follower_max",
        ),
        CheckConstraint(
            "follower_min IS NULL OR follower_max IS NULL OR follower_min <= follower_max",
            name="ck_collection_job_follower_range",
        ),
        Index("ix_collection_jobs_department_status", "department_id", "status"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    industry: Mapped[str] = mapped_column(String(160), nullable=False)
    subdirection: Mapped[str | None] = mapped_column(String(200), nullable=True)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    target_action: Mapped[str] = mapped_column(String(160), nullable=False)
    follower_min: Mapped[int | None] = mapped_column(nullable=True)
    follower_max: Mapped[int | None] = mapped_column(nullable=True)
    target_count: Mapped[int] = mapped_column(nullable=False)
    department_id: Mapped[UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False
    )
    owner_operator_id: Mapped[UUID] = mapped_column(nullable=False)
    source_type: Mapped[ImportSourceType] = mapped_column(
        Enum(ImportSourceType, name="import_source_type", values_callable=enum_values),
        nullable=False,
    )
    status: Mapped[CollectionJobStatus] = mapped_column(
        Enum(CollectionJobStatus, name="collection_job_status", values_callable=enum_values),
        nullable=False,
        default=CollectionJobStatus.DRAFT,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImportJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "import_jobs"
    __table_args__ = (
        Index("ix_import_jobs_department_status", "department_id", "status"),
        Index("ix_import_jobs_sha256", "sha256"),
        Index("ix_import_jobs_collection", "collection_job_id"),
        Index("ix_import_jobs_stored_file", "stored_file_id"),
        ForeignKeyConstraint(
            ["collection_job_id", "department_id"],
            ["collection_jobs.id", "collection_jobs.department_id"],
            name="fk_import_job_collection_department",
            ondelete="RESTRICT",
        ),
        CheckConstraint("file_size > 0", name="ck_import_job_file_size"),
        CheckConstraint("preview_revision >= 0", name="ck_import_job_preview_revision"),
        CheckConstraint(
            "confirmed_revision IS NULL OR confirmed_revision <= preview_revision",
            name="ck_import_job_confirmed_revision",
        ),
        CheckConstraint(
            "total_rows >= 0 AND valid_rows >= 0 AND warning_rows >= 0 "
            "AND error_rows >= 0 AND created_rows >= 0 AND updated_rows >= 0 "
            "AND no_change_rows >= 0 AND skipped_rows >= 0 AND manual_review_rows >= 0",
            name="ck_import_job_counts_nonnegative",
        ),
    )

    collection_job_id: Mapped[UUID] = mapped_column(nullable=False)
    department_id: Mapped[UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False
    )
    operator_id: Mapped[UUID] = mapped_column(
        ForeignKey("operators.id", ondelete="RESTRICT"), nullable=False
    )
    stored_file_id: Mapped[UUID] = mapped_column(
        ForeignKey("stored_import_files.id", ondelete="RESTRICT"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(160), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[ImportSourceType] = mapped_column(
        Enum(ImportSourceType, name="import_source_type", values_callable=enum_values),
        nullable=False,
    )
    status: Mapped[ImportJobStatus] = mapped_column(
        Enum(ImportJobStatus, name="import_job_status", values_callable=enum_values),
        nullable=False,
    )
    detected_fields: Mapped[list[str] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    field_mapping: Mapped[dict[str, str] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    mapping_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preview_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    preview_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    no_change_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manual_review_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confirmed_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parse_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirm_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImportRow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "import_rows"
    __table_args__ = (
        UniqueConstraint("import_job_id", "row_number", name="uq_import_rows_job_number"),
        UniqueConstraint("id", "import_job_id", name="uq_import_row_job_pair"),
        ForeignKeyConstraint(
            ["matched_platform_account_id", "matched_influencer_id"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.influencer_id"],
            name="fk_import_row_match_account_influencer",
            ondelete="RESTRICT",
        ),
        CheckConstraint("row_number >= 2", name="ck_import_row_number"),
        CheckConstraint(
            "matched_platform_account_id IS NULL OR matched_influencer_id IS NOT NULL",
            name="ck_import_row_match_pair",
        ),
        Index("ix_import_rows_job_action", "import_job_id", "action"),
    )

    import_job_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    normalized_data: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    matched_influencer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("influencers.id", ondelete="RESTRICT"), nullable=True
    )
    matched_platform_account_id: Mapped[UUID | None] = mapped_column(nullable=True)
    match_type: Mapped[ImportMatchType] = mapped_column(
        Enum(ImportMatchType, name="import_match_type", values_callable=enum_values),
        nullable=False,
        default=ImportMatchType.NONE,
    )
    action: Mapped[ImportRowAction] = mapped_column(
        Enum(ImportRowAction, name="import_row_action", values_callable=enum_values),
        nullable=False,
    )
    merge_plan: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=list
    )
    errors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=list
    )
    preview_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    committed_action: Mapped[ImportRowAction | None] = mapped_column(
        Enum(ImportRowAction, name="import_row_action", values_callable=enum_values), nullable=True
    )
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

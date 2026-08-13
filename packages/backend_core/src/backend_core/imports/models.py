"""Collection jobs and persisted two-phase import plans."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
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
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend_core.db.base import Base
from backend_core.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from backend_core.db.types import JSON_DOCUMENT
from backend_core.imports.enums import (
    CollectionJobStatus,
    ImportJobFailedStage,
    ImportJobFileStatus,
    ImportJobStatus,
    ImportMatchType,
    ImportRowAction,
    ImportSourceType,
    ImportTaskKind,
    ImportTaskState,
    SourceAcquiredAtOrigin,
    StoredFileType,
)


def default_screening_rules() -> dict[str, Any]:
    return {"schema_version": 1, "platforms": [], "source_tags_exact_any": []}


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
    import_job_files: Mapped[list["ImportJobFile"]] = relationship(
        back_populates="stored_file", lazy="raise"
    )


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
        CheckConstraint(
            "screening_rules_revision >= 1",
            name="ck_collection_job_screening_rules_revision",
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
    screening_rules: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        nullable=False,
        default=default_screening_rules,
        server_default='{"schema_version":1,"platforms":[],"source_tags_exact_any":[]}',
    )
    screening_rules_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )


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
        CheckConstraint("file_size IS NULL OR file_size > 0", name="ck_import_job_file_size"),
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
    stored_file_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("stored_import_files.id", ondelete="RESTRICT"), nullable=True
    )
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    failed_stage: Mapped[ImportJobFailedStage | None] = mapped_column(
        Enum(ImportJobFailedStage, name="import_job_failed_stage", values_callable=enum_values),
        nullable=True,
    )
    files: Mapped[list["ImportJobFile"]] = relationship(
        back_populates="import_job", lazy="raise", order_by="ImportJobFile.position"
    )


class ImportJobFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "import_job_files"
    __table_args__ = (
        UniqueConstraint("import_job_id", "position", name="uq_import_job_file_position"),
        UniqueConstraint("import_job_id", "stored_file_id", name="uq_import_job_file_stored_file"),
        UniqueConstraint("id", "import_job_id", name="uq_import_job_file_job_pair"),
        CheckConstraint("position >= 1", name="ck_import_job_file_position"),
        CheckConstraint(
            "raw_rows >= 0 AND warning_rows >= 0 AND error_rows >= 0 " "AND parse_attempts >= 0",
            name="ck_import_job_file_counts_nonnegative",
        ),
        CheckConstraint(
            "(source_acquired_at_origin = 'legacy_unknown' AND source_acquired_at IS NULL) "
            "OR (source_acquired_at_origin IN ('server_default', 'user_confirmed') "
            "AND source_acquired_at IS NOT NULL)",
            name="ck_import_job_file_acquisition_origin",
        ),
        CheckConstraint(
            "NOT source_acquired_at_confirmation_required "
            "OR (source_acquired_at IS NOT NULL "
            "AND source_acquired_at_origin = 'server_default')",
            name="ck_import_job_file_acquisition_confirmation",
        ),
    )

    import_job_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    stored_file_id: Mapped[UUID] = mapped_column(
        ForeignKey("stored_import_files.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_mime: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[ImportJobFileStatus] = mapped_column(
        Enum(ImportJobFileStatus, name="import_job_file_status", values_callable=enum_values),
        nullable=False,
        default=ImportJobFileStatus.UPLOADED,
    )
    source_acquired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_acquired_at_origin: Mapped[SourceAcquiredAtOrigin] = mapped_column(
        Enum(
            SourceAcquiredAtOrigin,
            name="source_acquired_at_origin",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    source_acquired_at_confirmation_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    detected_fields: Mapped[list[str] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    field_mapping: Mapped[dict[str, str] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    mapping_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parse_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parse_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    parse_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    excluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    import_job: Mapped[ImportJob] = relationship(back_populates="files", lazy="raise")
    stored_file: Mapped[StoredImportFile] = relationship(
        back_populates="import_job_files", lazy="raise"
    )
    client_ids: Mapped[list["ImportJobFileClientId"]] = relationship(
        back_populates="import_job_file", lazy="raise"
    )
    rows: Mapped[list["ImportRow"]] = relationship(back_populates="import_job_file", lazy="raise")


class ImportJobFileClientId(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "import_job_file_client_ids"
    __table_args__ = (
        UniqueConstraint(
            "import_job_id",
            "client_file_id",
            name="uq_import_job_file_client_id_alias",
        ),
        ForeignKeyConstraint(
            ["import_job_file_id", "import_job_id"],
            ["import_job_files.id", "import_job_files.import_job_id"],
            name="fk_import_job_file_client_id_file_job",
            ondelete="RESTRICT",
        ),
        Index("ix_import_job_file_client_ids_file", "import_job_file_id"),
    )

    import_job_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    import_job_file_id: Mapped[UUID] = mapped_column(nullable=False)
    client_file_id: Mapped[str] = mapped_column(String(160), nullable=False)

    import_job_file: Mapped[ImportJobFile] = relationship(back_populates="client_ids", lazy="raise")


class ImportTaskRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "import_task_requests"
    __table_args__ = (
        UniqueConstraint("task_token", name="uq_import_task_request_token"),
        ForeignKeyConstraint(
            ["import_job_file_id", "import_job_id"],
            ["import_job_files.id", "import_job_files.import_job_id"],
            name="fk_import_task_request_file_job",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "dispatch_attempts >= 0 AND run_attempts >= 0",
            name="ck_import_task_request_attempts_nonnegative",
        ),
        CheckConstraint(
            "(task_kind = 'file_parse' AND import_job_file_id IS NOT NULL "
            "AND preview_revision IS NULL) "
            "OR (task_kind = 'confirm' AND import_job_file_id IS NULL "
            "AND preview_revision IS NOT NULL AND preview_revision >= 1) "
            "OR (task_kind IN ('legacy_parse', 'preview') "
            "AND import_job_file_id IS NULL AND preview_revision IS NULL)",
            name="ck_import_task_request_target",
        ),
        CheckConstraint(
            "(state <> 'completed' OR completed_at IS NOT NULL) "
            "AND (state <> 'running' OR lease_expires_at IS NOT NULL) "
            "AND (state <> 'retry_wait' OR next_retry_at IS NOT NULL)",
            name="ck_import_task_request_state_timestamps",
        ),
        Index(
            "uq_import_task_request_active_legacy_parse",
            "import_job_id",
            unique=True,
            postgresql_where=text(
                "task_kind = 'legacy_parse' " "AND state IN ('requested', 'running', 'retry_wait')"
            ),
        ),
        Index(
            "uq_import_task_request_active_file_parse",
            "import_job_id",
            "import_job_file_id",
            unique=True,
            postgresql_where=text(
                "task_kind = 'file_parse' " "AND state IN ('requested', 'running', 'retry_wait')"
            ),
        ),
        Index(
            "uq_import_task_request_active_preview",
            "import_job_id",
            unique=True,
            postgresql_where=text(
                "task_kind = 'preview' " "AND state IN ('requested', 'running', 'retry_wait')"
            ),
        ),
        Index(
            "uq_import_task_request_active_confirm",
            "import_job_id",
            "preview_revision",
            unique=True,
            postgresql_where=text(
                "task_kind = 'confirm' " "AND state IN ('requested', 'running', 'retry_wait')"
            ),
        ),
        Index(
            "ix_import_task_requests_due",
            func.coalesce(text("next_retry_at"), text("requested_at")),
            "requested_at",
            "id",
            postgresql_where=text("state IN ('requested', 'retry_wait')"),
        ),
        Index(
            "ix_import_task_requests_expired_lease",
            "lease_expires_at",
            "id",
            postgresql_where=text("state = 'running'"),
        ),
    )

    task_token: Mapped[UUID] = mapped_column(nullable=False)
    task_kind: Mapped[ImportTaskKind] = mapped_column(
        Enum(ImportTaskKind, name="import_task_kind", values_callable=enum_values),
        nullable=False,
    )
    import_job_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    import_job_file_id: Mapped[UUID | None] = mapped_column(nullable=True)
    preview_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[ImportTaskState] = mapped_column(
        Enum(ImportTaskState, name="import_task_state", values_callable=enum_values),
        nullable=False,
        default=ImportTaskState.REQUESTED,
    )
    dispatch_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    run_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_dispatch_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ImportRow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "import_rows"
    __table_args__ = (
        UniqueConstraint("import_job_file_id", "row_number", name="uq_import_rows_file_number"),
        UniqueConstraint("id", "import_job_id", name="uq_import_row_job_pair"),
        ForeignKeyConstraint(
            ["import_job_file_id", "import_job_id"],
            ["import_job_files.id", "import_job_files.import_job_id"],
            name="fk_import_row_file_job",
            ondelete="RESTRICT",
        ),
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
    import_job_file_id: Mapped[UUID] = mapped_column(nullable=False)
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
    import_job_file: Mapped[ImportJobFile] = relationship(back_populates="rows", lazy="raise")


Index(
    "ix_import_rows_account_committed_job",
    ImportRow.matched_platform_account_id,
    ImportRow.committed_at.desc(),
    ImportRow.import_job_id,
)

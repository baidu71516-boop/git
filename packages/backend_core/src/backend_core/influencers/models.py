"""Company-level influencer subjects and their platform accounts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend_core.db.base import Base
from backend_core.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from backend_core.db.types import JSON_DOCUMENT
from backend_core.influencers.enums import (
    ContactType,
    ContactValidationStatus,
    CRMStage,
    DataSource,
    InfluencerStatus,
    Platform,
)


def enum_values(enum_type: type[Any]) -> list[str]:
    return [item.value for item in enum_type]


class Influencer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "influencers"

    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    owner_operator_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("operators.id", ondelete="SET NULL"), nullable=True
    )
    crm_stage: Mapped[CRMStage] = mapped_column(
        Enum(CRMStage, name="crm_stage", values_callable=enum_values),
        nullable=False,
        default=CRMStage.TO_DEVELOP,
    )
    status: Mapped[InfluencerStatus] = mapped_column(
        Enum(InfluencerStatus, name="influencer_status", values_callable=enum_values),
        nullable=False,
        default=InfluencerStatus.ACTIVE,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InfluencerPlatformAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "influencer_platform_accounts"
    __table_args__ = (
        UniqueConstraint("platform", "platform_account_id", name="uq_platform_account_identity"),
        UniqueConstraint("platform", "normalized_profile_url", name="uq_platform_profile_url"),
        UniqueConstraint("id", "influencer_id", name="uq_platform_account_influencer_pair"),
        UniqueConstraint("id", "platform", name="uq_platform_account_platform_pair"),
        Index("ix_platform_accounts_influencer", "influencer_id", "is_active"),
    )

    influencer_id: Mapped[UUID] = mapped_column(
        ForeignKey("influencers.id", ondelete="RESTRICT"), nullable=False
    )
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, name="platform_enum", values_callable=enum_values), nullable=False
    )
    platform_account_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    account_name: Mapped[str] = mapped_column(String(160), nullable=False)
    account_handle: Mapped[str | None] = mapped_column(String(160), nullable=True)
    profile_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_profile_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source: Mapped[DataSource] = mapped_column(
        Enum(DataSource, name="data_source", values_callable=enum_values), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(40), nullable=True)
    region_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    mcn_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_tags: Mapped[list[str] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    creator_level: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_brand_partner: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class InfluencerSourceState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "influencer_source_states"
    __table_args__ = (
        UniqueConstraint("platform_account_id", "source", name="uq_platform_account_source_state"),
        ForeignKeyConstraint(
            ["platform_account_id", "influencer_id"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.influencer_id"],
            name="fk_source_state_account_influencer",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["last_import_row_id", "last_import_job_id"],
            ["import_rows.id", "import_rows.import_job_id"],
            name="fk_source_state_last_import",
            ondelete="RESTRICT",
        ),
        CheckConstraint("state_version >= 1", name="ck_source_state_version"),
    )

    influencer_id: Mapped[UUID] = mapped_column(
        ForeignKey("influencers.id", ondelete="RESTRICT"), nullable=False
    )
    platform_account_id: Mapped[UUID] = mapped_column(nullable=False)
    source: Mapped[DataSource] = mapped_column(
        Enum(DataSource, name="data_source", values_callable=enum_values), nullable=False
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    source_data_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state_version: Mapped[int] = mapped_column(nullable=False, default=1)
    last_import_job_id: Mapped[UUID] = mapped_column(nullable=False)
    last_import_row_id: Mapped[UUID] = mapped_column(nullable=False)


class InfluencerContact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "influencer_contacts"
    __table_args__ = (
        UniqueConstraint(
            "influencer_id",
            "type",
            "normalized_value",
            "source",
            name="uq_influencer_contact_source",
        ),
        ForeignKeyConstraint(
            ["platform_account_id", "influencer_id"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.influencer_id"],
            name="fk_contact_account_influencer",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["first_import_row_id", "first_import_job_id"],
            ["import_rows.id", "import_rows.import_job_id"],
            name="fk_contact_first_import",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["last_import_row_id", "last_import_job_id"],
            ["import_rows.id", "import_rows.import_job_id"],
            name="fk_contact_last_import",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(first_import_job_id IS NULL AND first_import_row_id IS NULL) OR "
            "(first_import_job_id IS NOT NULL AND first_import_row_id IS NOT NULL)",
            name="ck_contact_first_import_pair",
        ),
        CheckConstraint(
            "(last_import_job_id IS NULL AND last_import_row_id IS NULL) OR "
            "(last_import_job_id IS NOT NULL AND last_import_row_id IS NOT NULL)",
            name="ck_contact_last_import_pair",
        ),
        CheckConstraint(
            "source = 'manual' OR (first_import_job_id IS NOT NULL AND "
            "last_import_job_id IS NOT NULL)",
            name="ck_contact_import_provenance",
        ),
        CheckConstraint("last_seen_at >= first_seen_at", name="ck_contact_seen_range"),
        Index("ix_influencer_contacts_normalized", "type", "normalized_value"),
    )

    influencer_id: Mapped[UUID] = mapped_column(
        ForeignKey("influencers.id", ondelete="RESTRICT"), nullable=False
    )
    platform_account_id: Mapped[UUID | None] = mapped_column(nullable=True)
    type: Mapped[ContactType] = mapped_column(
        Enum(ContactType, name="contact_type", values_callable=enum_values), nullable=False
    )
    value: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[DataSource] = mapped_column(
        Enum(DataSource, name="data_source", values_callable=enum_values), nullable=False
    )
    validation_status: Mapped[ContactValidationStatus] = mapped_column(
        Enum(
            ContactValidationStatus,
            name="contact_validation_status",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    possible_duplicate_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_import_job_id: Mapped[UUID | None] = mapped_column(nullable=True)
    first_import_row_id: Mapped[UUID | None] = mapped_column(nullable=True)
    last_import_job_id: Mapped[UUID | None] = mapped_column(nullable=True)
    last_import_row_id: Mapped[UUID | None] = mapped_column(nullable=True)


class InfluencerCurrentMetrics(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "influencer_current_metrics"
    __table_args__ = (
        UniqueConstraint(
            "platform_account_id", "source", name="uq_platform_account_current_metrics"
        ),
        ForeignKeyConstraint(
            ["platform_account_id", "influencer_id"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.influencer_id"],
            name="fk_current_metrics_account_influencer",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["last_import_row_id", "last_import_job_id"],
            ["import_rows.id", "import_rows.import_job_id"],
            name="fk_current_metrics_last_import",
            ondelete="RESTRICT",
        ),
    )

    influencer_id: Mapped[UUID] = mapped_column(
        ForeignKey("influencers.id", ondelete="RESTRICT"), nullable=False
    )
    platform_account_id: Mapped[UUID] = mapped_column(nullable=False)
    source: Mapped[DataSource] = mapped_column(
        Enum(DataSource, name="data_source", values_callable=enum_values), nullable=False
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    metrics_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_import_job_id: Mapped[UUID] = mapped_column(nullable=False)
    last_import_row_id: Mapped[UUID] = mapped_column(nullable=False)


class InfluencerMetricSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "influencer_metric_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "platform_account_id",
            "source",
            "source_updated_at",
            "metrics_hash",
            name="uq_platform_metric_snapshot",
        ),
        UniqueConstraint("snapshot_key", name="uq_metric_snapshot_key"),
        UniqueConstraint("import_row_id", name="uq_metric_snapshot_import_row"),
        ForeignKeyConstraint(
            ["platform_account_id", "influencer_id"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.influencer_id"],
            name="fk_metric_snapshot_account_influencer",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["import_row_id", "import_job_id"],
            ["import_rows.id", "import_rows.import_job_id"],
            name="fk_metric_snapshot_import",
            ondelete="RESTRICT",
        ),
        Index("ix_metric_snapshots_influencer_captured", "influencer_id", "captured_at"),
    )

    influencer_id: Mapped[UUID] = mapped_column(
        ForeignKey("influencers.id", ondelete="RESTRICT"), nullable=False
    )
    platform_account_id: Mapped[UUID] = mapped_column(nullable=False)
    source: Mapped[DataSource] = mapped_column(
        Enum(DataSource, name="data_source", values_callable=enum_values), nullable=False
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    import_job_id: Mapped[UUID] = mapped_column(nullable=False)
    import_row_id: Mapped[UUID] = mapped_column(nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    metrics_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_key: Mapped[str] = mapped_column(String(64), nullable=False)


class PlatformAccountSourceIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A provider-specific stable identity for a generic platform account."""

    __tablename__ = "platform_account_source_identities"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "platform",
            "external_account_id",
            name="uq_source_platform_external_account",
        ),
        UniqueConstraint(
            "platform_account_id",
            "source",
            "external_account_id",
            name="uq_account_source_external_account",
        ),
        ForeignKeyConstraint(
            ["platform_account_id", "platform"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.platform"],
            name="fk_source_identity_account_platform",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["first_import_row_id", "first_import_job_id"],
            ["import_rows.id", "import_rows.import_job_id"],
            name="fk_source_identity_first_import",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["last_import_row_id", "last_import_job_id"],
            ["import_rows.id", "import_rows.import_job_id"],
            name="fk_source_identity_last_import",
            ondelete="RESTRICT",
        ),
        Index("ix_source_identity_account", "platform_account_id", "source"),
    )

    platform_account_id: Mapped[UUID] = mapped_column(nullable=False)
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, name="platform_enum", values_callable=enum_values), nullable=False
    )
    source: Mapped[DataSource] = mapped_column(
        Enum(DataSource, name="data_source", values_callable=enum_values), nullable=False
    )
    external_account_id: Mapped[str] = mapped_column(String(160), nullable=False)
    first_import_job_id: Mapped[UUID] = mapped_column(nullable=False)
    first_import_row_id: Mapped[UUID] = mapped_column(nullable=False)
    last_import_job_id: Mapped[UUID] = mapped_column(nullable=False)
    last_import_row_id: Mapped[UUID] = mapped_column(nullable=False)

"""Department-owned refresh queues and immutable candidate snapshots."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend_core.db.base import Base
from backend_core.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from backend_core.db.types import JSON_DOCUMENT
from backend_core.influencers.enums import DataSource
from backend_core.refresh.enums import RefreshQueueItemStatus, RefreshQueueStatus


def enum_values(enum_type: type[Any]) -> list[str]:
    return [item.value for item in enum_type]


class RefreshQueue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "refresh_queues"
    __table_args__ = (
        UniqueConstraint("id", "department_id", name="uq_refresh_queue_department_pair"),
        CheckConstraint(
            "requested_limit > 0 AND today_total_limit > 0 AND refresh_limit > 0",
            name="ck_refresh_queue_limits_positive",
        ),
        CheckConstraint(
            "requested_limit <= refresh_limit AND refresh_limit <= today_total_limit",
            name="ck_refresh_queue_limit_order",
        ),
        CheckConstraint(
            "requested_limit <= 2000",
            name="ck_refresh_queue_requested_limit_max",
        ),
        CheckConstraint("policy_version >= 1", name="ck_refresh_queue_policy_version"),
        CheckConstraint(
            "(status = 'open' AND exported_at IS NULL "
            "AND completed_at IS NULL AND cancelled_at IS NULL) OR "
            "(status = 'exported' AND exported_at IS NOT NULL "
            "AND completed_at IS NULL AND cancelled_at IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL AND cancelled_at IS NULL) OR "
            "(status = 'cancelled' AND completed_at IS NULL AND cancelled_at IS NOT NULL)",
            name="ck_refresh_queue_status_timestamps",
        ),
        Index(
            "ix_refresh_queues_department_status_created",
            "department_id",
            "status",
            "created_at",
            "id",
        ),
    )

    department_id: Mapped[UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_operator_id: Mapped[UUID] = mapped_column(
        ForeignKey("operators.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[RefreshQueueStatus] = mapped_column(
        Enum(RefreshQueueStatus, name="refresh_queue_status", values_callable=enum_values),
        nullable=False,
        default=RefreshQueueStatus.OPEN,
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    today_total_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    refresh_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    criteria_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["RefreshQueueItem"]] = relationship(back_populates="queue", lazy="raise")


class RefreshQueueItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "refresh_queue_items"
    __table_args__ = (
        UniqueConstraint(
            "queue_id",
            "platform_account_id",
            "source",
            name="uq_refresh_queue_item_queue_account_source",
        ),
        ForeignKeyConstraint(
            ["queue_id", "department_id"],
            ["refresh_queues.id", "refresh_queues.department_id"],
            name="fk_refresh_queue_item_queue_department",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["platform_account_id", "influencer_id"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.influencer_id"],
            name="fk_refresh_queue_item_account_influencer",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["fulfilled_import_row_id", "fulfilled_import_job_id"],
            ["import_rows.id", "import_rows.import_job_id"],
            name="fk_refresh_queue_item_fulfilled_import",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["last_return_import_row_id", "last_return_import_job_id"],
            ["import_rows.id", "import_rows.import_job_id"],
            name="fk_refresh_queue_item_last_return_import",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "priority_tier >= 1 AND priority_tier <= 5",
            name="ck_refresh_queue_item_priority_tier",
        ),
        CheckConstraint(
            "source = 'huitun'",
            name="ck_refresh_queue_item_source_huitun",
        ),
        CheckConstraint(
            "(fulfilled_import_job_id IS NULL AND fulfilled_import_row_id IS NULL) OR "
            "(fulfilled_import_job_id IS NOT NULL AND fulfilled_import_row_id IS NOT NULL)",
            name="ck_refresh_queue_item_fulfillment_pair",
        ),
        CheckConstraint(
            "(last_return_import_job_id IS NULL AND last_return_import_row_id IS NULL) OR "
            "(last_return_import_job_id IS NOT NULL AND last_return_import_row_id IS NOT NULL)",
            name="ck_refresh_queue_item_last_return_pair",
        ),
        CheckConstraint(
            "(status IN ('fulfilled_changed', 'fulfilled_no_change') "
            "AND fulfilled_import_job_id IS NOT NULL "
            "AND fulfilled_import_row_id IS NOT NULL AND fulfilled_at IS NOT NULL) OR "
            "(status NOT IN ('fulfilled_changed', 'fulfilled_no_change') "
            "AND fulfilled_import_job_id IS NULL "
            "AND fulfilled_import_row_id IS NULL AND fulfilled_at IS NULL)",
            name="ck_refresh_queue_item_fulfillment_state",
        ),
        Index(
            "uq_refresh_queue_item_active_candidate",
            "department_id",
            "platform_account_id",
            "source",
            unique=True,
            postgresql_where=text("status IN ('pending', 'stale_return', 'unresolved')"),
            sqlite_where=text("status IN ('pending', 'stale_return', 'unresolved')"),
        ),
        Index(
            "ix_refresh_queue_items_queue_status",
            "queue_id",
            "status",
        ),
        Index(
            "ix_refresh_queue_items_queue_priority",
            "queue_id",
            "priority_tier",
            "baseline_last_observed_at",
            "influencer_id",
            "platform_account_id",
            "id",
        ),
    )

    department_id: Mapped[UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False
    )
    queue_id: Mapped[UUID] = mapped_column(nullable=False)
    influencer_id: Mapped[UUID] = mapped_column(
        ForeignKey("influencers.id", ondelete="RESTRICT"), nullable=False
    )
    platform_account_id: Mapped[UUID] = mapped_column(nullable=False)
    source: Mapped[DataSource] = mapped_column(
        Enum(DataSource, name="data_source", values_callable=enum_values), nullable=False
    )
    priority_tier: Mapped[int] = mapped_column(Integer, nullable=False)
    priority_reasons: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    identity_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    baseline_last_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    baseline_source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[RefreshQueueItemStatus] = mapped_column(
        Enum(
            RefreshQueueItemStatus,
            name="refresh_queue_item_status",
            values_callable=enum_values,
        ),
        nullable=False,
        default=RefreshQueueItemStatus.PENDING,
    )
    fulfilled_import_job_id: Mapped[UUID | None] = mapped_column(nullable=True)
    fulfilled_import_row_id: Mapped[UUID | None] = mapped_column(nullable=True)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_return_import_job_id: Mapped[UUID | None] = mapped_column(nullable=True)
    last_return_import_row_id: Mapped[UUID | None] = mapped_column(nullable=True)

    queue: Mapped[RefreshQueue] = relationship(back_populates="items", lazy="raise")

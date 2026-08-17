"""Outreach target, task, event, and template persistence only."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    desc,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend_core.db.base import Base
from backend_core.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from backend_core.db.types import JSON_DOCUMENT
from backend_core.outreach.enums import (
    MessageTemplateState,
    OutreachActorType,
    OutreachChannel,
    OutreachEventType,
    OutreachPriority,
    OutreachPrioritySource,
    OutreachTaskKind,
    OutreachTaskState,
)


def enum_values(enum_type: type[Any]) -> list[str]:
    return [item.value for item in enum_type]


class OutreachTarget(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outreach_targets"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id",
            "influencer_id",
            "channel",
            name="uq_outreach_target_campaign_influencer_channel",
        ),
        UniqueConstraint(
            "id", "campaign_id", "department_id", name="uq_outreach_target_id_campaign_department"
        ),
        ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="fk_outreach_target_department",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["campaign_id", "department_id"],
            ["campaigns.id", "campaigns.department_id"],
            name="fk_outreach_target_campaign_department",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["member_id", "campaign_id", "influencer_id"],
            [
                "campaign_members.id",
                "campaign_members.campaign_id",
                "campaign_members.influencer_id",
            ],
            name="fk_outreach_target_member_campaign_influencer",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["contact_id", "influencer_id"],
            ["influencer_contacts.id", "influencer_contacts.influencer_id"],
            name="fk_outreach_target_contact_influencer",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["platform_account_id", "influencer_id"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.influencer_id"],
            name="fk_outreach_target_account_influencer",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_outreach_target_version"),
        CheckConstraint(
            "(channel IN ('EMAIL', 'WECHAT') AND contact_id IS NOT NULL "
            "AND platform_account_id IS NULL) OR "
            "(channel IN ('XIAOHONGSHU_PRIVATE_MESSAGE', 'DOUYIN_PRIVATE_MESSAGE') "
            "AND contact_id IS NULL AND platform_account_id IS NOT NULL) OR "
            "(channel = 'MANUAL' AND ((contact_id IS NOT NULL AND platform_account_id IS NULL) "
            "OR (contact_id IS NULL AND platform_account_id IS NOT NULL)))",
            name="ck_outreach_target_channel_reference_shape",
        ),
    )

    department_id: Mapped[UUID] = mapped_column(nullable=False)
    campaign_id: Mapped[UUID] = mapped_column(nullable=False)
    member_id: Mapped[UUID] = mapped_column(nullable=False)
    influencer_id: Mapped[UUID] = mapped_column(nullable=False)
    channel: Mapped[OutreachChannel] = mapped_column(
        Enum(OutreachChannel, name="outreach_channel", values_callable=enum_values), nullable=False
    )
    contact_id: Mapped[UUID | None] = mapped_column(nullable=True)
    platform_account_id: Mapped[UUID | None] = mapped_column(nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class MessageTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "message_templates"
    __table_args__ = (
        UniqueConstraint("id", "department_id", name="uq_message_template_department_pair"),
        UniqueConstraint(
            "department_id", "name", "channel", name="uq_message_template_department_name_channel"
        ),
        ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="fk_message_template_department",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["created_by_operator_id", "department_id"],
            ["operators.id", "operators.department_id"],
            name="fk_message_template_creator_department",
            ondelete="RESTRICT",
        ),
    )

    department_id: Mapped[UUID] = mapped_column(nullable=False)
    created_by_operator_id: Mapped[UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    channel: Mapped[OutreachChannel] = mapped_column(
        Enum(OutreachChannel, name="outreach_channel", values_callable=enum_values), nullable=False
    )
    state: Mapped[MessageTemplateState] = mapped_column(
        Enum(MessageTemplateState, name="message_template_state", values_callable=enum_values),
        nullable=False,
        default=MessageTemplateState.ACTIVE,
    )
    metadata_document: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSON_DOCUMENT, nullable=True
    )


class MessageTemplateVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "message_template_versions"
    __table_args__ = (
        UniqueConstraint(
            "template_id", "version", name="uq_message_template_version_template_version"
        ),
        ForeignKeyConstraint(
            ["template_id"],
            ["message_templates.id"],
            name="fk_message_template_version_template",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["created_by_operator_id"],
            ["operators.id"],
            name="fk_message_template_version_creator",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_message_template_version_version"),
    )

    template_id: Mapped[UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    variable_schema: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_operator_id: Mapped[UUID] = mapped_column(nullable=False)


class OutreachTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outreach_tasks"
    __table_args__ = (
        UniqueConstraint("outreach_target_id", "step_key", name="uq_outreach_task_target_step_key"),
        UniqueConstraint(
            "department_id",
            "create_idempotency_key",
            name="uq_outreach_task_department_create_idempotency",
        ),
        ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="fk_outreach_task_department",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["outreach_target_id", "campaign_id", "department_id"],
            [
                "outreach_targets.id",
                "outreach_targets.campaign_id",
                "outreach_targets.department_id",
            ],
            name="fk_outreach_task_target_campaign_department",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assigned_operator_id", "department_id"],
            ["operators.id", "operators.department_id"],
            name="fk_outreach_task_assignee_department",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["template_version_id"],
            ["message_template_versions.id"],
            name="fk_outreach_task_template_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_outreach_task_version"),
        Index(
            "ix_outreach_tasks_today_queue", "department_id", "state", "priority", "due_at", "id"
        ),
        Index(
            "ix_outreach_tasks_owner_queue",
            "department_id",
            "assigned_operator_id",
            "state",
            "due_at",
            "id",
        ),
        Index("ix_outreach_tasks_campaign_queue", "campaign_id", "state", "due_at", "id"),
    )

    department_id: Mapped[UUID] = mapped_column(nullable=False)
    campaign_id: Mapped[UUID] = mapped_column(nullable=False)
    outreach_target_id: Mapped[UUID] = mapped_column(nullable=False)
    kind: Mapped[OutreachTaskKind] = mapped_column(
        Enum(OutreachTaskKind, name="outreach_task_kind", values_callable=enum_values),
        nullable=False,
    )
    step_key: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[OutreachTaskState] = mapped_column(
        Enum(OutreachTaskState, name="outreach_task_state", values_callable=enum_values),
        nullable=False,
    )
    priority: Mapped[OutreachPriority] = mapped_column(
        Enum(OutreachPriority, name="outreach_priority", values_callable=enum_values),
        nullable=False,
        default=OutreachPriority.NORMAL,
    )
    priority_source: Mapped[OutreachPrioritySource] = mapped_column(
        Enum(OutreachPrioritySource, name="outreach_priority_source", values_callable=enum_values),
        nullable=False,
        default=OutreachPrioritySource.DEFAULT,
    )
    priority_reason_codes: Mapped[list[str] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    assigned_operator_id: Mapped[UUID | None] = mapped_column(nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    template_version_id: Mapped[UUID | None] = mapped_column(nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    create_idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    create_request_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class OutreachEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outreach_events"
    __table_args__ = (
        UniqueConstraint("task_id", "idempotency_key", name="uq_outreach_event_task_idempotency"),
        ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="fk_outreach_event_department",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["campaign_id", "department_id"],
            ["campaigns.id", "campaigns.department_id"],
            name="fk_outreach_event_campaign_department",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["influencer_id"],
            ["influencers.id"],
            name="fk_outreach_event_influencer",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_id"], ["outreach_tasks.id"], name="fk_outreach_event_task", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["actor_operator_id"],
            ["operators.id"],
            name="fk_outreach_event_actor",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_outreach_events_history",
            "department_id",
            "influencer_id",
            "channel",
            desc("occurred_at"),
            desc("id"),
        ),
        Index("ix_outreach_events_task_history", "task_id", "occurred_at", "id"),
    )

    department_id: Mapped[UUID] = mapped_column(nullable=False)
    campaign_id: Mapped[UUID] = mapped_column(nullable=False)
    influencer_id: Mapped[UUID] = mapped_column(nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    channel: Mapped[OutreachChannel] = mapped_column(
        Enum(OutreachChannel, name="outreach_channel", values_callable=enum_values), nullable=False
    )
    event_type: Mapped[OutreachEventType] = mapped_column(
        Enum(OutreachEventType, name="outreach_event_type", values_callable=enum_values),
        nullable=False,
    )
    actor_type: Mapped[OutreachActorType] = mapped_column(
        Enum(OutreachActorType, name="outreach_actor_type", values_callable=enum_values),
        nullable=False,
    )
    actor_operator_id: Mapped[UUID | None] = mapped_column(nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    from_state: Mapped[OutreachTaskState | None] = mapped_column(
        Enum(OutreachTaskState, name="outreach_task_state", values_callable=enum_values),
        nullable=True,
    )
    to_state: Mapped[OutreachTaskState | None] = mapped_column(
        Enum(OutreachTaskState, name="outreach_task_state", values_callable=enum_values),
        nullable=True,
    )
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    redacted_target_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DOCUMENT, nullable=True
    )
    redacted_message_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DOCUMENT, nullable=True
    )
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSON_DOCUMENT, nullable=True
    )

"""Closed, API-independent contracts for Phase 3A Outreach operations."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from backend_core.outreach.enums import (
    OutreachActorType,
    OutreachChannel,
    OutreachEventType,
    OutreachPriority,
    OutreachPrioritySource,
    OutreachTaskKind,
    OutreachTaskState,
)

type PositiveStrictInt = Annotated[StrictInt, Field(ge=1)]
type ReasonCode = Annotated[
    StrictStr,
    Field(pattern=r"^[A-Z][A-Z0-9_]{0,79}$", min_length=1, max_length=80),
]
type StepKey = Annotated[StrictStr, Field(min_length=1, max_length=120)]
type MaskedDisplay = Annotated[StrictStr, Field(min_length=1, max_length=512)]


class OutreachWriteContract(BaseModel):
    """Strict input contract shared by Outreach domain services."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OutreachReadContract(BaseModel):
    """Allowlisted result contract that can read SQLAlchemy attributes."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class OutreachTargetCreateInput(OutreachWriteContract):
    """Endpoint reference request; Campaign identity is a service path argument."""

    department_id: UUID | None = None
    member_id: UUID
    influencer_id: UUID
    channel: OutreachChannel
    contact_id: UUID | None = None
    platform_account_id: UUID | None = None

    @model_validator(mode="after")
    def validate_channel_reference_shape(self) -> Self:
        _validate_target_reference_shape(
            channel=self.channel,
            contact_id=self.contact_id,
            platform_account_id=self.platform_account_id,
        )
        return self


class OutreachTargetUpdateInput(OutreachWriteContract):
    """CAS replacement of a Target endpoint; target identity is a service path argument."""

    department_id: UUID | None = None
    contact_id: UUID | None = None
    platform_account_id: UUID | None = None
    expected_version: PositiveStrictInt

    @model_validator(mode="after")
    def require_exactly_one_reference(self) -> Self:
        if (self.contact_id is None) == (self.platform_account_id is None):
            raise ValueError("exactly one of contact_id or platform_account_id is required")
        return self


class OutreachTaskCreateInput(OutreachWriteContract):
    """Initial Task request; Target identity and idempotency key are service arguments."""

    department_id: UUID | None = None
    kind: OutreachTaskKind = OutreachTaskKind.FIRST_TOUCH
    step_key: StepKey = "initial"
    priority: OutreachPriority = OutreachPriority.NORMAL
    priority_reason_codes: tuple[ReasonCode, ...] = ()
    assigned_operator_id: UUID | None = None
    due_at: datetime
    template_version_id: UUID | None = None

    @model_validator(mode="after")
    def validate_initial_task_contract(self) -> Self:
        if self.kind is not OutreachTaskKind.FIRST_TOUCH:
            raise ValueError("Phase 3A only creates FIRST_TOUCH Tasks")
        if self.step_key != "initial":
            raise ValueError("Phase 3A only creates the initial Task step")
        if self.priority is OutreachPriority.NORMAL and self.priority_reason_codes:
            raise ValueError("priority_reason_codes require HIGH manual priority")
        _require_timezone(self.due_at, name="due_at")
        return self


class OutreachTaskTransitionInput(OutreachWriteContract):
    """CAS state transition; Task identity and idempotency key are service arguments."""

    department_id: UUID | None = None
    to_state: OutreachTaskState
    expected_version: PositiveStrictInt
    reason_code: ReasonCode | None = None

    @model_validator(mode="after")
    def validate_transition_request(self) -> Self:
        if self.to_state is OutreachTaskState.REVIEW_REQUIRED:
            raise ValueError("REVIEW_REQUIRED is only valid when creating a Task")
        if self.to_state is OutreachTaskState.FAILED and self.reason_code is None:
            raise ValueError("reason_code is required when transitioning a Task to FAILED")
        return self


class OutreachTargetResult(OutreachReadContract):
    """Safe Target metadata with an already-masked endpoint display only."""

    id: UUID
    department_id: UUID
    campaign_id: UUID
    member_id: UUID
    influencer_id: UUID
    channel: OutreachChannel
    contact_id: UUID | None
    platform_account_id: UUID | None
    version: int
    masked_display: MaskedDisplay | None = None

    @classmethod
    def from_model(cls, model: object, *, masked_display: str | None = None) -> Self:
        return cls.model_validate(model).model_copy(update={"masked_display": masked_display})

    def to_replay_payload(self) -> dict[str, object]:
        """Return the redacted, version-1 value stored by OUTREACH_TARGET_CREATE."""

        return self.model_dump(mode="json")

    @classmethod
    def from_replay_payload(cls, payload: dict[str, object]) -> Self:
        return cls.model_validate(payload)


class OutreachTaskResult(OutreachReadContract):
    """Safe Task metadata. Idempotency keys and Event snapshots remain internal."""

    id: UUID
    department_id: UUID
    campaign_id: UUID
    outreach_target_id: UUID
    kind: OutreachTaskKind
    step_key: str
    state: OutreachTaskState
    priority: OutreachPriority
    priority_source: OutreachPrioritySource
    priority_reason_codes: tuple[str, ...] | None
    assigned_operator_id: UUID | None
    due_at: datetime
    template_version_id: UUID | None
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, model: object) -> Self:
        return cls.model_validate(model)

    @model_validator(mode="before")
    @classmethod
    def normalize_database_timestamps(cls, value: object) -> object:
        """Restore UTC metadata when SQLite drops timezone offsets in test storage."""

        if isinstance(value, Mapping):
            data = dict(value)
        else:
            data = {field_name: getattr(value, field_name) for field_name in cls.model_fields}
        for field_name in ("due_at", "created_at", "updated_at"):
            timestamp = data.get(field_name)
            if isinstance(timestamp, datetime) and (
                timestamp.tzinfo is None or timestamp.utcoffset() is None
            ):
                data[field_name] = timestamp.replace(tzinfo=UTC)
        return data

    @model_validator(mode="after")
    def require_timezone_aware_due_at(self) -> Self:
        _require_timezone(self.due_at, name="due_at")
        return self


class OutreachTransitionResult(OutreachReadContract):
    """Compact result for a Task transition or idempotent Event replay."""

    task_id: UUID
    state: OutreachTaskState
    version: int = Field(ge=1)
    event_id: UUID
    replayed: StrictBool = False


class OutreachTaskCreateResult(OutreachReadContract):
    """Task creation result with its redacted cross-Campaign history warning."""

    task: OutreachTaskResult
    history_warning: HistoryWarning | None = None


class OutreachEventResult(OutreachReadContract):
    """Safe append-only Event history; keys and message contents stay internal."""

    id: UUID
    task_id: UUID
    campaign_id: UUID
    influencer_id: UUID
    channel: OutreachChannel
    event_type: OutreachEventType
    actor_type: OutreachActorType
    actor_operator_id: UUID | None
    occurred_at: datetime
    from_state: OutreachTaskState | None
    to_state: OutreachTaskState | None
    reason_code: str | None

    @model_validator(mode="before")
    @classmethod
    def normalize_database_timestamp(cls, value: object) -> object:
        """Restore UTC metadata when SQLite drops timezone offsets in test storage."""

        if isinstance(value, Mapping):
            data = dict(value)
        else:
            data = {field_name: getattr(value, field_name) for field_name in cls.model_fields}
        occurred_at = data.get("occurred_at")
        if isinstance(occurred_at, datetime) and (
            occurred_at.tzinfo is None or occurred_at.utcoffset() is None
        ):
            data["occurred_at"] = occurred_at.replace(tzinfo=UTC)
        return data

    @model_validator(mode="after")
    def require_timezone_aware_timestamp(self) -> Self:
        _require_timezone(self.occurred_at, name="occurred_at")
        return self


class HistoryWarning(OutreachReadContract):
    """Redacted cross-Campaign contact history supplied before Task readiness."""

    channel: OutreachChannel
    last_sent_at: datetime

    @model_validator(mode="after")
    def require_timezone_aware_timestamp(self) -> Self:
        _require_timezone(self.last_sent_at, name="last_sent_at")
        return self


def _validate_target_reference_shape(
    *,
    channel: OutreachChannel,
    contact_id: UUID | None,
    platform_account_id: UUID | None,
) -> None:
    has_contact = contact_id is not None
    has_account = platform_account_id is not None

    if channel in {OutreachChannel.EMAIL, OutreachChannel.WECHAT}:
        if not has_contact or has_account:
            raise ValueError(f"{channel.value} requires exactly one contact_id")
        return
    if channel in {
        OutreachChannel.XIAOHONGSHU_PRIVATE_MESSAGE,
        OutreachChannel.DOUYIN_PRIVATE_MESSAGE,
    }:
        if has_contact or not has_account:
            raise ValueError(f"{channel.value} requires exactly one platform_account_id")
        return
    if channel is OutreachChannel.MANUAL and has_contact != has_account:
        return
    raise ValueError("MANUAL requires exactly one contact_id or platform_account_id")


def _require_timezone(value: datetime, *, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")


__all__ = [
    "HistoryWarning",
    "OutreachTargetCreateInput",
    "OutreachTargetResult",
    "OutreachTargetUpdateInput",
    "OutreachTaskCreateInput",
    "OutreachTaskCreateResult",
    "OutreachEventResult",
    "OutreachTaskResult",
    "OutreachTaskTransitionInput",
    "OutreachTransitionResult",
]

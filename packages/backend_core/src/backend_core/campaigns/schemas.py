"""Closed, API-independent contracts for Phase 3A Campaign operations."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from backend_core.growth.enums import (
    CampaignReviewMode,
    CampaignStatus,
    DuplicateHistoryPolicy,
)

type PositiveStrictInt = Annotated[StrictInt, Field(ge=1)]
type NonnegativeStrictInt = Annotated[StrictInt, Field(ge=0)]
type CampaignName = Annotated[StrictStr, Field(min_length=1, max_length=200)]


class CampaignWriteContract(BaseModel):
    """Strict inputs shared by Campaign domain services."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CampaignReadContract(BaseModel):
    """Allowlisted Campaign results that can read SQLAlchemy attributes."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class CampaignCreateInput(CampaignWriteContract):
    """Client-controlled Campaign configuration; context supplies creator ownership."""

    department_id: UUID | None = None
    owner_operator_id: UUID | None = None
    name: CampaignName
    review_mode: CampaignReviewMode = CampaignReviewMode.FIRST_N
    review_count: PositiveStrictInt | None = 50
    duplicate_history_policy: DuplicateHistoryPolicy = DuplicateHistoryPolicy.ALLOW_WITH_WARNING
    duplicate_window_days: PositiveStrictInt | None = None

    @model_validator(mode="after")
    def validate_config(self) -> Self:
        _validate_review_config(self.review_mode, self.review_count)
        _validate_duplicate_policy(
            self.duplicate_history_policy,
            self.duplicate_window_days,
        )
        return self


class CampaignUpdateInput(CampaignWriteContract):
    """Full CAS replacement of mutable Campaign configuration."""

    department_id: UUID | None = None
    name: CampaignName
    owner_operator_id: UUID
    review_mode: CampaignReviewMode
    review_count: PositiveStrictInt | None
    duplicate_history_policy: DuplicateHistoryPolicy
    duplicate_window_days: PositiveStrictInt | None
    expected_version: PositiveStrictInt

    @model_validator(mode="after")
    def validate_config(self) -> Self:
        _validate_review_config(self.review_mode, self.review_count)
        _validate_duplicate_policy(
            self.duplicate_history_policy,
            self.duplicate_window_days,
        )
        return self


class CampaignStatusTransitionInput(CampaignWriteContract):
    """CAS transition request; legal source/target pairs remain service-owned."""

    department_id: UUID | None = None
    to_status: CampaignStatus
    expected_version: PositiveStrictInt

    @model_validator(mode="after")
    def reject_initial_status(self) -> Self:
        if self.to_status is CampaignStatus.DRAFT:
            raise ValueError("DRAFT is only valid when creating a Campaign")
        return self


class CampaignMemberAddItem(CampaignWriteContract):
    """One member identity and the required preferred Campaign account."""

    influencer_id: UUID
    preferred_platform_account_id: UUID


class CampaignMemberBulkAddInput(CampaignWriteContract):
    """Set-oriented Member add request; Campaign identity is a service path argument."""

    department_id: UUID | None = None
    source_pool_run_id: UUID | None = None
    members: tuple[CampaignMemberAddItem, ...] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def reject_conflicting_duplicate_members(self) -> Self:
        """Allow exact duplicates for set semantics, but reject ambiguous account choices."""

        accounts_by_influencer: dict[UUID, UUID] = {}
        for member in self.members:
            existing_account_id = accounts_by_influencer.setdefault(
                member.influencer_id,
                member.preferred_platform_account_id,
            )
            if existing_account_id != member.preferred_platform_account_id:
                raise ValueError(
                    "one Campaign Member request cannot select multiple preferred accounts "
                    "for the same influencer"
                )
        return self


class CampaignMemberRemoveInput(CampaignWriteContract):
    """CAS soft-removal request; Campaign and Member identities are path arguments."""

    department_id: UUID | None = None
    expected_version: PositiveStrictInt


class CampaignResult(CampaignReadContract):
    """Safe Campaign result and version-1 create replay payload."""

    id: UUID
    department_id: UUID
    owner_operator_id: UUID
    created_by_operator_id: UUID
    name: str
    status: CampaignStatus
    review_mode: CampaignReviewMode
    review_count: int | None
    duplicate_history_policy: DuplicateHistoryPolicy
    duplicate_window_days: int | None
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, model: object) -> Self:
        return cls.model_validate(model)

    def to_replay_payload(self) -> dict[str, Any]:
        """Return the redacted, versioned value stored by CAMPAIGN_CREATE."""

        return self.model_dump(mode="json")

    @classmethod
    def from_replay_payload(cls, payload: Mapping[str, object]) -> Self:
        return cls.model_validate(payload)


class CampaignMemberBulkAddResult(CampaignReadContract):
    """Exact schema-version-1 A1 replay payload for a Member bulk add."""

    campaign_id: UUID
    source_pool_run_id: UUID | None
    requested_count: NonnegativeStrictInt
    added_count: NonnegativeStrictInt
    restored_count: NonnegativeStrictInt
    already_active_count: NonnegativeStrictInt
    active_count_after: NonnegativeStrictInt

    @model_validator(mode="after")
    def validate_outcome_counts(self) -> Self:
        if (
            self.added_count + self.restored_count + self.already_active_count
            != self.requested_count
        ):
            raise ValueError(
                "added_count + restored_count + already_active_count must equal requested_count"
            )
        return self

    def to_replay_payload(self) -> dict[str, Any]:
        """Return exactly the seven allowlisted keys constrained by A1."""

        return self.model_dump(mode="json")

    @classmethod
    def from_replay_payload(cls, payload: Mapping[str, object]) -> Self:
        return cls.model_validate(payload)


class CampaignMemberResult(CampaignReadContract):
    """Safe Member metadata; no Contact or account display values are included."""

    id: UUID
    department_id: UUID
    campaign_id: UUID
    influencer_id: UUID
    preferred_platform_account_id: UUID
    source_pool_run_id: UUID | None
    added_by_operator_id: UUID
    removed_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, model: object) -> Self:
        return cls.model_validate(model)


def _validate_review_config(
    review_mode: CampaignReviewMode,
    review_count: int | None,
) -> None:
    if review_mode is CampaignReviewMode.FIRST_N:
        if review_count is None:
            raise ValueError("review_count is required for FIRST_N review mode")
    elif review_count is not None:
        raise ValueError("review_count is only allowed for FIRST_N review mode")


def _validate_duplicate_policy(
    policy: DuplicateHistoryPolicy,
    window_days: int | None,
) -> None:
    if policy is DuplicateHistoryPolicy.BLOCK_WITHIN_WINDOW:
        if window_days is None:
            raise ValueError("duplicate_window_days is required for BLOCK_WITHIN_WINDOW")
    elif window_days is not None:
        raise ValueError("duplicate_window_days is only allowed for BLOCK_WITHIN_WINDOW")


__all__ = [
    "CampaignCreateInput",
    "CampaignMemberAddItem",
    "CampaignMemberBulkAddInput",
    "CampaignMemberBulkAddResult",
    "CampaignMemberRemoveInput",
    "CampaignMemberResult",
    "CampaignResult",
    "CampaignStatusTransitionInput",
    "CampaignUpdateInput",
]

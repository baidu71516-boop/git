"""Closed, API-independent contracts for Phase 3A Campaign operations."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from backend_core.auth.enums import OperatorStatus
from backend_core.growth.enums import (
    CampaignReviewMode,
    CampaignStatus,
    DuplicateHistoryPolicy,
)
from backend_core.influencers.schemas import (
    InfluencerIdentitySummary,
    PlatformAccountIdentitySummary,
)

type PositiveStrictInt = Annotated[StrictInt, Field(ge=1)]
type NonnegativeStrictInt = Annotated[StrictInt, Field(ge=0)]
type CampaignName = Annotated[StrictStr, Field(min_length=1, max_length=200)]


class CampaignMemberProjectionModel(Protocol):
    """ORM attributes required to build the public Member read projection."""

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
    """Direct Member add request; Campaign identity is a service path argument.

    ``source_pool_run_id`` remains in this internal input shape for callers
    compiled against the WO3 domain contract.  The Phase 3A direct path does
    not accept provenance; the service rejects a non-null legacy value and
    callers must use ``CampaignMemberFromCandidateRunBulkAddInput`` instead.
    The closed HTTP DTO never exposes this compatibility field.
    """

    department_id: UUID | None = None
    source_pool_run_id: UUID | None = None
    members: tuple[CampaignMemberAddItem, ...] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def reject_duplicate_influencers(self) -> Self:
        """A direct request must select each Influencer exactly once."""

        influencer_ids: set[UUID] = set()
        for member in self.members:
            if member.influencer_id in influencer_ids:
                raise ValueError(
                    "one Campaign Member request cannot contain duplicate influencer_id values"
                )
            influencer_ids.add(member.influencer_id)
        return self


class CampaignMemberFromCandidateRunBulkAddInput(CampaignWriteContract):
    """One explicit selection or all persisted MATCH rows from a completed run.

    The original explicit shape deliberately remains byte-for-byte compatible:
    ``{run_id, member_ids}``.  ``ALL_MATCH`` is a strict alternative rather
    than a mixed selection mode, so an all-MATCH request can never quietly add
    a selected UNKNOWN row.
    """

    department_id: UUID | None = None
    run_id: UUID
    selection_mode: Literal["ALL_MATCH"] | None = None
    member_ids: tuple[UUID, ...] | None = Field(default=None, min_length=1, max_length=10_000)
    excluded_member_ids: tuple[UUID, ...] | None = Field(default=None, max_length=10_000)

    @model_validator(mode="after")
    def require_exactly_one_selection_shape(self) -> Self:
        if self.selection_mode == "ALL_MATCH":
            if self.member_ids is not None or self.excluded_member_ids is None:
                raise ValueError(
                    "ALL_MATCH requires excluded_member_ids and does not accept member_ids"
                )
            if len(set(self.excluded_member_ids)) != len(self.excluded_member_ids):
                raise ValueError("excluded_member_ids must be distinct")
            return self

        if self.member_ids is None or self.excluded_member_ids is not None:
            raise ValueError(
                "Explicit selection requires member_ids and does not accept excluded_member_ids"
            )
        if len(set(self.member_ids)) != len(self.member_ids):
            raise ValueError("member_ids must be distinct")
        return self


class CampaignMemberRemoveInput(CampaignWriteContract):
    """CAS soft-removal request; Campaign and Member identities are path arguments."""

    department_id: UUID | None = None
    expected_version: PositiveStrictInt


class CampaignOwnerSummary(CampaignReadContract):
    """Canonical, Department-local business-owner display projection."""

    id: UUID
    name: str
    status: OperatorStatus


class CampaignResult(CampaignReadContract):
    """Safe Campaign result and version-1 create replay payload."""

    id: UUID
    department_id: UUID
    owner_operator_id: UUID
    owner: CampaignOwnerSummary
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

    @model_validator(mode="after")
    def require_matching_owner_projection(self) -> Self:
        if self.owner.id != self.owner_operator_id:
            raise ValueError("owner.id must match owner_operator_id")
        return self

    @classmethod
    def from_model(cls, model: object, *, owner: CampaignOwnerSummary) -> Self:
        """Build the closed response from the Campaign and its joined owner."""

        return cls.model_validate(
            {
                field_name: owner if field_name == "owner" else getattr(model, field_name)
                for field_name in cls.model_fields
            }
        )

    def to_replay_payload(self) -> dict[str, Any]:
        """Return the redacted, versioned value stored by CAMPAIGN_CREATE."""

        return self.model_dump(mode="json")

    @classmethod
    def from_replay_payload(
        cls,
        payload: Mapping[str, object],
        *,
        owner: CampaignOwnerSummary,
    ) -> Self:
        """Enrich legacy create-replay payloads that predate owner projection."""

        return cls.model_validate({**payload, "owner": owner})


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
    """Safe Member metadata with canonical identity display projections only."""

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
    influencer: InfluencerIdentitySummary
    preferred_platform_account: PlatformAccountIdentitySummary
    is_active: bool

    @classmethod
    def from_projection(
        cls,
        model: CampaignMemberProjectionModel,
        *,
        influencer: InfluencerIdentitySummary,
        preferred_platform_account: PlatformAccountIdentitySummary,
    ) -> Self:
        return cls.model_validate(
            {
                "id": model.id,
                "department_id": model.department_id,
                "campaign_id": model.campaign_id,
                "influencer_id": model.influencer_id,
                "preferred_platform_account_id": model.preferred_platform_account_id,
                "source_pool_run_id": model.source_pool_run_id,
                "added_by_operator_id": model.added_by_operator_id,
                "removed_at": model.removed_at,
                "version": model.version,
                "created_at": model.created_at,
                "updated_at": model.updated_at,
                "influencer": influencer,
                "preferred_platform_account": preferred_platform_account,
                "is_active": model.removed_at is None,
            }
        )

    @model_validator(mode="after")
    def require_consistent_display_projection(self) -> Self:
        if self.influencer.id != self.influencer_id:
            raise ValueError("influencer.id must match influencer_id")
        if self.preferred_platform_account.id != self.preferred_platform_account_id:
            raise ValueError(
                "preferred_platform_account.id must match preferred_platform_account_id"
            )
        if self.is_active != (self.removed_at is None):
            raise ValueError("is_active must match whether removed_at is null")
        return self


class CampaignCursor(CampaignReadContract):
    """The immutable tuple for the Campaign list keyset order."""

    updated_at: datetime
    id: UUID


class CampaignMemberCursor(CampaignReadContract):
    """A Member-list keyset tuple bound to its resolved scope and Campaign."""

    created_at: datetime
    id: UUID
    department_id: UUID
    campaign_id: UUID


class CampaignPage(CampaignReadContract):
    """Campaign page with an API-adaptable typed keyset continuation."""

    items: tuple[CampaignResult, ...]
    next_cursor: CampaignCursor | None = None


class CampaignMemberPage(CampaignReadContract):
    """Active Campaign Member page with an API-adaptable typed continuation."""

    items: tuple[CampaignMemberResult, ...]
    next_cursor: CampaignMemberCursor | None = None


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
    "CampaignCursor",
    "CampaignMemberAddItem",
    "CampaignMemberBulkAddInput",
    "CampaignMemberBulkAddResult",
    "CampaignMemberCursor",
    "CampaignMemberFromCandidateRunBulkAddInput",
    "CampaignMemberPage",
    "CampaignMemberRemoveInput",
    "CampaignMemberResult",
    "CampaignPage",
    "CampaignResult",
    "CampaignStatusTransitionInput",
    "CampaignUpdateInput",
]

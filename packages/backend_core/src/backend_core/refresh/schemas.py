"""Closed Pydantic contracts for refresh queue snapshots and API payloads."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from backend_core.influencers.enums import DataSource, Platform
from backend_core.influencers.freshness import FreshnessPolicy, FreshnessStatus
from backend_core.refresh.enums import (
    RefreshPriorityReason,
    RefreshQueueItemStatus,
    RefreshQueueStatus,
)

SCHEMA_VERSION = 1
MAX_REQUESTED_LIMIT = 2_000
MAX_PAGE_LIMIT = 200
# Python's complete Unicode whitespace set, made explicit so SQL candidate
# eligibility and snapshot validation use exactly the same definition.
IDENTITY_WHITESPACE = (
    "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)

type NonnegativeStrictInt = Annotated[StrictInt, Field(ge=0)]
type PositiveStrictInt = Annotated[StrictInt, Field(gt=0)]
type RequestedLimit = Annotated[StrictInt, Field(gt=0, le=MAX_REQUESTED_LIMIT)]
type PageLimit = Annotated[StrictInt, Field(gt=0, le=MAX_PAGE_LIMIT)]
type PriorityTier = Annotated[StrictInt, Field(ge=1, le=5)]

type CandidateConditions = tuple[
    Literal["influencer_active_not_deleted"],
    Literal["platform_account_active"],
    Literal["huitun_evidence_required"],
    Literal["exportable_identity_required"],
    Literal["department_active_queue_excluded"],
]
type IdentityLocatorFields = tuple[
    Literal["platform_account_id"],
    Literal["profile_url"],
    Literal["account_handle"],
    Literal["account_name"],
    Literal["external_source_id"],
]
type SelectionSort = tuple[
    Literal["priority_tier ASC"],
    Literal["baseline_last_observed_at ASC NULLS FIRST"],
    Literal["influencer_id ASC"],
    Literal["platform_account_id ASC"],
]


class FrozenRefreshContract(BaseModel):
    """Immutable, closed JSON contract used for persisted snapshots."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class RefreshReadContract(BaseModel):
    """Closed response contract that can read SQLAlchemy domain attributes."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class RefreshQueueCreateInput(BaseModel):
    """The only client-controlled inputs used to create a refresh queue."""

    model_config = ConfigDict(extra="forbid")

    department_id: UUID | None = None
    requested_limit: RequestedLimit
    today_total_limit: PositiveStrictInt
    refresh_limit: PositiveStrictInt

    @model_validator(mode="after")
    def validate_limit_order(self) -> RefreshQueueCreateInput:
        if self.requested_limit > self.refresh_limit:
            raise ValueError("requested_limit must not exceed refresh_limit")
        if self.refresh_limit > self.today_total_limit:
            raise ValueError("refresh_limit must not exceed today_total_limit")
        return self


class IdentitySnapshot(FrozenRefreshContract):
    """Public lookup identity frozen at queue creation; contacts are impossible here.

    ``followers_count`` is the one permitted non-identity business scalar. It is
    frozen beside the identity so repeated exports never read mutable live metrics.
    """

    schema_version: Literal[1] = 1
    platform: Platform
    account_name: StrictStr | None = None
    platform_account_id: StrictStr | None = None
    account_handle: StrictStr | None = None
    profile_url: StrictStr | None = None
    external_source_id: StrictStr | None = None
    followers_count: NonnegativeStrictInt | None = None

    @model_validator(mode="after")
    def require_exportable_locator(self) -> IdentitySnapshot:
        locators = (
            self.platform_account_id,
            self.profile_url,
            self.account_handle,
            self.account_name,
            self.external_source_id,
        )
        if not any(value is not None and value.strip(IDENTITY_WHITESPACE) for value in locators):
            raise ValueError("at least one nonblank exportable identity field is required")
        return self


class FreshnessThresholdsSnapshot(FrozenRefreshContract):
    """Elapsed-time thresholds copied from one validated ``FreshnessPolicy``."""

    fresh_duration_seconds: NonnegativeStrictInt
    aging_duration_seconds: PositiveStrictInt
    stale_duration_seconds: PositiveStrictInt

    @model_validator(mode="after")
    def validate_threshold_order(self) -> FreshnessThresholdsSnapshot:
        if not (
            self.fresh_duration_seconds < self.aging_duration_seconds < self.stale_duration_seconds
        ):
            raise ValueError("freshness thresholds must be strictly increasing")
        return self

    @classmethod
    def from_policy(cls, policy: FreshnessPolicy) -> FreshnessThresholdsSnapshot:
        return cls(
            fresh_duration_seconds=_whole_seconds(policy.fresh_duration),
            aging_duration_seconds=_whole_seconds(policy.aging_duration),
            stale_duration_seconds=_whole_seconds(policy.stale_duration),
        )


class CandidateRulesSnapshot(FrozenRefreshContract):
    """The exact frozen MVP eligibility rules, represented as stable codes."""

    granularity: Literal["platform_account_source"] = "platform_account_source"
    source: Literal[DataSource.HUITUN] = DataSource.HUITUN
    required_conditions: CandidateConditions = (
        "influencer_active_not_deleted",
        "platform_account_active",
        "huitun_evidence_required",
        "exportable_identity_required",
        "department_active_queue_excluded",
    )
    identity_any_of: IdentityLocatorFields = (
        "platform_account_id",
        "profile_url",
        "account_handle",
        "account_name",
        "external_source_id",
    )
    occupied_item_statuses: tuple[RefreshQueueItemStatus, ...] = (
        RefreshQueueItemStatus.PENDING,
        RefreshQueueItemStatus.STALE_RETURN,
        RefreshQueueItemStatus.UNRESOLVED,
    )

    @field_validator("occupied_item_statuses")
    @classmethod
    def require_exact_occupied_statuses(
        cls,
        value: tuple[RefreshQueueItemStatus, ...],
    ) -> tuple[RefreshQueueItemStatus, ...]:
        expected = (
            RefreshQueueItemStatus.PENDING,
            RefreshQueueItemStatus.STALE_RETURN,
            RefreshQueueItemStatus.UNRESOLVED,
        )
        if value != expected:
            raise ValueError("occupied_item_statuses must match the frozen active statuses")
        return value


class PriorityTierRuleSnapshot(FrozenRefreshContract):
    tier: PriorityTier
    freshness_status: FreshnessStatus
    followers_missing_required: bool = False
    reason: RefreshPriorityReason


_FROZEN_PRIORITY_TIERS = (
    PriorityTierRuleSnapshot(
        tier=1,
        freshness_status=FreshnessStatus.UNKNOWN,
        reason=RefreshPriorityReason.FRESHNESS_UNKNOWN,
    ),
    PriorityTierRuleSnapshot(
        tier=2,
        freshness_status=FreshnessStatus.VERY_STALE,
        reason=RefreshPriorityReason.VERY_STALE,
    ),
    PriorityTierRuleSnapshot(
        tier=3,
        freshness_status=FreshnessStatus.STALE,
        reason=RefreshPriorityReason.STALE,
    ),
    PriorityTierRuleSnapshot(
        tier=4,
        freshness_status=FreshnessStatus.AGING,
        reason=RefreshPriorityReason.AGING,
    ),
    PriorityTierRuleSnapshot(
        tier=5,
        freshness_status=FreshnessStatus.FRESH,
        followers_missing_required=True,
        reason=RefreshPriorityReason.FOLLOWERS_MISSING,
    ),
)


class PriorityRulesSnapshot(FrozenRefreshContract):
    """Exact tier mapping plus the stable secondary-reason rule."""

    tiers: tuple[PriorityTierRuleSnapshot, ...] = _FROZEN_PRIORITY_TIERS
    append_followers_missing_reason: Literal[True] = True

    @field_validator("tiers")
    @classmethod
    def require_frozen_tiers(
        cls,
        value: tuple[PriorityTierRuleSnapshot, ...],
    ) -> tuple[PriorityTierRuleSnapshot, ...]:
        if value != _FROZEN_PRIORITY_TIERS:
            raise ValueError("priority tiers must match the frozen deterministic policy")
        return value


class CriteriaSnapshot(FrozenRefreshContract):
    """Server-generated record of every selection input and fixed rule."""

    schema_version: Literal[1] = 1
    policy_version: Literal[1] = 1
    requested_limit: RequestedLimit
    today_total_limit: PositiveStrictInt
    refresh_limit: PositiveStrictInt
    suggested_new_acquisition: NonnegativeStrictInt
    freshness_thresholds: FreshnessThresholdsSnapshot
    candidate_rules: CandidateRulesSnapshot = Field(default_factory=CandidateRulesSnapshot)
    priority_rules: PriorityRulesSnapshot = Field(default_factory=PriorityRulesSnapshot)
    sort: SelectionSort = (
        "priority_tier ASC",
        "baseline_last_observed_at ASC NULLS FIRST",
        "influencer_id ASC",
        "platform_account_id ASC",
    )

    @model_validator(mode="after")
    def validate_limits(self) -> CriteriaSnapshot:
        if self.requested_limit > self.refresh_limit:
            raise ValueError("requested_limit must not exceed refresh_limit")
        if self.refresh_limit > self.today_total_limit:
            raise ValueError("refresh_limit must not exceed today_total_limit")
        expected_new = self.today_total_limit - self.refresh_limit
        if self.suggested_new_acquisition != expected_new:
            raise ValueError(
                "suggested_new_acquisition must equal today_total_limit - refresh_limit"
            )
        return self

    @classmethod
    def from_policy(
        cls,
        *,
        create_input: RefreshQueueCreateInput,
        policy: FreshnessPolicy,
    ) -> CriteriaSnapshot:
        """Freeze one validated request using thresholds from the sole policy source."""

        return cls(
            requested_limit=create_input.requested_limit,
            today_total_limit=create_input.today_total_limit,
            refresh_limit=create_input.refresh_limit,
            suggested_new_acquisition=(create_input.today_total_limit - create_input.refresh_limit),
            freshness_thresholds=FreshnessThresholdsSnapshot.from_policy(policy),
        )


class RefreshQueueListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offset: NonnegativeStrictInt = 0
    limit: PageLimit = 50


class RefreshQueueItemListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offset: NonnegativeStrictInt = 0
    limit: PageLimit = 50


class RefreshQueuePublic(RefreshReadContract):
    id: UUID
    department_id: UUID
    created_by_operator_id: UUID
    status: RefreshQueueStatus
    as_of: datetime
    requested_limit: RequestedLimit
    today_total_limit: PositiveStrictInt
    refresh_limit: PositiveStrictInt
    policy_version: PositiveStrictInt
    criteria_snapshot: CriteriaSnapshot
    created_at: datetime
    updated_at: datetime
    exported_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None

    @field_validator("as_of")
    @classmethod
    def require_utc_as_of(cls, value: datetime) -> datetime:
        return _as_utc(value, name="as_of")


class RefreshQueueItemPublic(RefreshReadContract):
    id: UUID
    department_id: UUID
    queue_id: UUID
    influencer_id: UUID
    platform_account_id: UUID
    source: Literal[DataSource.HUITUN]
    priority_tier: PriorityTier
    priority_reasons: tuple[RefreshPriorityReason, ...]
    identity_snapshot: IdentitySnapshot
    baseline_last_observed_at: datetime | None = None
    baseline_source_updated_at: datetime | None = None
    status: RefreshQueueItemStatus
    fulfilled_import_job_id: UUID | None = None
    fulfilled_import_row_id: UUID | None = None
    fulfilled_at: datetime | None = None
    last_return_import_job_id: UUID | None = None
    last_return_import_row_id: UUID | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("baseline_last_observed_at", "baseline_source_updated_at")
    @classmethod
    def normalize_optional_baseline(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _as_utc(value, name="baseline timestamp")

    @model_validator(mode="after")
    def validate_priority_reasons(self) -> RefreshQueueItemPublic:
        expected_primary = {
            1: RefreshPriorityReason.FRESHNESS_UNKNOWN,
            2: RefreshPriorityReason.VERY_STALE,
            3: RefreshPriorityReason.STALE,
            4: RefreshPriorityReason.AGING,
            5: RefreshPriorityReason.FOLLOWERS_MISSING,
        }[self.priority_tier]
        if not self.priority_reasons or self.priority_reasons[0] is not expected_primary:
            raise ValueError("priority_reasons must start with the tier's stable primary reason")
        if len(set(self.priority_reasons)) != len(self.priority_reasons):
            raise ValueError("priority_reasons must not contain duplicates")
        allowed = (
            (expected_primary,)
            if self.priority_tier == 5
            else (
                (expected_primary,),
                (expected_primary, RefreshPriorityReason.FOLLOWERS_MISSING),
            )
        )
        if self.priority_tier == 5:
            if self.priority_reasons != allowed:
                raise ValueError("tier 5 must contain only FOLLOWERS_MISSING")
        elif self.priority_reasons not in allowed:
            raise ValueError("FOLLOWERS_MISSING may only follow the primary reason")
        return self


class RefreshQueueSummary(RefreshReadContract):
    requested: RequestedLimit
    selected: NonnegativeStrictInt
    unique_influencers: NonnegativeStrictInt
    freshness_breakdown: dict[FreshnessStatus, NonnegativeStrictInt]
    priority_breakdown: dict[PriorityTier, NonnegativeStrictInt]
    status_breakdown: dict[RefreshQueueItemStatus, NonnegativeStrictInt]

    @model_validator(mode="after")
    def validate_counts(self) -> RefreshQueueSummary:
        if self.selected > self.requested:
            raise ValueError("selected must not exceed requested")
        if self.unique_influencers > self.selected:
            raise ValueError("unique_influencers must not exceed selected")
        for name, breakdown in (
            ("freshness_breakdown", self.freshness_breakdown),
            ("priority_breakdown", self.priority_breakdown),
            ("status_breakdown", self.status_breakdown),
        ):
            if sum(breakdown.values()) != self.selected:
                raise ValueError(f"{name} must sum to selected")
        return self


class RefreshQueueListPage(RefreshReadContract):
    items: list[RefreshQueuePublic] = Field(default_factory=list)
    total: NonnegativeStrictInt
    offset: NonnegativeStrictInt
    limit: PageLimit


class RefreshQueueItemPage(RefreshReadContract):
    items: list[RefreshQueueItemPublic] = Field(default_factory=list)
    total: NonnegativeStrictInt
    offset: NonnegativeStrictInt
    limit: PageLimit


class RefreshQueueDetailResponse(RefreshReadContract):
    queue: RefreshQueuePublic
    summary: RefreshQueueSummary


def _whole_seconds(duration: timedelta) -> int:
    seconds = duration.total_seconds()
    if not seconds.is_integer():
        raise ValueError("freshness policy durations must use whole seconds")
    return int(seconds)


def _as_utc(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


__all__ = [
    "MAX_PAGE_LIMIT",
    "MAX_REQUESTED_LIMIT",
    "IDENTITY_WHITESPACE",
    "SCHEMA_VERSION",
    "CandidateRulesSnapshot",
    "CriteriaSnapshot",
    "FreshnessThresholdsSnapshot",
    "IdentitySnapshot",
    "PriorityRulesSnapshot",
    "PriorityTierRuleSnapshot",
    "RefreshQueueCreateInput",
    "RefreshQueueDetailResponse",
    "RefreshQueueItemListQuery",
    "RefreshQueueItemPage",
    "RefreshQueueItemPublic",
    "RefreshQueueListPage",
    "RefreshQueueListQuery",
    "RefreshQueuePublic",
    "RefreshQueueSummary",
]

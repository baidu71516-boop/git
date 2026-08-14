"""API-independent read contracts for the Phase 1C influencer library."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from backend_core.auth.enums import OperatorStatus
from backend_core.influencers.enums import (
    ContactType,
    ContactValidationStatus,
    CRMStage,
    DataSource,
    InfluencerStatus,
    Platform,
)
from backend_core.influencers.freshness import FreshnessStatus

type MetricScalar = StrictStr | StrictInt | StrictBool | None
type MetricValue = MetricScalar | list[MetricValue] | dict[str, MetricValue]
type MetricsDocument = dict[str, MetricValue]


def validate_query_integer(value: object) -> object:
    """Accept integer query strings without silently truncating decimal inputs."""

    if value is None or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    if isinstance(value, str):
        candidate = value.strip()
        unsigned = candidate[1:] if candidate[:1] in {"+", "-"} else candidate
        if unsigned and unsigned.isascii() and unsigned.isdigit():
            return candidate
    raise ValueError("value must be an integer")


type QueryInteger = Annotated[int, BeforeValidator(validate_query_integer)]


def validate_query_boolean(value: object) -> bool:
    """Accept only the two lowercase HTTP spellings, plus native Python booleans."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value == "true":
            return True
        if value == "false":
            return False
    raise ValueError("value must be either 'true' or 'false'")


type QueryBoolean = Annotated[bool, BeforeValidator(validate_query_boolean)]


def validate_query_datetime(value: object) -> datetime:
    """Require an ISO datetime with an offset and normalize it to UTC."""

    if isinstance(value, datetime):
        candidate = value
    elif isinstance(value, str):
        try:
            candidate = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("value must be an ISO 8601 datetime") from exc
    else:
        raise ValueError("value must be a datetime")

    if candidate.tzinfo is None or candidate.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return candidate.astimezone(UTC)


type QueryDateTime = Annotated[datetime, BeforeValidator(validate_query_datetime)]


class QueryContract(BaseModel):
    """Closed input contract shared without an HTTP framework dependency."""

    model_config = ConfigDict(extra="forbid")


class ReadContract(BaseModel):
    """Closed response contract that may be populated from domain attributes."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class InfluencerListQuery(QueryContract):
    q: str | None = Field(default=None, max_length=160)
    tag: str | None = Field(default=None, max_length=160)
    followers_min: QueryInteger | None = Field(default=None, ge=0)
    followers_max: QueryInteger | None = Field(default=None, ge=0)
    owner_operator_id: UUID | None = None
    crm_stage: CRMStage | None = None
    freshness_status: FreshnessStatus | None = None
    requires_refresh: QueryBoolean | None = None
    last_huitun_observed_before: QueryDateTime | None = None
    last_huitun_observed_after: QueryDateTime | None = None
    page: QueryInteger = Field(default=1, ge=1)
    page_size: QueryInteger = Field(default=50, ge=1, le=100)

    @field_validator("q", "tag", mode="before")
    @classmethod
    def trim_query_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("q", "tag")
    @classmethod
    def blank_query_text_is_missing(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def validate_follower_range(self) -> InfluencerListQuery:
        if (
            self.followers_min is not None
            and self.followers_max is not None
            and self.followers_min > self.followers_max
        ):
            raise ValueError("followers_min must not exceed followers_max")
        return self

    @model_validator(mode="after")
    def validate_observed_range(self) -> InfluencerListQuery:
        if (
            self.last_huitun_observed_after is not None
            and self.last_huitun_observed_before is not None
            and self.last_huitun_observed_after > self.last_huitun_observed_before
        ):
            raise ValueError(
                "last_huitun_observed_after must not exceed last_huitun_observed_before"
            )
        return self


class OwnerSummary(ReadContract):
    id: UUID
    name: str
    status: OperatorStatus


class PlatformAccountSummary(ReadContract):
    id: UUID
    platform: Platform
    platform_account_id: str | None
    account_name: str
    account_handle: str | None
    profile_url: str | None
    source: DataSource
    is_active: bool
    source_tags: list[str] = Field(default_factory=list)
    last_huitun_observed_at: datetime | None = None
    last_huitun_imported_at: datetime | None = None
    freshness_status: FreshnessStatus | None = None
    freshness_age_days: StrictInt | None = Field(default=None, ge=0)
    requires_refresh: bool = False


class CurrentMetricsSummary(ReadContract):
    platform_account_id: UUID
    source: DataSource
    source_updated_at: datetime | None = None
    followers_count: StrictInt | None = Field(default=None, ge=0)


class CurrentContactSummary(ReadContract):
    id: UUID
    type: ContactType
    display_value: str
    source: DataSource
    validation_status: ContactValidationStatus
    possible_duplicate_contact: bool


class InfluencerListItem(ReadContract):
    id: UUID
    display_name: str
    status: InfluencerStatus
    crm_stage: CRMStage
    owner: OwnerSummary | None = None
    platform_accounts: list[PlatformAccountSummary] = Field(default_factory=list)
    current_metrics: list[CurrentMetricsSummary] = Field(default_factory=list)
    current_contacts: list[CurrentContactSummary] = Field(default_factory=list)
    possible_duplicate_contact: bool = False
    freshness_status: FreshnessStatus = FreshnessStatus.UNKNOWN
    requires_refresh: bool = False
    created_at: datetime
    updated_at: datetime


class InfluencerListPage(ReadContract):
    items: list[InfluencerListItem] = Field(default_factory=list)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class PlatformAccountDetail(PlatformAccountSummary):
    bio: str | None = None
    gender: str | None = None
    region_raw: str | None = None
    verification_info: str | None = None
    mcn_name: str | None = None
    creator_level: str | None = None
    is_brand_partner: bool | None = None


class InfluencerContactDetail(ReadContract):
    id: UUID
    platform_account_id: UUID | None
    type: ContactType
    display_value: str
    source: DataSource
    validation_status: ContactValidationStatus
    is_current: bool
    possible_duplicate_contact: bool
    first_seen_at: datetime
    last_seen_at: datetime
    source_updated_at: datetime | None = None
    first_import_job_id: UUID | None = None
    first_import_row_id: UUID | None = None
    last_import_job_id: UUID | None = None
    last_import_row_id: UUID | None = None


class SourceStateDetail(ReadContract):
    platform_account_id: UUID
    source: DataSource
    source_updated_at: datetime | None = None
    state_version: int = Field(ge=1)
    creator_tags: list[str] = Field(default_factory=list)
    last_import_job_id: UUID
    last_import_row_id: UUID


class SourceIdentityDetail(ReadContract):
    id: UUID
    platform_account_id: UUID
    platform: Platform
    source: DataSource
    external_account_id: str
    first_import_job_id: UUID
    first_import_row_id: UUID
    last_import_job_id: UUID
    last_import_row_id: UUID


class CurrentMetricsDetail(ReadContract):
    platform_account_id: UUID
    source: DataSource
    source_updated_at: datetime | None = None
    metrics: MetricsDocument
    last_import_job_id: UUID
    last_import_row_id: UUID


class InfluencerDetail(ReadContract):
    id: UUID
    display_name: str
    status: InfluencerStatus
    crm_stage: CRMStage
    owner: OwnerSummary | None = None
    created_at: datetime
    updated_at: datetime
    platform_accounts: list[PlatformAccountDetail] = Field(default_factory=list)
    contacts: list[InfluencerContactDetail] = Field(default_factory=list)
    source_states: list[SourceStateDetail] = Field(default_factory=list)
    source_identities: list[SourceIdentityDetail] = Field(default_factory=list)
    current_metrics: list[CurrentMetricsDetail] = Field(default_factory=list)
    freshness_status: FreshnessStatus = FreshnessStatus.UNKNOWN
    requires_refresh: bool = False


class MetricSnapshotItem(ReadContract):
    id: UUID
    platform_account_id: UUID
    source: DataSource
    source_updated_at: datetime | None = None
    captured_at: datetime
    metrics: MetricsDocument
    import_job_id: UUID
    import_row_id: UUID


class MetricSnapshotPage(ReadContract):
    items: list[MetricSnapshotItem] = Field(default_factory=list)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class InfluencerFilterOptions(ReadContract):
    owners: list[OwnerSummary] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    crm_stages: list[CRMStage] = Field(default_factory=list)


__all__ = [
    "CurrentContactSummary",
    "CurrentMetricsDetail",
    "CurrentMetricsSummary",
    "InfluencerContactDetail",
    "InfluencerDetail",
    "InfluencerFilterOptions",
    "InfluencerListItem",
    "InfluencerListPage",
    "InfluencerListQuery",
    "MetricScalar",
    "MetricSnapshotItem",
    "MetricSnapshotPage",
    "MetricValue",
    "MetricsDocument",
    "OwnerSummary",
    "PlatformAccountDetail",
    "PlatformAccountSummary",
    "SourceIdentityDetail",
    "SourceStateDetail",
]

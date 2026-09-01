"""Closed deterministic targeting contracts and pure evaluators for Candidate Pools.

This module has no database or provider dependency.  Repositories assemble only
canonical, non-sensitive facts, then materializers persist the resulting
MATCH/UNKNOWN evaluations.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    TypeAdapter,
    field_validator,
    model_validator,
)

from backend_core.content_activity.enums import (
    ContentActivityCoverageStatus,
    ContentActivityObservationStatus,
    ContentActivityResult,
)
from backend_core.growth.enums import BuyerLeadTier
from backend_core.imports.hashing import canonical_value, hash_document
from backend_core.influencers.enums import ContactFilter, ContactType, DataSource, Platform
from backend_core.influencers.freshness import (
    ContentActivityFreshnessPolicy,
    FreshnessStatus,
    GreyDolphinActivityFreshnessPolicy,
)


class FrozenTargetingContract(BaseModel):
    """Strict, immutable documents that may be persisted as targeting policy JSON."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class TargetingEvaluationResult(StrEnum):
    MATCH = "MATCH"
    NOT_MATCH = "NOT_MATCH"
    UNKNOWN = "UNKNOWN"


class BuyerCategoryRelation(StrEnum):
    """Buyer-only category detail kept separate from legacy tri-state targeting."""

    EXACT = "EXACT"
    PARENT_CHILD = "PARENT_CHILD"
    COMPATIBLE = "COMPATIBLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    NO_RULE = "NO_RULE"


class TargetingReasonCode(StrEnum):
    """Closed reasons emitted by the deterministic targeting evaluators."""

    ACTIVITY_MISSING = "ACTIVITY_MISSING"
    AMBIGUOUS_CLASSIFICATION = "AMBIGUOUS_CLASSIFICATION"
    CATEGORY_ALIGNED = "CATEGORY_ALIGNED"
    CATEGORY_MISMATCH = "CATEGORY_MISMATCH"
    CLASSIFICATION_STALE = "CLASSIFICATION_STALE"
    CLIENT_CATEGORY_UNMAPPED = "CLIENT_CATEGORY_UNMAPPED"
    # Kept solely so historical persisted CandidatePoolMember evidence remains
    # readable. New Buyer V1 evaluations emit CLIENT_CATEGORY_UNMAPPED.
    COLLECTION_CATEGORY_UNMAPPED = "COLLECTION_CATEGORY_UNMAPPED"
    COLLECTION_CONTEXT_MISSING = "COLLECTION_CONTEXT_MISSING"
    CONTACT_AVAILABLE = "CONTACT_AVAILABLE"
    CONTACT_EVIDENCE_REDACTED = "CONTACT_EVIDENCE_REDACTED"
    CONTENT_ACTIVITY_INCOMPLETE = "CONTENT_ACTIVITY_INCOMPLETE"
    CONTENT_ACTIVITY_MATCH = "CONTENT_ACTIVITY_MATCH"
    CONTENT_ACTIVITY_MISSING = "CONTENT_ACTIVITY_MISSING"
    CONTENT_ACTIVITY_NO_PUBLIC_CONTENT = "CONTENT_ACTIVITY_NO_PUBLIC_CONTENT"
    CONTENT_ACTIVITY_RECENT = "CONTENT_ACTIVITY_RECENT"
    CONTENT_ACTIVITY_STALE = "CONTENT_ACTIVITY_STALE"
    CONTENT_ACTIVITY_UNKNOWN = "CONTENT_ACTIVITY_UNKNOWN"
    CONTENT_ACTIVITY_UNTRUSTED = "CONTENT_ACTIVITY_UNTRUSTED"
    CREATOR_CATEGORY_UNMAPPED = "CREATOR_CATEGORY_UNMAPPED"
    CREATOR_CLASSIFICATION_MISSING = "CREATOR_CLASSIFICATION_MISSING"
    EMAIL_AVAILABLE = "EMAIL_AVAILABLE"
    FOLLOWERS_IN_RANGE = "FOLLOWERS_IN_RANGE"
    FOLLOWERS_MISSING = "FOLLOWERS_MISSING"
    FOLLOWERS_OUT_OF_RANGE = "FOLLOWERS_OUT_OF_RANGE"
    FRESHNESS_MATCH = "FRESHNESS_MATCH"
    FRESHNESS_MISSING = "FRESHNESS_MISSING"
    FRESHNESS_NOT_MATCH = "FRESHNESS_NOT_MATCH"
    LONG_INACTIVITY_CACHE_MATCH = "LONG_INACTIVITY_CACHE_MATCH"
    LONG_INACTIVITY_CACHE_RECENT = "LONG_INACTIVITY_CACHE_RECENT"
    LONG_INACTIVITY_GREY_DOLPHIN_INVALID = "LONG_INACTIVITY_GREY_DOLPHIN_INVALID"
    LONG_INACTIVITY_GREY_DOLPHIN_MATCH = "LONG_INACTIVITY_GREY_DOLPHIN_MATCH"
    LONG_INACTIVITY_GREY_DOLPHIN_RECENT = "LONG_INACTIVITY_GREY_DOLPHIN_RECENT"
    LONG_INACTIVITY_GREY_DOLPHIN_STALE = "LONG_INACTIVITY_GREY_DOLPHIN_STALE"
    LONG_INACTIVITY_HUITUN_MATCH = "LONG_INACTIVITY_HUITUN_MATCH"
    LONG_INACTIVITY_HUITUN_RECENT = "LONG_INACTIVITY_HUITUN_RECENT"
    LONG_INACTIVITY_HUITUN_STALE = "LONG_INACTIVITY_HUITUN_STALE"
    LONG_INACTIVITY_UNKNOWN = "LONG_INACTIVITY_UNKNOWN"
    NO_COMPARISON_RULE = "NO_COMPARISON_RULE"
    NO_CURRENT_CONTACT = "NO_CURRENT_CONTACT"
    NO_CURRENT_EMAIL = "NO_CURRENT_EMAIL"
    NOTES_60D_IN_RANGE = "NOTES_60D_IN_RANGE"
    NOTES_60D_OUT_OF_RANGE = "NOTES_60D_OUT_OF_RANGE"
    NOTES_7D_IN_RANGE = "NOTES_7D_IN_RANGE"
    NOTES_7D_OUT_OF_RANGE = "NOTES_7D_OUT_OF_RANGE"
    PLATFORM_MATCH = "PLATFORM_MATCH"
    PLATFORM_MISSING = "PLATFORM_MISSING"
    PLATFORM_NOT_MATCH = "PLATFORM_NOT_MATCH"
    SOURCE_MATCH = "SOURCE_MATCH"
    SOURCE_MISSING = "SOURCE_MISSING"
    SOURCE_NOT_MATCH = "SOURCE_NOT_MATCH"
    TRACK_MATCH = "TRACK_MATCH"
    TRACK_MISSING = "TRACK_MISSING"
    TRACK_NOT_MATCH = "TRACK_NOT_MATCH"


class AccountSignalValueState(StrEnum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    NOT_SUPPORTED = "NOT_SUPPORTED"


class AccountSignalFact(FrozenTargetingContract):
    """Provider-neutral, in-memory-only future signal contract."""

    schema_version: Literal[1] = 1
    platform_account_id: UUID
    platform: Platform
    signal_type: str = Field(min_length=1, max_length=80)
    value_state: AccountSignalValueState
    observed_value: str | int | float | bool | None = None
    observed_at: datetime | None = None
    source: str = Field(min_length=1, max_length=80)
    source_reference: str | None = Field(default=None, max_length=255)

    @field_validator("observed_at")
    @classmethod
    def require_aware_observed_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("observed_at must be timezone-aware")
        return value.astimezone(UTC) if value is not None else None


class IntegerRange(FrozenTargetingContract):
    """Inclusive configured bounds for a strict canonical nonnegative integer."""

    minimum: StrictInt | None = Field(default=None, ge=0)
    maximum: StrictInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> IntegerRange:
        if self.minimum is None and self.maximum is None:
            raise ValueError("at least one integer bound is required")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        return self

    def includes(self, value: int) -> bool:
        return (self.minimum is None or value >= self.minimum) and (
            self.maximum is None or value <= self.maximum
        )


class FreshnessConstraint(FrozenTargetingContract):
    """A configured allow-list over canonical freshness status."""

    allowed_statuses: tuple[FreshnessStatus, ...]

    @field_validator("allowed_statuses")
    @classmethod
    def normalize_statuses(cls, value: tuple[FreshnessStatus, ...]) -> tuple[FreshnessStatus, ...]:
        if not value:
            raise ValueError("allowed_statuses must not be empty")
        if len(value) != len(set(value)):
            raise ValueError("allowed_statuses must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))


class ContentActivityConstraint(FrozenTargetingContract):
    """Require an exact trusted current-public inactivity threshold for Seller targeting."""

    schema_version: Literal[1] = 1
    minimum_inactive_days: StrictInt = Field(ge=1, le=3_650)


class LongInactivityConstraint(FrozenTargetingContract):
    """D1A.1 business threshold, independent from trusted Content Activity."""

    schema_version: Literal[1] = 1
    minimum_inactive_days: StrictInt

    @field_validator("minimum_inactive_days")
    @classmethod
    def require_d1a1_threshold(cls, value: int) -> int:
        if value not in {30, 60, 90, 180}:
            raise ValueError("LONG_INACTIVITY supports only 30, 60, 90, or 180 days")
        return value


class ContentActivityFact(FrozenTargetingContract):
    """Trusted-current Content Activity fields for one account candidate.

    Absence and every non-exact state are valid fact inputs: the evaluator maps
    them to ``UNKNOWN`` rather than rejecting an entire materialization batch.
    """

    schema_version: Literal[1] = 1
    trusted_observation_id: UUID | None = None
    trusted_observed_at: datetime | None = None
    trusted_observation_status: ContentActivityObservationStatus | None = None
    trusted_coverage_status: ContentActivityCoverageStatus | None = None
    trusted_activity_result: ContentActivityResult | None = None
    last_publication_at: datetime | None = None
    # Latest-attempt fields are deliberately separate from trusted-current
    # evidence.  They let Candidate evidence distinguish a never-trusted
    # incomplete/provider failure from an account that simply has no check,
    # without allowing a failed attempt to overwrite a fresh trusted fact.
    latest_attempt_observed_at: datetime | None = None
    latest_attempt_observation_status: ContentActivityObservationStatus | None = None
    latest_attempt_coverage_status: ContentActivityCoverageStatus | None = None
    latest_attempt_activity_result: ContentActivityResult | None = None
    # More than one immutable attempt at the exact newest observation instant
    # is unorderable evidence.  The repository preserves this cardinality so
    # targeting never picks a UUID-sorted winner and treats it as current.
    latest_attempt_same_instant_count: int | None = Field(default=None, ge=1)
    # Huitun runtime evidence is intentionally separate from trusted current
    # public Content Activity. It can support the frozen Douyin V1 business
    # rule, but it must never be reclassified as CURRENT_PUBLIC_VISIBLE.
    huitun_observation_id: UUID | None = None
    huitun_observed_at: datetime | None = None
    huitun_observation_status: ContentActivityObservationStatus | None = None
    huitun_coverage_status: ContentActivityCoverageStatus | None = None
    huitun_activity_result: ContentActivityResult | None = None
    huitun_last_publication_at: datetime | None = None
    huitun_coverage_start_at: datetime | None = None
    huitun_coverage_end_at: datetime | None = None
    huitun_latest_observation_id: UUID | None = None
    huitun_latest_observed_at: datetime | None = None
    huitun_latest_observation_status: ContentActivityObservationStatus | None = None
    huitun_latest_coverage_status: ContentActivityCoverageStatus | None = None
    huitun_latest_activity_result: ContentActivityResult | None = None
    huitun_latest_same_instant_count: int | None = Field(default=None, ge=1)

    @field_validator(
        "trusted_observed_at",
        "last_publication_at",
        "latest_attempt_observed_at",
        "huitun_observed_at",
        "huitun_last_publication_at",
        "huitun_coverage_start_at",
        "huitun_coverage_end_at",
        "huitun_latest_observed_at",
    )
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("content activity fact timestamps must be timezone-aware")
        return value.astimezone(UTC) if value is not None else None


class GreyDolphinActivityFact(FrozenTargetingContract):
    """One coherent Huitun aggregate snapshot; never trusted-public evidence."""

    schema_version: Literal[1] = 1
    source: Literal[DataSource.HUITUN] = DataSource.HUITUN
    observed_at: datetime | None = None
    source_updated_at: datetime | None = None
    notes_7d: StrictInt | None = Field(default=None, ge=0)
    notes_60d: StrictInt | None = Field(default=None, ge=0)
    import_job_id: UUID | None = None
    import_row_id: UUID | None = None

    @field_validator("observed_at", "source_updated_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Grey Dolphin activity timestamps must be timezone-aware")
        return value.astimezone(UTC) if value is not None else None


def _normalized_strings(
    value: object,
    *,
    field_name: str,
    max_length: int = 160,
) -> tuple[str, ...]:
    if value is None or isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be an array")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} items must be strings")
        candidate = item.strip()
        if not candidate:
            raise ValueError(f"{field_name} items must not be blank")
        if len(candidate) > max_length:
            raise ValueError(f"{field_name} items must not exceed {max_length} characters")
        normalized.append(candidate)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must not contain duplicates after trim")
    return tuple(sorted(normalized))


class SellerTargetingPolicy(FrozenTargetingContract):
    """The sole typed Seller V1 policy document."""

    schema_version: Literal[1] = 1
    policy_type: Literal["SELLER_V1"] = "SELLER_V1"
    contact_availability: ContactFilter | None = None
    followers: IntegerRange | None = None
    tags_exact_any: tuple[str, ...] = ()
    notes_7d: IntegerRange | None = None
    notes_60d: IntegerRange | None = None
    freshness: FreshnessConstraint | None = None
    content_activity: ContentActivityConstraint | None = None
    long_inactivity: LongInactivityConstraint | None = None
    platforms: tuple[Platform, ...] = ()
    sources: tuple[DataSource, ...] = ()

    @field_validator("tags_exact_any", mode="before")
    @classmethod
    def validate_tags(cls, value: object) -> tuple[str, ...]:
        return _normalized_strings(value, field_name="tags_exact_any")

    @field_validator("platforms")
    @classmethod
    def validate_platforms(cls, value: tuple[Platform, ...]) -> tuple[Platform, ...]:
        if len(value) != len(set(value)):
            raise ValueError("platforms must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, value: tuple[DataSource, ...]) -> tuple[DataSource, ...]:
        if len(value) != len(set(value)):
            raise ValueError("sources must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))

    @property
    def canonical_hash(self) -> str:
        # Existing persisted SELLER_V1 definitions predate this optional D1A
        # constraint. Omitting an absent constraint preserves their historical
        # canonical hash exactly; a configured constraint remains part of the
        # versioned definition and therefore changes the hash.
        canonical = self.model_dump(mode="json")
        if canonical["content_activity"] is None:
            del canonical["content_activity"]
        if canonical["long_inactivity"] is None:
            del canonical["long_inactivity"]
        return hash_document(canonical)


class TaxonomyAlias(FrozenTargetingContract):
    label: str = Field(min_length=1, max_length=160)
    category_id: str = Field(min_length=1, max_length=160)

    @field_validator("label", "category_id")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("taxonomy values must not be blank")
        return normalized


class TaxonomyRelation(FrozenTargetingContract):
    left_category_id: str = Field(min_length=1, max_length=160)
    right_category_id: str = Field(min_length=1, max_length=160)

    @field_validator("left_category_id", "right_category_id")
    @classmethod
    def normalize_category_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("taxonomy category IDs must not be blank")
        return normalized

    @model_validator(mode="after")
    def forbid_self_relation(self) -> TaxonomyRelation:
        if self.left_category_id == self.right_category_id:
            raise ValueError("taxonomy relations must connect distinct category IDs")
        return self

    @property
    def pair(self) -> tuple[str, str]:
        left, right = sorted((self.left_category_id, self.right_category_id))
        return left, right


class TaxonomyDefinition(FrozenTargetingContract):
    """Human-owned policy snapshot; it deliberately has no persistence table."""

    schema_version: Literal[1] = 1
    taxonomy_id: str | None = Field(default=None, min_length=1, max_length=120)
    taxonomy_version: str = Field(min_length=1, max_length=80)
    artifact_hash: str | None = Field(default=None, min_length=64, max_length=64)
    review_status: str | None = Field(default=None, min_length=1, max_length=40)
    reviewed: StrictBool = False
    categories: tuple[str, ...] = ()
    aliases: tuple[TaxonomyAlias, ...] = ()
    parent_child: tuple[TaxonomyRelation, ...] = ()
    compatible: tuple[TaxonomyRelation, ...] = ()
    incompatible: tuple[TaxonomyRelation, ...] = ()

    @field_validator("taxonomy_version")
    @classmethod
    def normalize_version(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("taxonomy_version must not be blank")
        return normalized

    @field_validator("taxonomy_id", "review_status")
    @classmethod
    def normalize_optional_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("taxonomy identity values must not be blank")
        return normalized

    @field_validator("categories", mode="before")
    @classmethod
    def validate_categories(cls, value: object) -> tuple[str, ...]:
        return _normalized_strings(value, field_name="categories")

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, value: tuple[TaxonomyAlias, ...]) -> tuple[TaxonomyAlias, ...]:
        return tuple(sorted(value, key=lambda item: (item.label, item.category_id)))

    @field_validator("parent_child", "compatible", "incompatible")
    @classmethod
    def normalize_relations(
        cls, value: tuple[TaxonomyRelation, ...]
    ) -> tuple[TaxonomyRelation, ...]:
        # Category comparison treats these relations as symmetric. Persist the
        # same direction and order for semantically identical policy documents.
        normalized = tuple(
            TaxonomyRelation(left_category_id=pair[0], right_category_id=pair[1])
            for relation in value
            for pair in (relation.pair,)
        )
        return tuple(sorted(normalized, key=lambda item: item.pair))

    @model_validator(mode="after")
    def validate_taxonomy(self) -> TaxonomyDefinition:
        aliases = [item.label for item in self.aliases]
        if len(aliases) != len(set(aliases)):
            raise ValueError("taxonomy aliases must have unique labels")
        category_ids = set(self.categories) | {item.category_id for item in self.aliases}
        for relation in (*self.parent_child, *self.compatible, *self.incompatible):
            if (
                relation.left_category_id not in category_ids
                or relation.right_category_id not in category_ids
            ):
                raise ValueError("taxonomy relations must reference known categories")
        groups = (self.parent_child, self.compatible, self.incompatible)
        all_pairs = [relation.pair for group in groups for relation in group]
        if len(all_pairs) != len(set(all_pairs)):
            raise ValueError("taxonomy relation pairs must not overlap")
        return self

    def normalize(self, label: str) -> str | None:
        normalized = label.strip()
        if not normalized:
            return None
        if normalized in self.categories:
            return normalized
        return next(
            (alias.category_id for alias in self.aliases if alias.label == normalized),
            None,
        )

    def relation(self, left: str, right: str) -> str | None:
        if left == right:
            return "COMPATIBLE"
        pair = tuple(sorted((left, right)))
        if pair in {relation.pair for relation in self.parent_child}:
            return "COMPATIBLE"
        if pair in {relation.pair for relation in self.compatible}:
            return "COMPATIBLE"
        if pair in {relation.pair for relation in self.incompatible}:
            return "INCOMPATIBLE"
        return None

    def buyer_relation_detail(self, left: str, right: str) -> BuyerCategoryRelation:
        """Return Buyer V1's auditable relation without changing generic semantics."""

        if left == right:
            return BuyerCategoryRelation.EXACT
        pair = tuple(sorted((left, right)))
        if pair in {relation.pair for relation in self.parent_child}:
            return BuyerCategoryRelation.PARENT_CHILD
        if pair in {relation.pair for relation in self.compatible}:
            return BuyerCategoryRelation.COMPATIBLE
        if pair in {relation.pair for relation in self.incompatible}:
            return BuyerCategoryRelation.INCOMPATIBLE
        return BuyerCategoryRelation.NO_RULE


class BuyerTargetingPolicy(FrozenTargetingContract):
    """The sole typed Buyer V1 policy document."""

    schema_version: Literal[1] = 1
    policy_type: Literal["BUYER_V1"] = "BUYER_V1"
    taxonomy: TaxonomyDefinition
    freshness: FreshnessConstraint | None = None

    @property
    def canonical_hash(self) -> str:
        return hash_document(self.model_dump(mode="json"))


class CollectionContextSnapshot(FrozenTargetingContract):
    """Immutable CollectionJob context captured when a Buyer run is reserved."""

    schema_version: Literal[1] = 1
    source_collection_job_id: UUID
    industry: str = Field(min_length=1, max_length=160)
    subdirection: str | None = Field(default=None, max_length=200)
    updated_at: datetime | None = None

    @field_validator("industry")
    @classmethod
    def normalize_industry(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("industry must not be blank")
        return normalized

    @field_validator("subdirection")
    @classmethod
    def normalize_subdirection(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("updated_at")
    @classmethod
    def normalize_updated_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("updated_at must be timezone-aware")
        return value.astimezone(UTC) if value is not None else None


TargetingPolicyDefinition = Annotated[
    SellerTargetingPolicy | BuyerTargetingPolicy,
    Field(discriminator="policy_type"),
]
_POLICY_DEFINITION_ADAPTER: TypeAdapter[SellerTargetingPolicy | BuyerTargetingPolicy] = TypeAdapter(
    TargetingPolicyDefinition
)


def parse_targeting_policy(
    value: object,
) -> SellerTargetingPolicy | BuyerTargetingPolicy:
    """Validate one closed persisted/API policy definition."""

    return _POLICY_DEFINITION_ADAPTER.validate_python(value)


class CandidateFactBundle(FrozenTargetingContract):
    """Canonical input facts for one account candidate; Contact values cannot enter it."""

    schema_version: Literal[1] = 1
    influencer_id: UUID
    platform_account_id: UUID
    platform: Platform
    source: DataSource | None = None
    current_contact_types: tuple[ContactType, ...] = ()
    followers_count: StrictInt | None = Field(default=None, ge=0)
    notes_7d: StrictInt | None = Field(default=None, ge=0)
    notes_60d: StrictInt | None = Field(default=None, ge=0)
    source_tags: tuple[str, ...] | None = ()
    freshness_status: FreshnessStatus | None = None
    freshness_observed_at: datetime | None = None
    source_updated_at: datetime | None = None
    content_activity: ContentActivityFact | None = None
    grey_dolphin_activity: GreyDolphinActivityFact | None = None
    source_collection_job_id: UUID | None = None
    collection_industry: str | None = Field(default=None, max_length=160)
    collection_subdirection: str | None = Field(default=None, max_length=200)
    creator_classification_tags: tuple[str, ...] | None = ()
    creator_classification_ambiguous: StrictBool = False
    source_collection_import_job_ids: tuple[UUID, ...] = ()
    creator_classification_import_job_ids: tuple[UUID, ...] = ()
    account_signals: tuple[AccountSignalFact, ...] = ()

    @field_validator("current_contact_types")
    @classmethod
    def normalize_contact_types(cls, value: tuple[ContactType, ...]) -> tuple[ContactType, ...]:
        if len(value) != len(set(value)):
            raise ValueError("current_contact_types must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))

    @field_validator("source_tags", "creator_classification_tags", mode="before")
    @classmethod
    def normalize_fact_tags(cls, value: object, info: Any) -> tuple[str, ...] | None:
        # JSONB source fields predate this contract and may contain malformed
        # legacy values. Treat them as unavailable evidence rather than failing
        # the entire materialization run.
        if value is None:
            return None
        try:
            return _normalized_strings(value, field_name=info.field_name)
        except ValueError:
            return None

    @field_validator(
        "source_collection_import_job_ids",
        "creator_classification_import_job_ids",
    )
    @classmethod
    def normalize_import_job_ids(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(value) != len(set(value)):
            raise ValueError("classification provenance import job IDs must be unique")
        return tuple(sorted(value, key=str))

    @field_validator("freshness_observed_at", "source_updated_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("fact timestamps must be timezone-aware")
        return value.astimezone(UTC) if value is not None else None


class CriterionEvaluation(FrozenTargetingContract):
    criterion: str = Field(min_length=1, max_length=80)
    result: TargetingEvaluationResult
    reason_code: TargetingReasonCode
    configured: dict[str, Any]
    observed: dict[str, Any]


class TargetingEvaluation(FrozenTargetingContract):
    result: TargetingEvaluationResult
    reason_codes: tuple[TargetingReasonCode, ...]
    redacted_evidence: dict[str, Any]

    @property
    def evidence_hash(self) -> str:
        return hash_document(self.redacted_evidence)


class BuyerLeadTierDecision(FrozenTargetingContract):
    """Immutable Buyer-sales projection derived from a trusted policy and facts."""

    tier: BuyerLeadTier
    relation_summary: dict[str, Any]


_EMAIL_VALUE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_PHONE_VALUE = re.compile(r"(?<!\d)\+?\d(?:[\d\s().-]{5,}\d)(?!\d)")
_CANONICAL_UUID_VALUE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _is_sensitive(value: object) -> bool:
    if isinstance(value, str):
        # Canonical UUIDs are system identifiers, not phone numbers.  Their
        # numeric segments can otherwise match the permissive phone pattern
        # below and make redacted targeting evidence nondeterministic.
        if _CANONICAL_UUID_VALUE.fullmatch(value):
            return False
        digits = sum(character.isdigit() for character in value)
        return bool(
            _EMAIL_VALUE.search(value) or (7 <= digits <= 15 and _PHONE_VALUE.search(value))
        )
    if isinstance(value, list | tuple):
        return any(_is_sensitive(item) for item in value)
    if isinstance(value, dict):
        return any(_is_sensitive(item) for item in value.values())
    return False


def _redacted(value: Any) -> Any:
    canonical = canonical_value(value)
    if isinstance(canonical, dict):
        return {key: _redacted(item) for key, item in canonical.items()}
    if isinstance(canonical, list):
        return [_redacted(item) for item in canonical]
    if _is_sensitive(canonical):
        return "[REDACTED]"
    return canonical


def reduce_criterion_results(
    results: Iterable[TargetingEvaluationResult],
) -> TargetingEvaluationResult:
    """Apply the frozen order-independent Seller/Buyer tri-state reduction."""

    materialized = tuple(results)
    if TargetingEvaluationResult.NOT_MATCH in materialized:
        return TargetingEvaluationResult.NOT_MATCH
    if TargetingEvaluationResult.UNKNOWN in materialized:
        return TargetingEvaluationResult.UNKNOWN
    return TargetingEvaluationResult.MATCH


def _range_evaluation(
    *,
    criterion: str,
    value: int | None,
    configured: IntegerRange,
    match_reason: str,
    missing_reason: str,
    not_match_reason: str,
) -> CriterionEvaluation:
    if value is None:
        return CriterionEvaluation(
            criterion=criterion,
            result=TargetingEvaluationResult.UNKNOWN,
            reason_code=missing_reason,
            configured={"minimum": configured.minimum, "maximum": configured.maximum},
            observed={"value": None},
        )
    matches = configured.includes(value)
    return CriterionEvaluation(
        criterion=criterion,
        result=(
            TargetingEvaluationResult.MATCH if matches else TargetingEvaluationResult.NOT_MATCH
        ),
        reason_code=match_reason if matches else not_match_reason,
        configured={"minimum": configured.minimum, "maximum": configured.maximum},
        observed={"value": value},
    )


def _contact_evaluation(
    facts: CandidateFactBundle, configured: ContactFilter
) -> CriterionEvaluation:
    types = facts.current_contact_types
    if configured is ContactFilter.HAS_CONTACT:
        matches = bool(types)
        match_reason, not_match_reason = "CONTACT_AVAILABLE", "NO_CURRENT_CONTACT"
    elif configured is ContactFilter.HAS_EMAIL:
        matches = ContactType.EMAIL in types
        match_reason, not_match_reason = "EMAIL_AVAILABLE", "NO_CURRENT_EMAIL"
    else:
        matches = not types
        match_reason, not_match_reason = "NO_CURRENT_CONTACT", "CONTACT_AVAILABLE"
    return CriterionEvaluation(
        criterion="contact_availability",
        result=TargetingEvaluationResult.MATCH if matches else TargetingEvaluationResult.NOT_MATCH,
        reason_code=match_reason if matches else not_match_reason,
        configured={"filter": configured.value},
        observed={
            "current_contact_count": len(types),
            "current_contact_types": [item.value for item in types],
        },
    )


def _tags_evaluation(
    facts: CandidateFactBundle, configured: tuple[str, ...]
) -> CriterionEvaluation:
    observed = facts.source_tags or ()
    if not observed:
        return CriterionEvaluation(
            criterion="tags_exact_any",
            result=TargetingEvaluationResult.UNKNOWN,
            reason_code="TRACK_MISSING",
            configured={"exact_any": list(configured)},
            observed={"tags": []},
        )
    intersection = tuple(tag for tag in observed if tag in configured)
    return CriterionEvaluation(
        criterion="tags_exact_any",
        result=(
            TargetingEvaluationResult.MATCH if intersection else TargetingEvaluationResult.NOT_MATCH
        ),
        reason_code="TRACK_MATCH" if intersection else "TRACK_NOT_MATCH",
        configured={"exact_any": list(configured)},
        observed={"tags": list(observed), "matching_tags": list(intersection)},
    )


def _set_evaluation(
    *,
    criterion: str,
    observed: str | None,
    configured: Sequence[str],
    missing_reason: str,
    match_reason: str,
    not_match_reason: str,
) -> CriterionEvaluation:
    if observed is None:
        return CriterionEvaluation(
            criterion=criterion,
            result=TargetingEvaluationResult.UNKNOWN,
            reason_code=missing_reason,
            configured={"allowed": list(configured)},
            observed={"value": None},
        )
    matches = observed in configured
    return CriterionEvaluation(
        criterion=criterion,
        result=TargetingEvaluationResult.MATCH if matches else TargetingEvaluationResult.NOT_MATCH,
        reason_code=match_reason if matches else not_match_reason,
        configured={"allowed": list(configured)},
        observed={"value": observed},
    )


def _freshness_evaluation(
    facts: CandidateFactBundle, configured: FreshnessConstraint
) -> CriterionEvaluation:
    if facts.freshness_status is None or facts.freshness_status is FreshnessStatus.UNKNOWN:
        return CriterionEvaluation(
            criterion="freshness",
            result=TargetingEvaluationResult.UNKNOWN,
            reason_code="FRESHNESS_MISSING",
            configured={"allowed_statuses": [item.value for item in configured.allowed_statuses]},
            observed={
                "status": None if facts.freshness_status is None else facts.freshness_status.value
            },
        )
    return _set_evaluation(
        criterion="freshness",
        observed=facts.freshness_status.value,
        configured=[item.value for item in configured.allowed_statuses],
        missing_reason="FRESHNESS_MISSING",
        match_reason="FRESHNESS_MATCH",
        not_match_reason="FRESHNESS_NOT_MATCH",
    )


def _content_activity_unknown(
    *,
    configured: ContentActivityConstraint,
    reason_code: TargetingReasonCode,
    fact: ContentActivityFact | None,
    as_of: datetime | None,
) -> CriterionEvaluation:
    """Build one explicit fail-closed Content Activity criterion result."""

    return CriterionEvaluation(
        criterion="content_activity",
        result=TargetingEvaluationResult.UNKNOWN,
        reason_code=reason_code,
        configured={
            "schema_version": configured.schema_version,
            "minimum_inactive_days": configured.minimum_inactive_days,
        },
        observed={
            "as_of": as_of,
            "trusted_observed_at": (fact.trusted_observed_at if fact is not None else None),
            "trusted_observation_status": (
                fact.trusted_observation_status if fact is not None else None
            ),
            "trusted_coverage_status": (fact.trusted_coverage_status if fact is not None else None),
            "trusted_activity_result": (fact.trusted_activity_result if fact is not None else None),
            "latest_attempt_observed_at": (
                fact.latest_attempt_observed_at if fact is not None else None
            ),
            "latest_attempt_observation_status": (
                fact.latest_attempt_observation_status if fact is not None else None
            ),
            "latest_attempt_coverage_status": (
                fact.latest_attempt_coverage_status if fact is not None else None
            ),
            "latest_attempt_activity_result": (
                fact.latest_attempt_activity_result if fact is not None else None
            ),
            "latest_attempt_same_instant_count": (
                fact.latest_attempt_same_instant_count if fact is not None else None
            ),
            # A timestamp from a non-current/incomplete/untrusted fact is not
            # evidence of the creator's present public activity.  Omit it
            # from sales-visible Candidate evidence instead of inviting an
            # accidental inactivity inference.
            "last_publication_at": None,
        },
    )


def _latest_attempt_supersedes_trusted_fact(
    fact: ContentActivityFact,
    *,
    evaluation_time: datetime,
) -> bool:
    """Return whether a later/contradictory attempt makes old trust last-known only.

    A successful newer complete observation replaces the trusted projection. A
    later timeout, partial page, or untrusted result deliberately does not
    erase that historical fact, but it prevents Candidate targeting from
    asserting the old fact as current. Equal instants are usable only when all
    decision-bearing status axes agree; otherwise they are unorderable and
    fail closed.
    """

    if (fact.latest_attempt_same_instant_count or 0) > 1:
        return True

    latest_observed_at = fact.latest_attempt_observed_at
    trusted_observed_at = fact.trusted_observed_at
    if latest_observed_at is None or trusted_observed_at is None:
        return False
    if latest_observed_at > evaluation_time:
        return True
    if latest_observed_at > trusted_observed_at:
        return True
    if latest_observed_at < trusted_observed_at:
        return False
    return (
        fact.latest_attempt_observation_status != fact.trusted_observation_status
        or fact.latest_attempt_coverage_status != fact.trusted_coverage_status
        or fact.latest_attempt_activity_result != fact.trusted_activity_result
    )


def _latest_attempt_unknown_reason(fact: ContentActivityFact) -> TargetingReasonCode:
    """Classify a post-trusted attempt without turning it into a current fact."""

    if (
        fact.latest_attempt_observation_status is ContentActivityObservationStatus.RESULT_INCOMPLETE
        or fact.latest_attempt_coverage_status is ContentActivityCoverageStatus.INCOMPLETE
    ):
        return TargetingReasonCode.CONTENT_ACTIVITY_INCOMPLETE
    return TargetingReasonCode.CONTENT_ACTIVITY_UNTRUSTED


def _content_activity_evaluation(
    facts: CandidateFactBundle,
    configured: ContentActivityConstraint,
    *,
    as_of: datetime | None,
    freshness_policy: ContentActivityFreshnessPolicy,
) -> CriterionEvaluation:
    """Evaluate a current trusted-public publication timestamp, never an estimate.

    A Content Activity projection is intentionally a separate evidence stream
    from source-data freshness.  It is usable only if the exact three-part
    trusted truth is present at the Candidate Run's captured ``as_of``.
    """

    fact = facts.content_activity
    if fact is None:
        return _content_activity_unknown(
            configured=configured,
            reason_code=TargetingReasonCode.CONTENT_ACTIVITY_MISSING,
            fact=None,
            as_of=as_of,
        )
    if as_of is None:
        return _content_activity_unknown(
            configured=configured,
            reason_code=TargetingReasonCode.CONTENT_ACTIVITY_UNKNOWN,
            fact=fact,
            as_of=None,
        )
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("content activity as_of must be timezone-aware")
    evaluation_time = as_of.astimezone(UTC)

    # A projection has trusted fields only after a complete accepted result.
    # When it has never had one, retain the latest-attempt classification so
    # Candidate results are safely UNKNOWN *and* diagnostically distinguish
    # partial pagination/provider failure from an account never checked.
    if fact.trusted_observation_status is None:
        if (
            fact.latest_attempt_observation_status
            == ContentActivityObservationStatus.RESULT_INCOMPLETE
            or fact.latest_attempt_coverage_status == ContentActivityCoverageStatus.INCOMPLETE
        ):
            return _content_activity_unknown(
                configured=configured,
                reason_code=TargetingReasonCode.CONTENT_ACTIVITY_INCOMPLETE,
                fact=fact,
                as_of=evaluation_time,
            )
        if fact.latest_attempt_observation_status is not None:
            return _content_activity_unknown(
                configured=configured,
                reason_code=TargetingReasonCode.CONTENT_ACTIVITY_UNTRUSTED,
                fact=fact,
                as_of=evaluation_time,
            )
        return _content_activity_unknown(
            configured=configured,
            reason_code=TargetingReasonCode.CONTENT_ACTIVITY_MISSING,
            fact=fact,
            as_of=evaluation_time,
        )

    if _latest_attempt_supersedes_trusted_fact(fact, evaluation_time=evaluation_time):
        # Preserve the old trusted observation in durable history/projection,
        # but do not use it as a CURRENT_PUBLIC_VISIBLE targeting fact after a
        # later incomplete or failed attempt. This is last-known evidence only.
        return _content_activity_unknown(
            configured=configured,
            reason_code=_latest_attempt_unknown_reason(fact),
            fact=fact,
            as_of=evaluation_time,
        )

    # A partial collection is not a current-public result even if it happens
    # to contain an old publication.  Keep this distinct in durable evidence.
    if (
        fact.trusted_observation_status == ContentActivityObservationStatus.RESULT_INCOMPLETE
        or fact.trusted_coverage_status == ContentActivityCoverageStatus.INCOMPLETE
    ):
        return _content_activity_unknown(
            configured=configured,
            reason_code=TargetingReasonCode.CONTENT_ACTIVITY_INCOMPLETE,
            fact=fact,
            as_of=evaluation_time,
        )
    if fact.trusted_observation_status in {
        ContentActivityObservationStatus.RESULT_UNTRUSTED,
        ContentActivityObservationStatus.IDENTITY_UNRESOLVED,
        ContentActivityObservationStatus.ACCESS_RESTRICTED,
        ContentActivityObservationStatus.PROVIDER_AUTH_ERROR,
        ContentActivityObservationStatus.PROVIDER_RATE_LIMITED,
        ContentActivityObservationStatus.PROVIDER_ERROR,
    }:
        return _content_activity_unknown(
            configured=configured,
            reason_code=TargetingReasonCode.CONTENT_ACTIVITY_UNTRUSTED,
            fact=fact,
            as_of=evaluation_time,
        )
    if (
        fact.trusted_observation_status != ContentActivityObservationStatus.COMPLETE
        or fact.trusted_coverage_status != ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET
    ):
        return _content_activity_unknown(
            configured=configured,
            reason_code=TargetingReasonCode.CONTENT_ACTIVITY_UNKNOWN,
            fact=fact,
            as_of=evaluation_time,
        )
    if fact.trusted_activity_result == ContentActivityResult.NO_PUBLIC_CONTENT:
        # A complete empty public set establishes no publication timestamp. It
        # must not turn into an invented infinite inactivity duration.
        return _content_activity_unknown(
            configured=configured,
            reason_code=TargetingReasonCode.CONTENT_ACTIVITY_NO_PUBLIC_CONTENT,
            fact=fact,
            as_of=evaluation_time,
        )
    if fact.trusted_activity_result != ContentActivityResult.PUBLICATION_FOUND:
        return _content_activity_unknown(
            configured=configured,
            reason_code=TargetingReasonCode.CONTENT_ACTIVITY_UNKNOWN,
            fact=fact,
            as_of=evaluation_time,
        )
    if fact.last_publication_at is None:
        return _content_activity_unknown(
            configured=configured,
            reason_code=TargetingReasonCode.CONTENT_ACTIVITY_UNTRUSTED,
            fact=fact,
            as_of=evaluation_time,
        )
    if not freshness_policy.is_current(fact.trusted_observed_at, evaluation_time):
        return _content_activity_unknown(
            configured=configured,
            reason_code=TargetingReasonCode.CONTENT_ACTIVITY_STALE,
            fact=fact,
            as_of=evaluation_time,
        )

    last_publication_at = fact.last_publication_at.astimezone(UTC)
    if last_publication_at > evaluation_time:
        return _content_activity_unknown(
            configured=configured,
            reason_code=TargetingReasonCode.CONTENT_ACTIVITY_UNTRUSTED,
            fact=fact,
            as_of=evaluation_time,
        )
    cutoff = evaluation_time - timedelta(days=configured.minimum_inactive_days)
    matches = last_publication_at <= cutoff
    return CriterionEvaluation(
        criterion="content_activity",
        result=(
            TargetingEvaluationResult.MATCH if matches else TargetingEvaluationResult.NOT_MATCH
        ),
        reason_code=(
            TargetingReasonCode.CONTENT_ACTIVITY_MATCH
            if matches
            else TargetingReasonCode.CONTENT_ACTIVITY_RECENT
        ),
        configured={
            "schema_version": configured.schema_version,
            "minimum_inactive_days": configured.minimum_inactive_days,
        },
        observed={
            "as_of": evaluation_time,
            "trusted_observed_at": fact.trusted_observed_at,
            "last_publication_at": last_publication_at,
            # This is display/evidence only.  The decision above uses the
            # exact timestamp cutoff, never rounded day arithmetic.
            "inactive_days": int((evaluation_time - last_publication_at).total_seconds() // 86_400),
        },
    )


def _huitun_runtime_unknown(
    *,
    configured: LongInactivityConstraint,
    fact: ContentActivityFact | None,
    as_of: datetime | None,
    reason_code: TargetingReasonCode = TargetingReasonCode.LONG_INACTIVITY_UNKNOWN,
) -> CriterionEvaluation:
    """Return one explicit unknown without upgrading returned-scope evidence.

    The fields intentionally omit a synthetic last-publication timestamp.  In
    particular, an encrypted envelope, auth failure, partial response, uid
    mismatch, or stale semantic observation cannot flow into a duration.
    """

    return CriterionEvaluation(
        criterion="long_inactivity",
        result=TargetingEvaluationResult.UNKNOWN,
        reason_code=reason_code,
        configured={
            "schema_version": configured.schema_version,
            "minimum_inactive_days": configured.minimum_inactive_days,
        },
        observed={
            "source": "HUITUN_DOUYIN_AWEME_LIST",
            "precision": "UNKNOWN",
            "as_of": as_of,
            "observed_at": fact.huitun_observed_at if fact is not None else None,
            "latest_attempt_observed_at": (
                fact.huitun_latest_observed_at if fact is not None else None
            ),
            "last_publication_at": None,
            "inactive_days": None,
            "lower_bound_inactive_days": None,
        },
    )


def _huitun_douyin_inactivity_evaluation(
    facts: CandidateFactBundle,
    configured: LongInactivityConstraint,
    *,
    as_of: datetime | None,
    freshness_policy: ContentActivityFreshnessPolicy,
) -> CriterionEvaluation:
    """Evaluate only a bound Huitun awemeList runtime semantic observation.

    This evidence has a deliberately narrower visibility contract than
    ``_content_activity_evaluation``. An exact semantic result can decide a
    threshold; a confirmed empty current-ending range yields only a lower bound
    and therefore may match but can never prove an account is recent.
    """

    fact = facts.content_activity
    if facts.platform is not Platform.DOUYIN or fact is None:
        return _huitun_runtime_unknown(configured=configured, fact=fact, as_of=as_of)
    if as_of is None:
        return _huitun_runtime_unknown(configured=configured, fact=fact, as_of=None)
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("Huitun runtime as_of must be timezone-aware")
    evaluation_time = as_of.astimezone(UTC)
    if fact.huitun_observation_id is None or fact.huitun_observed_at is None:
        return _huitun_runtime_unknown(
            configured=configured,
            fact=fact,
            as_of=evaluation_time,
        )

    # A later (or concurrent same-instant) Huitun attempt is authoritative for
    # fail-closed semantics. Do not let a retained older semantic success mask
    # an encrypted/auth/partial/mismatched current attempt.
    if (
        fact.huitun_latest_observation_id is None
        or fact.huitun_latest_observed_at is None
        or fact.huitun_latest_same_instant_count != 1
        or fact.huitun_latest_observed_at > fact.huitun_observed_at
        or (
            fact.huitun_latest_observed_at == fact.huitun_observed_at
            and fact.huitun_latest_observation_id != fact.huitun_observation_id
        )
    ):
        return _huitun_runtime_unknown(
            configured=configured,
            fact=fact,
            as_of=evaluation_time,
        )

    if (
        fact.huitun_observation_status is not ContentActivityObservationStatus.COMPLETE
        or fact.huitun_coverage_status
        not in {
            ContentActivityCoverageStatus.LATEST_BOUND_PROVEN,
            ContentActivityCoverageStatus.LOOKBACK_BOUNDED,
        }
    ):
        return _huitun_runtime_unknown(
            configured=configured,
            fact=fact,
            as_of=evaluation_time,
        )
    if not freshness_policy.is_current(fact.huitun_observed_at, evaluation_time):
        return _huitun_runtime_unknown(
            configured=configured,
            fact=fact,
            as_of=evaluation_time,
            reason_code=TargetingReasonCode.LONG_INACTIVITY_HUITUN_STALE,
        )
    if (
        fact.huitun_coverage_end_at is None
        or fact.huitun_coverage_end_at > fact.huitun_observed_at
        or fact.huitun_observed_at - fact.huitun_coverage_end_at > timedelta(minutes=5)
    ):
        return _huitun_runtime_unknown(
            configured=configured,
            fact=fact,
            as_of=evaluation_time,
        )

    configured_document = {
        "schema_version": configured.schema_version,
        "minimum_inactive_days": configured.minimum_inactive_days,
    }
    common_observed = {
        "source": "HUITUN_DOUYIN_AWEME_LIST",
        "observed_at": fact.huitun_observed_at,
        "latest_attempt_observed_at": fact.huitun_latest_observed_at,
        "coverage_start_at": fact.huitun_coverage_start_at,
        "coverage_end_at": fact.huitun_coverage_end_at,
    }
    if fact.huitun_activity_result is ContentActivityResult.PUBLICATION_FOUND:
        publication = fact.huitun_last_publication_at
        if publication is None or publication > evaluation_time:
            return _huitun_runtime_unknown(
                configured=configured,
                fact=fact,
                as_of=evaluation_time,
            )
        cutoff = evaluation_time - timedelta(days=configured.minimum_inactive_days)
        matches = publication <= cutoff
        return CriterionEvaluation(
            criterion="long_inactivity",
            result=(
                TargetingEvaluationResult.MATCH if matches else TargetingEvaluationResult.NOT_MATCH
            ),
            reason_code=(
                TargetingReasonCode.LONG_INACTIVITY_HUITUN_MATCH
                if matches
                else TargetingReasonCode.LONG_INACTIVITY_HUITUN_RECENT
            ),
            configured=configured_document,
            observed={
                "precision": "EXACT",
                "as_of": evaluation_time,
                "last_publication_at": publication,
                "inactive_days": int((evaluation_time - publication).total_seconds() // 86_400),
                **common_observed,
            },
        )

    if fact.huitun_activity_result is not ContentActivityResult.AT_LEAST_LOOKBACK_INACTIVE:
        return _huitun_runtime_unknown(
            configured=configured,
            fact=fact,
            as_of=evaluation_time,
        )
    coverage_start_at = fact.huitun_coverage_start_at
    coverage_end_at = fact.huitun_coverage_end_at
    if (
        coverage_start_at is None
        or coverage_end_at is None
        or coverage_start_at >= coverage_end_at
        or coverage_end_at > evaluation_time
        or coverage_end_at > fact.huitun_observed_at
    ):
        return _huitun_runtime_unknown(
            configured=configured,
            fact=fact,
            as_of=evaluation_time,
        )
    lower_bound_days = int((coverage_end_at - coverage_start_at).total_seconds() // 86_400)
    if lower_bound_days < configured.minimum_inactive_days:
        # A bounded empty range below the requested threshold cannot establish
        # recency: older work may exist immediately before the range.
        return _huitun_runtime_unknown(
            configured=configured,
            fact=fact,
            as_of=evaluation_time,
        )
    return CriterionEvaluation(
        criterion="long_inactivity",
        result=TargetingEvaluationResult.MATCH,
        reason_code=TargetingReasonCode.LONG_INACTIVITY_HUITUN_MATCH,
        configured=configured_document,
        observed={
            "precision": "LOWER_BOUND",
            "as_of": evaluation_time,
            "last_publication_at": None,
            "inactive_days": None,
            "lower_bound_inactive_days": lower_bound_days,
            **common_observed,
        },
    )


def _long_inactivity_evaluation(
    facts: CandidateFactBundle,
    configured: LongInactivityConstraint,
    *,
    as_of: datetime | None,
    content_activity_freshness_policy: ContentActivityFreshnessPolicy,
    grey_dolphin_activity_freshness_policy: GreyDolphinActivityFreshnessPolicy,
) -> CriterionEvaluation:
    """Route Huitun semantic evidence, exact cache, then coarse Grey Dolphin.

    Grey Dolphin is intentionally evaluated here rather than being copied into
    ``ContentActivityFact``: its aggregate notes counters can support only the
    frozen business conclusions below and never become a trusted-public fact.
    """

    huitun = _huitun_douyin_inactivity_evaluation(
        facts,
        configured,
        as_of=as_of,
        freshness_policy=content_activity_freshness_policy,
    )
    # Once an account has a Huitun runtime attempt, its semantic state is the
    # current source-specific truth. Incomplete, raw-encrypted, auth, uid, and
    # runtime failures must remain UNKNOWN rather than falling through to a
    # coarse aggregate that could hide the failure.
    if (
        facts.content_activity is not None
        and facts.content_activity.huitun_latest_observation_id is not None
    ):
        return huitun

    exact = _content_activity_evaluation(
        facts,
        ContentActivityConstraint(minimum_inactive_days=configured.minimum_inactive_days),
        as_of=as_of,
        freshness_policy=content_activity_freshness_policy,
    )
    if exact.result is not TargetingEvaluationResult.UNKNOWN:
        return CriterionEvaluation(
            criterion="long_inactivity",
            result=exact.result,
            reason_code=(
                TargetingReasonCode.LONG_INACTIVITY_CACHE_MATCH
                if exact.result is TargetingEvaluationResult.MATCH
                else TargetingReasonCode.LONG_INACTIVITY_CACHE_RECENT
            ),
            configured={
                "schema_version": configured.schema_version,
                "minimum_inactive_days": configured.minimum_inactive_days,
            },
            observed={
                "source": "TRUSTED_CONTENT_ACTIVITY",
                "precision": "EXACT",
                **exact.observed,
            },
        )

    grey = facts.grey_dolphin_activity
    configured_document = {
        "schema_version": configured.schema_version,
        "minimum_inactive_days": configured.minimum_inactive_days,
    }
    if grey is None or as_of is None:
        return CriterionEvaluation(
            criterion="long_inactivity",
            result=TargetingEvaluationResult.UNKNOWN,
            reason_code=TargetingReasonCode.LONG_INACTIVITY_UNKNOWN,
            configured=configured_document,
            observed={
                "source": "GREY_DOLPHIN",
                "precision": "COARSE",
                "observed_at": None,
                "notes_7d": None,
                "notes_60d": None,
                "cache_reason": exact.reason_code,
            },
        )
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("long inactivity as_of must be timezone-aware")
    evaluation_time = as_of.astimezone(UTC)
    observed = {
        "source": "GREY_DOLPHIN",
        "precision": "COARSE",
        "observed_at": grey.observed_at,
        "source_updated_at": grey.source_updated_at,
        "notes_7d": grey.notes_7d,
        "notes_60d": grey.notes_60d,
        "cache_reason": exact.reason_code,
    }
    if grey.notes_7d is None or grey.notes_60d is None or grey.notes_7d > grey.notes_60d:
        return CriterionEvaluation(
            criterion="long_inactivity",
            result=TargetingEvaluationResult.UNKNOWN,
            reason_code=TargetingReasonCode.LONG_INACTIVITY_GREY_DOLPHIN_INVALID,
            configured=configured_document,
            observed=observed,
        )
    if not grey_dolphin_activity_freshness_policy.is_current(grey.observed_at, evaluation_time):
        return CriterionEvaluation(
            criterion="long_inactivity",
            result=TargetingEvaluationResult.UNKNOWN,
            reason_code=TargetingReasonCode.LONG_INACTIVITY_GREY_DOLPHIN_STALE,
            configured=configured_document,
            observed=observed,
        )

    minimum = configured.minimum_inactive_days
    # Any note inside the recent seven-day window disproves all P0 thresholds.
    if grey.notes_7d > 0:
        result = TargetingEvaluationResult.NOT_MATCH
    elif grey.notes_60d == 0:
        # A zero 60-day counter safely proves both 30+ and 60+, but no more.
        result = (
            TargetingEvaluationResult.MATCH
            if minimum in {30, 60}
            else TargetingEvaluationResult.UNKNOWN
        )
    elif minimum in {60, 90, 180}:
        # A note somewhere in the last 60 days rules out all of these spans.
        result = TargetingEvaluationResult.NOT_MATCH
    else:
        # The note could be on either side of the 30-day boundary.
        result = TargetingEvaluationResult.UNKNOWN

    return CriterionEvaluation(
        criterion="long_inactivity",
        result=result,
        reason_code=(
            TargetingReasonCode.LONG_INACTIVITY_GREY_DOLPHIN_MATCH
            if result is TargetingEvaluationResult.MATCH
            else (
                TargetingReasonCode.LONG_INACTIVITY_GREY_DOLPHIN_RECENT
                if result is TargetingEvaluationResult.NOT_MATCH
                else TargetingReasonCode.LONG_INACTIVITY_UNKNOWN
            )
        ),
        configured=configured_document,
        observed=observed,
    )


def evaluate_seller(
    policy: SellerTargetingPolicy,
    facts: CandidateFactBundle,
    *,
    as_of: datetime | None = None,
    content_activity_freshness_policy: ContentActivityFreshnessPolicy | None = None,
    grey_dolphin_activity_freshness_policy: GreyDolphinActivityFreshnessPolicy | None = None,
) -> TargetingEvaluation:
    """Evaluate configured Seller criteria with frozen NOT_MATCH precedence."""

    criteria: list[CriterionEvaluation] = []
    if policy.contact_availability is not None:
        criteria.append(_contact_evaluation(facts, policy.contact_availability))
    if policy.followers is not None:
        criteria.append(
            _range_evaluation(
                criterion="followers",
                value=facts.followers_count,
                configured=policy.followers,
                match_reason="FOLLOWERS_IN_RANGE",
                missing_reason="FOLLOWERS_MISSING",
                not_match_reason="FOLLOWERS_OUT_OF_RANGE",
            )
        )
    if policy.tags_exact_any:
        criteria.append(_tags_evaluation(facts, policy.tags_exact_any))
    if policy.notes_7d is not None:
        criteria.append(
            _range_evaluation(
                criterion="notes_7d",
                value=facts.notes_7d,
                configured=policy.notes_7d,
                match_reason="NOTES_7D_IN_RANGE",
                missing_reason="ACTIVITY_MISSING",
                not_match_reason="NOTES_7D_OUT_OF_RANGE",
            )
        )
    if policy.notes_60d is not None:
        criteria.append(
            _range_evaluation(
                criterion="notes_60d",
                value=facts.notes_60d,
                configured=policy.notes_60d,
                match_reason="NOTES_60D_IN_RANGE",
                missing_reason="ACTIVITY_MISSING",
                not_match_reason="NOTES_60D_OUT_OF_RANGE",
            )
        )
    if policy.freshness is not None:
        criteria.append(_freshness_evaluation(facts, policy.freshness))
    if policy.content_activity is not None:
        criteria.append(
            _content_activity_evaluation(
                facts,
                policy.content_activity,
                as_of=as_of,
                freshness_policy=(
                    content_activity_freshness_policy or ContentActivityFreshnessPolicy()
                ),
            )
        )
    if policy.long_inactivity is not None:
        criteria.append(
            _long_inactivity_evaluation(
                facts,
                policy.long_inactivity,
                as_of=as_of,
                content_activity_freshness_policy=(
                    content_activity_freshness_policy or ContentActivityFreshnessPolicy()
                ),
                grey_dolphin_activity_freshness_policy=(
                    grey_dolphin_activity_freshness_policy or GreyDolphinActivityFreshnessPolicy()
                ),
            )
        )
    if policy.platforms:
        criteria.append(
            _set_evaluation(
                criterion="platform",
                observed=facts.platform.value,
                configured=[item.value for item in policy.platforms],
                missing_reason="PLATFORM_MISSING",
                match_reason="PLATFORM_MATCH",
                not_match_reason="PLATFORM_NOT_MATCH",
            )
        )
    if policy.sources:
        criteria.append(
            _set_evaluation(
                criterion="source",
                observed=facts.source.value if facts.source is not None else None,
                configured=[item.value for item in policy.sources],
                missing_reason="SOURCE_MISSING",
                match_reason="SOURCE_MATCH",
                not_match_reason="SOURCE_NOT_MATCH",
            )
        )

    result = reduce_criterion_results(item.result for item in criteria)
    evidence = {
        "schema_version": 1,
        "policy_type": policy.policy_type,
        "policy_hash": policy.canonical_hash,
        "account": {
            "influencer_id": facts.influencer_id,
            "platform_account_id": facts.platform_account_id,
            "platform": facts.platform,
        },
        "criteria": [item.model_dump(mode="json") for item in criteria],
    }
    return TargetingEvaluation(
        result=result,
        reason_codes=tuple(item.reason_code for item in criteria),
        redacted_evidence=_redacted(evidence),
    )


def _normalize_collection_categories(
    taxonomy: TaxonomyDefinition, facts: CandidateFactBundle
) -> tuple[tuple[str, ...] | None, str | None]:
    labels = [facts.collection_industry]
    if facts.collection_subdirection is not None and facts.collection_subdirection.strip():
        labels.append(facts.collection_subdirection)
    if not labels[0] or not labels[0].strip():
        return None, "COLLECTION_CONTEXT_MISSING"
    normalized: list[str] = []
    for label in labels:
        if label is None:
            continue
        category = taxonomy.normalize(label)
        if category is None:
            return None, "CLIENT_CATEGORY_UNMAPPED"
        normalized.append(category)
    return tuple(sorted(set(normalized))), None


def _normalize_creator_categories(
    taxonomy: TaxonomyDefinition, facts: CandidateFactBundle
) -> tuple[tuple[str, ...] | None, str | None]:
    if facts.creator_classification_ambiguous:
        return None, "AMBIGUOUS_CLASSIFICATION"
    if not facts.creator_classification_tags:
        return None, "CREATOR_CLASSIFICATION_MISSING"
    normalized: list[str] = []
    for tag in facts.creator_classification_tags:
        category = taxonomy.normalize(tag)
        if category is None:
            return None, "CREATOR_CATEGORY_UNMAPPED"
        normalized.append(category)
    return tuple(sorted(set(normalized))), None


def _buyer_unknown(
    policy: BuyerTargetingPolicy,
    facts: CandidateFactBundle,
    reason_code: TargetingReasonCode,
) -> TargetingEvaluation:
    evidence = {
        "schema_version": 1,
        "policy_type": policy.policy_type,
        "taxonomy_version": policy.taxonomy.taxonomy_version,
        "account": {
            "influencer_id": facts.influencer_id,
            "platform_account_id": facts.platform_account_id,
            "platform": facts.platform,
        },
        "collection_context": {
            "source_collection_job_id": facts.source_collection_job_id,
            "industry": facts.collection_industry,
            "subdirection": facts.collection_subdirection,
            "provenance_import_job_ids": list(facts.source_collection_import_job_ids),
        },
        "creator_classification": {
            "tags": list(facts.creator_classification_tags or ()),
            "provenance_import_job_ids": list(facts.creator_classification_import_job_ids),
        },
        "reason": reason_code,
    }
    return TargetingEvaluation(
        result=TargetingEvaluationResult.UNKNOWN,
        reason_codes=(reason_code,),
        redacted_evidence=_redacted(evidence),
    )


def evaluate_buyer(
    policy: BuyerTargetingPolicy,
    facts: CandidateFactBundle,
) -> TargetingEvaluation:
    """Evaluate explicit category relations without AI or free-text inference."""

    if facts.platform is not Platform.XIAOHONGSHU:
        return _buyer_unknown(policy, facts, TargetingReasonCode.CREATOR_CLASSIFICATION_MISSING)
    if policy.freshness is not None:
        freshness = _freshness_evaluation(facts, policy.freshness)
        if freshness.result is TargetingEvaluationResult.UNKNOWN:
            return _buyer_unknown(policy, facts, TargetingReasonCode.CLASSIFICATION_STALE)
        if freshness.result is TargetingEvaluationResult.NOT_MATCH:
            return _buyer_unknown(policy, facts, TargetingReasonCode.CLASSIFICATION_STALE)
    taxonomy = policy.taxonomy
    if not taxonomy.reviewed:
        return _buyer_unknown(policy, facts, TargetingReasonCode.NO_COMPARISON_RULE)
    # The boolean is historical presentation metadata, not a runtime trust
    # authority. Only the approved, server-owned V1 artifact may evaluate.
    from backend_core.growth.buyer_taxonomy_v1 import is_trusted_buyer_taxonomy_v1

    if not is_trusted_buyer_taxonomy_v1(taxonomy):
        return _buyer_unknown(policy, facts, TargetingReasonCode.NO_COMPARISON_RULE)
    if facts.source_collection_job_id is None or not facts.source_collection_import_job_ids:
        return _buyer_unknown(policy, facts, TargetingReasonCode.COLLECTION_CONTEXT_MISSING)
    if not facts.creator_classification_import_job_ids:
        return _buyer_unknown(policy, facts, TargetingReasonCode.CREATOR_CLASSIFICATION_MISSING)
    collection_categories, collection_error = _normalize_collection_categories(taxonomy, facts)
    if collection_error is not None:
        return _buyer_unknown(policy, facts, TargetingReasonCode(collection_error))
    creator_categories, creator_error = _normalize_creator_categories(taxonomy, facts)
    if creator_error is not None:
        return _buyer_unknown(policy, facts, TargetingReasonCode(creator_error))
    assert collection_categories is not None
    assert creator_categories is not None

    relations = {
        taxonomy.relation(collection_category, creator_category)
        for collection_category in collection_categories
        for creator_category in creator_categories
    }
    evidence = {
        "schema_version": 1,
        "policy_type": policy.policy_type,
        "taxonomy_version": taxonomy.taxonomy_version,
        "account": {
            "influencer_id": facts.influencer_id,
            "platform_account_id": facts.platform_account_id,
            "platform": facts.platform,
        },
        "collection_context": {
            "source_collection_job_id": facts.source_collection_job_id,
            "industry": facts.collection_industry,
            "subdirection": facts.collection_subdirection,
            "normalized_categories": list(collection_categories),
            "provenance_import_job_ids": list(facts.source_collection_import_job_ids),
        },
        "creator_classification": {
            "normalized_categories": list(creator_categories),
            "provenance_import_job_ids": list(facts.creator_classification_import_job_ids),
        },
    }
    if "COMPATIBLE" in relations:
        return TargetingEvaluation(
            result=TargetingEvaluationResult.NOT_MATCH,
            reason_codes=(TargetingReasonCode.CATEGORY_ALIGNED,),
            redacted_evidence=_redacted(evidence),
        )
    if relations and relations == {"INCOMPATIBLE"}:
        return TargetingEvaluation(
            result=TargetingEvaluationResult.MATCH,
            reason_codes=(TargetingReasonCode.CATEGORY_MISMATCH,),
            redacted_evidence=_redacted(evidence),
        )
    return _buyer_unknown(policy, facts, TargetingReasonCode.NO_COMPARISON_RULE)


def _buyer_unknown_tier(
    policy: BuyerTargetingPolicy,
    *,
    reason_code: TargetingReasonCode,
) -> BuyerLeadTierDecision:
    """Keep unavailable Buyer facts visibly distinct from known no-rule differences."""

    taxonomy = policy.taxonomy
    return BuyerLeadTierDecision(
        tier=BuyerLeadTier.UNKNOWN,
        relation_summary={
            "schema_version": 1,
            "status": "UNRELIABLE",
            "reason_code": reason_code.value,
            "taxonomy": {
                "taxonomy_id": taxonomy.taxonomy_id,
                "taxonomy_version": taxonomy.taxonomy_version,
                "artifact_hash": taxonomy.artifact_hash,
            },
            "client_categories": [],
            "creator_categories": [],
            "pairs": [],
        },
    )


def evaluate_buyer_lead_tier(
    policy: BuyerTargetingPolicy,
    facts: CandidateFactBundle,
) -> BuyerLeadTierDecision:
    """Project trusted Buyer classification evidence into the sales lead tier.

    This deliberately leaves ``evaluate_buyer`` and its frozen MATCH/NOT_MATCH/
    UNKNOWN contract untouched.  In particular, a known pair without a
    comparison rule is a reliable ``CHANGED`` lead, not a data-unknown result.
    """

    legacy = evaluate_buyer(policy, facts)
    reason_code = legacy.reason_codes[0]
    if (
        legacy.result is TargetingEvaluationResult.UNKNOWN
        and reason_code is not TargetingReasonCode.NO_COMPARISON_RULE
    ):
        return _buyer_unknown_tier(policy, reason_code=reason_code)

    # NO_COMPARISON_RULE is overloaded in legacy output.  Re-establish the
    # trusted runtime boundary before treating it as a known category change.
    from backend_core.growth.buyer_taxonomy_v1 import is_trusted_buyer_taxonomy_v1

    taxonomy = policy.taxonomy
    if not taxonomy.reviewed or not is_trusted_buyer_taxonomy_v1(taxonomy):
        return _buyer_unknown_tier(policy, reason_code=TargetingReasonCode.NO_COMPARISON_RULE)
    collection_categories, collection_error = _normalize_collection_categories(taxonomy, facts)
    if collection_error is not None:
        return _buyer_unknown_tier(policy, reason_code=TargetingReasonCode(collection_error))
    creator_categories, creator_error = _normalize_creator_categories(taxonomy, facts)
    if creator_error is not None:
        return _buyer_unknown_tier(policy, reason_code=TargetingReasonCode(creator_error))
    assert collection_categories is not None
    assert creator_categories is not None

    pairs = tuple(
        {
            "client_category_id": client_category,
            "creator_category_id": creator_category,
            "relation": taxonomy.buyer_relation_detail(client_category, creator_category).value,
        }
        for client_category in collection_categories
        for creator_category in creator_categories
    )
    relations = {BuyerCategoryRelation(pair["relation"]) for pair in pairs}
    same_sets = collection_categories == creator_categories
    has_difference = any(relation is not BuyerCategoryRelation.EXACT for relation in relations)

    if same_sets and not has_difference:
        tier = BuyerLeadTier.SAME_CATEGORY
    elif relations <= {
        BuyerCategoryRelation.EXACT,
        BuyerCategoryRelation.PARENT_CHILD,
        BuyerCategoryRelation.COMPATIBLE,
    } and (
        BuyerCategoryRelation.PARENT_CHILD in relations
        or BuyerCategoryRelation.COMPATIBLE in relations
    ):
        tier = BuyerLeadTier.RELATED
    elif relations == {BuyerCategoryRelation.INCOMPATIBLE}:
        tier = BuyerLeadTier.HIGH
    else:
        # Known different categories that are mixed or unruled are an
        # intentionally lower-priority change lead, never a false HIGH.
        tier = BuyerLeadTier.CHANGED

    return BuyerLeadTierDecision(
        tier=tier,
        relation_summary={
            "schema_version": 1,
            "status": "RELIABLE",
            "taxonomy": {
                "taxonomy_id": taxonomy.taxonomy_id,
                "taxonomy_version": taxonomy.taxonomy_version,
                "artifact_hash": taxonomy.artifact_hash,
            },
            "client_categories": list(collection_categories),
            "creator_categories": list(creator_categories),
            "pairs": list(pairs),
        },
    )


def evaluate_targeting(
    policy: SellerTargetingPolicy | BuyerTargetingPolicy,
    facts: CandidateFactBundle,
    *,
    as_of: datetime | None = None,
    content_activity_freshness_policy: ContentActivityFreshnessPolicy | None = None,
    grey_dolphin_activity_freshness_policy: GreyDolphinActivityFreshnessPolicy | None = None,
) -> TargetingEvaluation:
    if isinstance(policy, SellerTargetingPolicy):
        return evaluate_seller(
            policy,
            facts,
            as_of=as_of,
            content_activity_freshness_policy=content_activity_freshness_policy,
            grey_dolphin_activity_freshness_policy=grey_dolphin_activity_freshness_policy,
        )
    return evaluate_buyer(policy, facts)


__all__ = [
    "AccountSignalFact",
    "AccountSignalValueState",
    "BuyerCategoryRelation",
    "BuyerLeadTierDecision",
    "BuyerTargetingPolicy",
    "ContentActivityConstraint",
    "ContentActivityFact",
    "CandidateFactBundle",
    "CollectionContextSnapshot",
    "CriterionEvaluation",
    "FreshnessConstraint",
    "FrozenTargetingContract",
    "IntegerRange",
    "GreyDolphinActivityFact",
    "LongInactivityConstraint",
    "SellerTargetingPolicy",
    "TargetingEvaluation",
    "TargetingEvaluationResult",
    "TargetingPolicyDefinition",
    "TargetingReasonCode",
    "TaxonomyAlias",
    "TaxonomyDefinition",
    "TaxonomyRelation",
    "evaluate_buyer",
    "evaluate_buyer_lead_tier",
    "evaluate_seller",
    "evaluate_targeting",
    "parse_targeting_policy",
    "reduce_criterion_results",
]

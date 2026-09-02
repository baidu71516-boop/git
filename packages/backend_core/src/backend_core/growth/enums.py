"""Closed persistence enums for Phase 3A growth aggregates."""

from enum import StrEnum


class CandidatePoolKind(StrEnum):
    POTENTIAL_SELLER = "POTENTIAL_SELLER"
    POTENTIAL_BUYER = "POTENTIAL_BUYER"


class CandidatePoolStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    ARCHIVED = "ARCHIVED"


class CandidatePoolRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CandidateResult(StrEnum):
    MATCH = "MATCH"
    NOT_MATCH = "NOT_MATCH"
    UNKNOWN = "UNKNOWN"


class BuyerLeadTier(StrEnum):
    """Buyer-only sales prioritization; it never asserts an account transaction."""

    HIGH = "HIGH"
    CHANGED = "CHANGED"
    RELATED = "RELATED"
    SAME_CATEGORY = "SAME_CATEGORY"
    UNKNOWN = "UNKNOWN"


class BuyerProspectRecentCollectionWindow(StrEnum):
    """Closed Market Prospect Rule V1 collection-age choices."""

    DAYS_7 = "7"
    DAYS_30 = "30"
    DAYS_60 = "60"
    DAYS_90 = "90"
    ALL = "ALL"

    @property
    def days(self) -> int | None:
        return None if self is BuyerProspectRecentCollectionWindow.ALL else int(self.value)


class BuyerProspectOwnerFilter(StrEnum):
    """Closed ownership selector for Market Prospect Rule V1."""

    ANY = "ANY"
    UNASSIGNED = "UNASSIGNED"
    OPERATOR = "OPERATOR"


class CampaignStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    CLOSED = "CLOSED"


class CampaignReviewMode(StrEnum):
    ALL = "ALL"
    FIRST_N = "FIRST_N"
    SAMPLE = "SAMPLE"
    AUTO = "AUTO"


class DuplicateHistoryPolicy(StrEnum):
    ALLOW_WITH_WARNING = "ALLOW_WITH_WARNING"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"
    BLOCK_WITHIN_WINDOW = "BLOCK_WITHIN_WINDOW"


class Phase3AOperationScope(StrEnum):
    CANDIDATE_POOL_CREATE = "CANDIDATE_POOL_CREATE"
    TARGETING_POLICY_CREATE = "TARGETING_POLICY_CREATE"
    CAMPAIGN_CREATE = "CAMPAIGN_CREATE"
    CAMPAIGN_MEMBER_BULK_ADD = "CAMPAIGN_MEMBER_BULK_ADD"
    OUTREACH_TARGET_CREATE = "OUTREACH_TARGET_CREATE"

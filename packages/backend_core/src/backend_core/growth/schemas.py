"""Closed service and HTTP contracts for Candidate Pool targeting."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from backend_core.campaigns.schemas import CampaignOwnerSummary
from backend_core.growth.enums import (
    BuyerLeadTier,
    CandidatePoolKind,
    CandidatePoolRunStatus,
    CandidatePoolStatus,
    CandidateResult,
)
from backend_core.growth.targeting import (
    SellerTargetingPolicy,
    TargetingPolicyDefinition,
    TargetingReasonCode,
)
from backend_core.influencers.schemas import (
    InfluencerIdentitySummary,
    PlatformAccountIdentitySummary,
)


class TargetingWriteContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TargetingReadContract(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class CandidatePoolCreateInput(TargetingWriteContract):
    name: StrictStr = Field(min_length=1, max_length=200)
    kind: CandidatePoolKind
    source_collection_job_id: UUID | None = None
    owner_operator_id: UUID | None = None
    policy: TargetingPolicyDefinition


class TargetingPolicyCreateInput(TargetingWriteContract):
    policy: TargetingPolicyDefinition


class LongInactivityEnrichmentRequest(TargetingWriteContract):
    """Explicit per-run capacity controls for D1A.1 provider resolution."""

    planned_assignable_target: StrictInt = Field(ge=1, le=10_000)
    max_provider_enrichment: StrictInt = Field(ge=0, le=100)


class CandidatePoolRunRequest(TargetingWriteContract):
    """Closed C3A run shapes plus the existing D1A.1 execution input.

    ``{}`` resolves the Pool's current policy. ``{policy_id}`` reuses one
    historical policy belonging to this Pool. The third shape is the only
    authoring path: it compares the current Pool pointer/version under lock,
    then inserts a new Seller policy and reserves its Run atomically.

    ``{long_inactivity_enrichment}`` remains the existing opt-in execution
    path for the Pool's current D1A.1 policy; it cannot be combined with
    policy reuse or C3A authoring.
    """

    policy_id: UUID | None = None
    base_policy_id: UUID | None = None
    expected_pool_version: StrictInt | None = Field(default=None, ge=1)
    policy: SellerTargetingPolicy | None = None
    long_inactivity_enrichment: LongInactivityEnrichmentRequest | None = None

    @model_validator(mode="after")
    def require_one_closed_shape(self) -> Self:
        supplied = self.model_fields_set
        if not supplied:
            return self
        if supplied == {"policy_id"} and self.policy_id is not None:
            return self
        if (
            supplied == {"long_inactivity_enrichment"}
            and self.long_inactivity_enrichment is not None
        ):
            return self
        if supplied == {"base_policy_id", "expected_pool_version", "policy"} and (
            self.base_policy_id is not None
            and self.expected_pool_version is not None
            and self.policy is not None
        ):
            return self
        raise ValueError(
            "request must be {}, {policy_id}, {long_inactivity_enrichment}, or "
            "{base_policy_id, expected_pool_version, policy}"
        )

    @property
    def is_current_policy_run(self) -> bool:
        return not self.model_fields_set

    @property
    def is_historical_policy_run(self) -> bool:
        return self.model_fields_set == {"policy_id"}

    @property
    def is_adjusted_policy_run(self) -> bool:
        return self.model_fields_set == {"base_policy_id", "expected_pool_version", "policy"}

    @property
    def is_long_inactivity_current_policy_run(self) -> bool:
        return self.model_fields_set == {"long_inactivity_enrichment"}


class CandidatePoolPublic(TargetingReadContract):
    id: UUID
    department_id: UUID
    owner_operator_id: UUID
    owner: CampaignOwnerSummary
    name: str
    kind: CandidatePoolKind
    source_collection_job_id: UUID | None
    status: CandidatePoolStatus
    current_policy_id: UUID | None
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
        return cls.model_validate(
            {
                field_name: owner if field_name == "owner" else getattr(model, field_name)
                for field_name in cls.model_fields
            }
        )

    @classmethod
    def from_replay_payload(
        cls,
        payload: Mapping[str, object],
        *,
        owner: CampaignOwnerSummary,
    ) -> Self:
        """Enrich v1 create replays with live canonical owner identity."""

        return cls.model_validate({**payload, "owner": owner})


class TargetingPolicyPublic(TargetingReadContract):
    id: UUID
    pool_id: UUID
    version: int
    schema_version: int
    definition: TargetingPolicyDefinition
    canonical_hash: str
    created_by_operator_id: UUID
    created_at: datetime
    updated_at: datetime


class TargetingPolicyCreateResultPublic(TargetingReadContract):
    """Bounded, redacted create result used for durable Policy replay."""

    id: UUID
    pool_id: UUID
    version: int
    schema_version: int
    canonical_hash: str
    created_by_operator_id: UUID
    created_at: datetime


class CandidatePoolRunPublic(TargetingReadContract):
    id: UUID
    pool_id: UUID
    policy_id: UUID
    as_of: datetime
    input_watermark: dict[str, Any] | None
    status: CandidatePoolRunStatus
    match_count: int
    unknown_count: int
    not_match_count: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    idempotent_replay: bool = False


class BuyerScreeningBootstrapPublic(TargetingReadContract):
    """Server-derived Buyer bootstrap result; no policy authoring input exists."""

    pool: CandidatePoolPublic
    policy: TargetingPolicyCreateResultPublic
    run: CandidatePoolRunPublic
    reused_existing_pool: bool


class CandidatePoolRunPage(TargetingReadContract):
    items: tuple[CandidatePoolRunPublic, ...]
    next_cursor: UUID | None = None


class CandidatePoolMemberPublic(TargetingReadContract):
    id: UUID
    run_id: UUID
    influencer_id: UUID
    platform_account_id: UUID
    result: CandidateResult
    buyer_lead_tier: BuyerLeadTier | None = None
    buyer_relation_summary: dict[str, Any] | None = None
    reason_codes: tuple[TargetingReasonCode, ...]
    redacted_evidence: dict[str, Any]
    evidence_hash: str
    created_at: datetime
    updated_at: datetime
    influencer: InfluencerIdentitySummary
    platform_account: PlatformAccountIdentitySummary

    @model_validator(mode="after")
    def require_consistent_identity_projection(self) -> Self:
        if self.influencer.id != self.influencer_id:
            raise ValueError("influencer.id must match influencer_id")
        if self.platform_account.id != self.platform_account_id:
            raise ValueError("platform_account.id must match platform_account_id")
        return self

    @classmethod
    def from_projection(
        cls,
        model: object,
        *,
        influencer: InfluencerIdentitySummary,
        platform_account: PlatformAccountIdentitySummary,
        reason_codes: tuple[TargetingReasonCode, ...],
        redacted_evidence: dict[str, Any],
    ) -> Self:
        projection = {
            field_name: getattr(model, field_name)
            for field_name in cls.model_fields
            if field_name
            not in {"influencer", "platform_account", "reason_codes", "redacted_evidence"}
        }
        projection.update(
            influencer=influencer,
            platform_account=platform_account,
            reason_codes=reason_codes,
            redacted_evidence=redacted_evidence,
        )
        return cls.model_validate(projection)


class CandidatePoolPage(TargetingReadContract):
    items: tuple[CandidatePoolPublic, ...]
    next_cursor: UUID | None = None


class CandidatePoolRunMemberPage(TargetingReadContract):
    items: tuple[CandidatePoolMemberPublic, ...]
    next_cursor: UUID | None = None


def viewer_redacted_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Mask contact-derived evidence even though raw Contact values never exist here."""

    contact_keys = frozenset({"current_contact_count", "current_contact_types"})

    def redact(value: Any, key: str | None = None) -> Any:
        if key in contact_keys:
            return "***"
        if isinstance(value, dict):
            # A Viewer may inspect the evaluation outcome, but not which Contact
            # capability was configured or observed for that account.
            if value.get("criterion") == "contact_availability":
                return {
                    item_key: (
                        "CONTACT_EVIDENCE_REDACTED"
                        if item_key == "reason_code"
                        else (
                            "***"
                            if item_key in {"configured", "observed"}
                            else redact(item_value, item_key)
                        )
                    )
                    for item_key, item_value in value.items()
                }
            return {
                item_key: redact(item_value, item_key) for item_key, item_value in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    return cast(dict[str, Any], redact(evidence))


def viewer_redacted_reason_codes(
    reason_codes: tuple[TargetingReasonCode, ...],
) -> tuple[TargetingReasonCode, ...]:
    """Mask Contact-derived result reasons in Viewer member DTOs."""

    contact_reason_codes = frozenset(
        {
            TargetingReasonCode.CONTACT_AVAILABLE,
            TargetingReasonCode.EMAIL_AVAILABLE,
            TargetingReasonCode.NO_CURRENT_CONTACT,
            TargetingReasonCode.NO_CURRENT_EMAIL,
        }
    )
    return tuple(
        TargetingReasonCode.CONTACT_EVIDENCE_REDACTED if code in contact_reason_codes else code
        for code in reason_codes
    )


__all__ = [
    "BuyerScreeningBootstrapPublic",
    "CandidatePoolCreateInput",
    "CandidatePoolMemberPublic",
    "CandidatePoolPage",
    "CandidatePoolPublic",
    "CandidatePoolRunMemberPage",
    "CandidatePoolRunPage",
    "CandidatePoolRunPublic",
    "CandidatePoolRunRequest",
    "LongInactivityEnrichmentRequest",
    "TargetingPolicyCreateInput",
    "TargetingPolicyCreateResultPublic",
    "TargetingPolicyPublic",
    "viewer_redacted_evidence",
    "viewer_redacted_reason_codes",
]

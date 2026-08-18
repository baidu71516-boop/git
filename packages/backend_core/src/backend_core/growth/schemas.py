"""Closed service and HTTP contracts for Candidate Pool targeting."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from backend_core.growth.enums import (
    CandidatePoolKind,
    CandidatePoolRunStatus,
    CandidatePoolStatus,
    CandidateResult,
)
from backend_core.growth.targeting import TargetingPolicyDefinition, TargetingReasonCode


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


class CandidatePoolRunRequest(TargetingWriteContract):
    """Deliberately empty; the server resolves policy and captures as_of."""


class CandidatePoolPublic(TargetingReadContract):
    id: UUID
    department_id: UUID
    owner_operator_id: UUID
    name: str
    kind: CandidatePoolKind
    source_collection_job_id: UUID | None
    status: CandidatePoolStatus
    current_policy_id: UUID | None
    version: int
    created_at: datetime
    updated_at: datetime


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


class CandidatePoolRunPage(TargetingReadContract):
    items: tuple[CandidatePoolRunPublic, ...]
    next_cursor: UUID | None = None


class CandidatePoolMemberPublic(TargetingReadContract):
    id: UUID
    run_id: UUID
    influencer_id: UUID
    platform_account_id: UUID
    result: CandidateResult
    reason_codes: tuple[TargetingReasonCode, ...]
    redacted_evidence: dict[str, Any]
    evidence_hash: str
    created_at: datetime
    updated_at: datetime


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
    "CandidatePoolCreateInput",
    "CandidatePoolMemberPublic",
    "CandidatePoolPage",
    "CandidatePoolPublic",
    "CandidatePoolRunMemberPage",
    "CandidatePoolRunPage",
    "CandidatePoolRunPublic",
    "CandidatePoolRunRequest",
    "TargetingPolicyCreateInput",
    "TargetingPolicyCreateResultPublic",
    "TargetingPolicyPublic",
    "viewer_redacted_evidence",
    "viewer_redacted_reason_codes",
]

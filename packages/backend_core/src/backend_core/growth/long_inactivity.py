"""Explicit, bounded execution planning for D1A.1 Long Inactivity.

This module has no database, HTTP, queue, or provider dependency.  An upper
Candidate execution planner supplies its authoritative order and invokes the
single-candidate resolver only after the cheap routing result is unknown.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, model_validator

from backend_core.growth.targeting import (
    ContentActivityFact,
    FrozenTargetingContract,
    TargetingEvaluationResult,
)


class LongInactivityEvidenceSource(StrEnum):
    TRUSTED_CONTENT_ACTIVITY = "TRUSTED_CONTENT_ACTIVITY"
    GREY_DOLPHIN = "GREY_DOLPHIN"
    UNKNOWN = "UNKNOWN"


class LongInactivityExecutionRequest(FrozenTargetingContract):
    """Execution-only capacity controls; never a persisted Seller rule."""

    planned_assignable_target: StrictInt = Field(ge=1, le=10_000)
    already_fully_eligible: StrictInt = Field(ge=0, le=10_000)
    max_provider_enrichment: StrictInt = Field(ge=0, le=100)


class LongInactivityExecutionCandidate(FrozenTargetingContract):
    """One cheaply evaluated candidate in authoritative stable order."""

    platform_account_id: UUID
    order: StrictInt = Field(ge=0)
    local_result: TargetingEvaluationResult
    evidence_source: LongInactivityEvidenceSource
    eligible_if_activity_resolved: StrictBool

    @model_validator(mode="after")
    def validate_routing_state(self) -> LongInactivityExecutionCandidate:
        if self.local_result is TargetingEvaluationResult.UNKNOWN:
            if self.evidence_source is not LongInactivityEvidenceSource.UNKNOWN:
                raise ValueError("unknown local result requires UNKNOWN evidence source")
        elif self.evidence_source is LongInactivityEvidenceSource.UNKNOWN:
            raise ValueError("resolved local result requires a concrete evidence source")
        return self


class ProviderEnrichmentOutcome(FrozenTargetingContract):
    """One explicit provider attempt plus the upper planner's full-policy result."""

    long_inactivity_result: TargetingEvaluationResult
    full_policy_result: TargetingEvaluationResult


@dataclass(frozen=True, slots=True)
class LongInactivityProviderRefresh:
    """One provider attempt's safe activity fact plus acceptance provenance.

    A failed refresh may return a preserved older trusted projection. Only an
    exact identity match to this attempt's newly accepted trusted observation
    authorizes the Candidate planner to use the returned fact for eligibility.
    """

    content_activity: ContentActivityFact | None
    accepted_trusted_observation: bool


@dataclass(frozen=True, slots=True)
class LongInactivityExecutionMetrics:
    candidates_evaluated: int
    resolved_from_content_activity_cache: int
    resolved_from_grey_dolphin: int
    remained_unknown: int
    provider_calls_attempted: int
    provider_calls_avoided_by_grey_dolphin: int
    stopped_by_planned_target: bool
    stopped_by_provider_budget: bool


@dataclass(frozen=True, slots=True)
class LongInactivityExecutionResult:
    """Auditable result; no provider values or credentials are retained."""

    attempted_platform_account_ids: tuple[UUID, ...]
    fully_eligible_count: int
    metrics: LongInactivityExecutionMetrics


ProviderEnricher = Callable[
    [LongInactivityExecutionCandidate], Awaitable[ProviderEnrichmentOutcome]
]


async def execute_bounded_long_inactivity_enrichment(
    request: LongInactivityExecutionRequest,
    candidates: Iterable[LongInactivityExecutionCandidate],
    *,
    enrich_one: ProviderEnricher,
) -> LongInactivityExecutionResult:
    """Resolve only necessary unknowns and stop on full-policy eligibility.

    The injected resolver must perform one explicitly governed provider action
    and then obtain the *complete* Candidate-policy result.  This separation
    prevents a raw activity match from being miscounted as assignable.
    """

    ordered = tuple(candidates)
    if len({candidate.platform_account_id for candidate in ordered}) != len(ordered):
        raise ValueError("execution candidates must not contain duplicate platform accounts")
    if any(candidate.order != index for index, candidate in enumerate(ordered)):
        raise ValueError("execution candidates must use contiguous authoritative order")

    cache_resolved = sum(
        candidate.local_result is not TargetingEvaluationResult.UNKNOWN
        and candidate.evidence_source is LongInactivityEvidenceSource.TRUSTED_CONTENT_ACTIVITY
        for candidate in ordered
    )
    grey_resolved = sum(
        candidate.local_result is not TargetingEvaluationResult.UNKNOWN
        and candidate.evidence_source is LongInactivityEvidenceSource.GREY_DOLPHIN
        for candidate in ordered
    )
    unknown_after = sum(
        candidate.local_result is TargetingEvaluationResult.UNKNOWN for candidate in ordered
    )
    fully_eligible = request.already_fully_eligible
    attempted: list[UUID] = []
    stopped_by_target = fully_eligible >= request.planned_assignable_target
    stopped_by_budget = False

    for candidate in ordered:
        if fully_eligible >= request.planned_assignable_target:
            stopped_by_target = True
            break
        if (
            candidate.local_result is not TargetingEvaluationResult.UNKNOWN
            or not candidate.eligible_if_activity_resolved
        ):
            continue
        if len(attempted) >= request.max_provider_enrichment:
            stopped_by_budget = True
            break
        attempted.append(candidate.platform_account_id)
        outcome = await enrich_one(candidate)
        if outcome.long_inactivity_result is not TargetingEvaluationResult.UNKNOWN:
            unknown_after -= 1
        if outcome.full_policy_result is TargetingEvaluationResult.MATCH:
            fully_eligible += 1

    # These are terminal-state facts, rather than loop-control side effects.
    # In particular, the final provider attempt can independently exhaust the
    # allowance, reach the full-policy target, or do both at once.
    stopped_by_target = fully_eligible >= request.planned_assignable_target
    stopped_by_budget = len(attempted) >= request.max_provider_enrichment

    return LongInactivityExecutionResult(
        attempted_platform_account_ids=tuple(attempted),
        fully_eligible_count=fully_eligible,
        metrics=LongInactivityExecutionMetrics(
            candidates_evaluated=len(ordered),
            resolved_from_content_activity_cache=cache_resolved,
            resolved_from_grey_dolphin=grey_resolved,
            remained_unknown=unknown_after,
            provider_calls_attempted=len(attempted),
            provider_calls_avoided_by_grey_dolphin=grey_resolved,
            stopped_by_planned_target=stopped_by_target,
            stopped_by_provider_budget=stopped_by_budget,
        ),
    )


__all__ = [
    "LongInactivityEvidenceSource",
    "LongInactivityExecutionCandidate",
    "LongInactivityExecutionMetrics",
    "LongInactivityExecutionRequest",
    "LongInactivityExecutionResult",
    "LongInactivityProviderRefresh",
    "ProviderEnrichmentOutcome",
    "execute_bounded_long_inactivity_enrichment",
]

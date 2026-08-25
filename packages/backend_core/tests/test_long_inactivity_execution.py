"""D1A.1 bounded execution planning tests."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from backend_core.growth.long_inactivity import (
    LongInactivityEvidenceSource,
    LongInactivityExecutionCandidate,
    LongInactivityExecutionRequest,
    ProviderEnrichmentOutcome,
    execute_bounded_long_inactivity_enrichment,
)
from backend_core.growth.targeting import TargetingEvaluationResult


def _candidate(
    order: int,
    *,
    result: TargetingEvaluationResult = TargetingEvaluationResult.UNKNOWN,
    source: LongInactivityEvidenceSource = LongInactivityEvidenceSource.UNKNOWN,
    eligible_if_resolved: bool = True,
) -> LongInactivityExecutionCandidate:
    return LongInactivityExecutionCandidate(
        platform_account_id=UUID(int=order + 1),
        order=order,
        local_result=result,
        evidence_source=source,
        eligible_if_activity_resolved=eligible_if_resolved,
    )


def _run(
    request: LongInactivityExecutionRequest,
    candidates: tuple[LongInactivityExecutionCandidate, ...],
    outcomes: tuple[ProviderEnrichmentOutcome, ...] = (),
) -> tuple[object, list[UUID]]:
    calls: list[UUID] = []
    pending = iter(outcomes)

    async def enrich_one(candidate: LongInactivityExecutionCandidate) -> ProviderEnrichmentOutcome:
        calls.append(candidate.platform_account_id)
        return next(pending)

    return (
        asyncio.run(
            execute_bounded_long_inactivity_enrichment(request, candidates, enrich_one=enrich_one)
        ),
        calls,
    )


def test_grey_dolphin_resolutions_short_circuit_provider_and_are_counted() -> None:
    result, calls = _run(
        LongInactivityExecutionRequest(
            planned_assignable_target=2,
            already_fully_eligible=0,
            max_provider_enrichment=2,
        ),
        (
            _candidate(
                0,
                result=TargetingEvaluationResult.MATCH,
                source=LongInactivityEvidenceSource.GREY_DOLPHIN,
            ),
            _candidate(
                1,
                result=TargetingEvaluationResult.NOT_MATCH,
                source=LongInactivityEvidenceSource.GREY_DOLPHIN,
            ),
        ),
    )

    assert calls == []
    assert result.metrics.resolved_from_grey_dolphin == 2
    assert result.metrics.provider_calls_avoided_by_grey_dolphin == 2
    assert result.metrics.provider_calls_attempted == 0


def test_trusted_cache_resolution_short_circuits_provider() -> None:
    result, calls = _run(
        LongInactivityExecutionRequest(
            planned_assignable_target=1,
            already_fully_eligible=0,
            max_provider_enrichment=1,
        ),
        (
            _candidate(
                0,
                result=TargetingEvaluationResult.MATCH,
                source=LongInactivityEvidenceSource.TRUSTED_CONTENT_ACTIVITY,
            ),
        ),
    )

    assert calls == []
    assert result.metrics.resolved_from_content_activity_cache == 1
    assert result.metrics.provider_calls_attempted == 0


def test_unknown_candidate_with_no_provider_budget_stays_unknown_without_a_call() -> None:
    result, calls = _run(
        LongInactivityExecutionRequest(
            planned_assignable_target=1,
            already_fully_eligible=0,
            max_provider_enrichment=0,
        ),
        (_candidate(0),),
    )

    assert calls == []
    assert result.metrics.remained_unknown == 1
    assert result.metrics.provider_calls_attempted == 0
    assert result.metrics.stopped_by_provider_budget is True


def test_planned_target_already_met_makes_zero_provider_calls() -> None:
    result, calls = _run(
        LongInactivityExecutionRequest(
            planned_assignable_target=30,
            already_fully_eligible=30,
            max_provider_enrichment=50,
        ),
        (_candidate(0), _candidate(1)),
    )

    assert calls == []
    assert result.metrics.stopped_by_planned_target is True
    assert result.metrics.provider_calls_attempted == 0


def test_provider_stops_when_full_candidate_policy_reaches_target_midway() -> None:
    candidates = (_candidate(0), _candidate(1), _candidate(2), _candidate(3))
    result, calls = _run(
        LongInactivityExecutionRequest(
            planned_assignable_target=30,
            already_fully_eligible=28,
            max_provider_enrichment=50,
        ),
        candidates,
        (
            ProviderEnrichmentOutcome(
                long_inactivity_result=TargetingEvaluationResult.MATCH,
                full_policy_result=TargetingEvaluationResult.NOT_MATCH,
            ),
            ProviderEnrichmentOutcome(
                long_inactivity_result=TargetingEvaluationResult.MATCH,
                full_policy_result=TargetingEvaluationResult.MATCH,
            ),
            ProviderEnrichmentOutcome(
                long_inactivity_result=TargetingEvaluationResult.MATCH,
                full_policy_result=TargetingEvaluationResult.MATCH,
            ),
        ),
    )

    assert calls == [candidate.platform_account_id for candidate in candidates[:3]]
    assert result.fully_eligible_count == 30
    assert result.metrics.stopped_by_planned_target is True


def test_activity_matches_do_not_satisfy_assignable_target_without_full_policy_match() -> None:
    candidates = (_candidate(0), _candidate(1))
    result, calls = _run(
        LongInactivityExecutionRequest(
            planned_assignable_target=30,
            already_fully_eligible=29,
            max_provider_enrichment=2,
        ),
        candidates,
        (
            ProviderEnrichmentOutcome(
                long_inactivity_result=TargetingEvaluationResult.MATCH,
                full_policy_result=TargetingEvaluationResult.NOT_MATCH,
            ),
            ProviderEnrichmentOutcome(
                long_inactivity_result=TargetingEvaluationResult.MATCH,
                full_policy_result=TargetingEvaluationResult.MATCH,
            ),
        ),
    )

    assert calls == [candidate.platform_account_id for candidate in candidates]
    assert result.fully_eligible_count == 30


def test_provider_budget_stops_and_preserves_remaining_unknown_candidates() -> None:
    candidates = (_candidate(0), _candidate(1), _candidate(2))
    result, calls = _run(
        LongInactivityExecutionRequest(
            planned_assignable_target=3,
            already_fully_eligible=0,
            max_provider_enrichment=1,
        ),
        candidates,
        (
            ProviderEnrichmentOutcome(
                long_inactivity_result=TargetingEvaluationResult.UNKNOWN,
                full_policy_result=TargetingEvaluationResult.UNKNOWN,
            ),
        ),
    )

    assert calls == [candidates[0].platform_account_id]
    assert result.metrics.stopped_by_provider_budget is True
    assert result.metrics.remained_unknown == 3


def test_execution_order_is_deterministic_and_rejects_non_authoritative_input() -> None:
    request = LongInactivityExecutionRequest(
        planned_assignable_target=1,
        already_fully_eligible=0,
        max_provider_enrichment=2,
    )
    result, calls = _run(
        request,
        (_candidate(0), _candidate(1)),
        (
            ProviderEnrichmentOutcome(
                long_inactivity_result=TargetingEvaluationResult.MATCH,
                full_policy_result=TargetingEvaluationResult.MATCH,
            ),
        ),
    )
    assert calls == [UUID(int=1)]
    assert result.attempted_platform_account_ids == (UUID(int=1),)

    async def enrich_one(_: LongInactivityExecutionCandidate) -> ProviderEnrichmentOutcome:
        raise AssertionError("must not be called")

    with pytest.raises(ValueError, match="contiguous authoritative order"):
        asyncio.run(
            execute_bounded_long_inactivity_enrichment(
                request,
                (_candidate(1),),
                enrich_one=enrich_one,
            )
        )


def test_final_candidate_reaching_target_sets_terminal_target_flag() -> None:
    result, _calls = _run(
        LongInactivityExecutionRequest(
            planned_assignable_target=2,
            already_fully_eligible=1,
            max_provider_enrichment=3,
        ),
        (_candidate(0),),
        (
            ProviderEnrichmentOutcome(
                long_inactivity_result=TargetingEvaluationResult.MATCH,
                full_policy_result=TargetingEvaluationResult.MATCH,
            ),
        ),
    )

    assert result.metrics.stopped_by_planned_target is True
    assert result.metrics.stopped_by_provider_budget is False


def test_final_candidate_consuming_budget_sets_terminal_budget_flag() -> None:
    result, _calls = _run(
        LongInactivityExecutionRequest(
            planned_assignable_target=3,
            already_fully_eligible=0,
            max_provider_enrichment=1,
        ),
        (_candidate(0),),
        (
            ProviderEnrichmentOutcome(
                long_inactivity_result=TargetingEvaluationResult.NOT_MATCH,
                full_policy_result=TargetingEvaluationResult.NOT_MATCH,
            ),
        ),
    )

    assert result.metrics.stopped_by_planned_target is False
    assert result.metrics.stopped_by_provider_budget is True


def test_final_candidate_can_reach_target_and_budget_together() -> None:
    result, _calls = _run(
        LongInactivityExecutionRequest(
            planned_assignable_target=1,
            already_fully_eligible=0,
            max_provider_enrichment=1,
        ),
        (_candidate(0),),
        (
            ProviderEnrichmentOutcome(
                long_inactivity_result=TargetingEvaluationResult.MATCH,
                full_policy_result=TargetingEvaluationResult.MATCH,
            ),
        ),
    )

    assert result.metrics.stopped_by_planned_target is True
    assert result.metrics.stopped_by_provider_budget is True


def test_terminal_flags_remain_false_when_target_and_budget_are_not_reached() -> None:
    result, _calls = _run(
        LongInactivityExecutionRequest(
            planned_assignable_target=2,
            already_fully_eligible=0,
            max_provider_enrichment=2,
        ),
        (_candidate(0),),
        (
            ProviderEnrichmentOutcome(
                long_inactivity_result=TargetingEvaluationResult.UNKNOWN,
                full_policy_result=TargetingEvaluationResult.UNKNOWN,
            ),
        ),
    )

    assert result.metrics.stopped_by_planned_target is False
    assert result.metrics.stopped_by_provider_budget is False


def test_zero_provider_budget_is_a_terminal_budget_state_without_calls() -> None:
    result, calls = _run(
        LongInactivityExecutionRequest(
            planned_assignable_target=1,
            already_fully_eligible=0,
            max_provider_enrichment=0,
        ),
        (_candidate(0),),
    )

    assert calls == []
    assert result.metrics.stopped_by_planned_target is False
    assert result.metrics.stopped_by_provider_budget is True

from datetime import UTC, datetime, timedelta

import pytest
from backend_core.influencers.freshness import FreshnessPolicy, FreshnessStatus
from backend_core.refresh.enums import RefreshPriorityReason
from backend_core.refresh.priority import (
    POLICY_VERSION,
    RefreshPriorityDecision,
    evaluate_refresh_priority,
)

AS_OF = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


@pytest.fixture
def policy() -> FreshnessPolicy:
    # Non-default thresholds prove priority delegates boundary truth to this policy.
    return FreshnessPolicy.from_day_thresholds(fresh_days=1, aging_days=2, stale_days=3)


@pytest.mark.parametrize(
    ("observed_at", "expected_tier", "expected_status", "expected_reason"),
    [
        (None, 1, FreshnessStatus.UNKNOWN, RefreshPriorityReason.FRESHNESS_UNKNOWN),
        (
            AS_OF - timedelta(days=3, microseconds=1),
            2,
            FreshnessStatus.VERY_STALE,
            RefreshPriorityReason.VERY_STALE,
        ),
        (
            AS_OF - timedelta(days=2, microseconds=1),
            3,
            FreshnessStatus.STALE,
            RefreshPriorityReason.STALE,
        ),
        (
            AS_OF - timedelta(days=1, microseconds=1),
            4,
            FreshnessStatus.AGING,
            RefreshPriorityReason.AGING,
        ),
    ],
)
def test_priority_maps_shared_freshness_policy_to_frozen_tiers(
    policy: FreshnessPolicy,
    observed_at: datetime | None,
    expected_tier: int,
    expected_status: FreshnessStatus,
    expected_reason: RefreshPriorityReason,
) -> None:
    decision = evaluate_refresh_priority(
        policy=policy,
        observed_at=observed_at,
        as_of=AS_OF,
        followers_count=10,
    )

    assert decision == RefreshPriorityDecision(
        tier=expected_tier,
        reasons=(expected_reason,),
        freshness_status=expected_status,
        followers_count=10,
    )


@pytest.mark.parametrize(
    ("observed_at", "primary_reason"),
    [
        (None, RefreshPriorityReason.FRESHNESS_UNKNOWN),
        (AS_OF - timedelta(days=4), RefreshPriorityReason.VERY_STALE),
        (AS_OF - timedelta(days=2, microseconds=1), RefreshPriorityReason.STALE),
        (AS_OF - timedelta(days=1, microseconds=1), RefreshPriorityReason.AGING),
    ],
)
def test_missing_followers_is_appended_after_primary_freshness_reason(
    policy: FreshnessPolicy,
    observed_at: datetime | None,
    primary_reason: RefreshPriorityReason,
) -> None:
    decision = evaluate_refresh_priority(
        policy=policy,
        observed_at=observed_at,
        as_of=AS_OF,
        followers_count=None,
    )

    assert decision is not None
    assert decision.reasons == (primary_reason, RefreshPriorityReason.FOLLOWERS_MISSING)


def test_fresh_with_missing_followers_is_tier_five(policy: FreshnessPolicy) -> None:
    assert evaluate_refresh_priority(
        policy=policy,
        observed_at=AS_OF - timedelta(hours=1),
        as_of=AS_OF,
        followers_count=None,
    ) == RefreshPriorityDecision(
        tier=5,
        reasons=(RefreshPriorityReason.FOLLOWERS_MISSING,),
        freshness_status=FreshnessStatus.FRESH,
        followers_count=None,
    )


@pytest.mark.parametrize("followers_count", [0, 1, 1_000_000])
def test_fresh_with_real_followers_is_not_a_candidate(
    policy: FreshnessPolicy,
    followers_count: int,
) -> None:
    assert (
        evaluate_refresh_priority(
            policy=policy,
            observed_at=AS_OF,
            as_of=AS_OF,
            followers_count=followers_count,
        )
        is None
    )


@pytest.mark.parametrize("followers_count", [True, 1.0, "1"])
def test_priority_rejects_non_integer_followers(
    policy: FreshnessPolicy,
    followers_count: object,
) -> None:
    with pytest.raises(TypeError, match="integer"):
        evaluate_refresh_priority(
            policy=policy,
            observed_at=AS_OF,
            as_of=AS_OF,
            followers_count=followers_count,  # type: ignore[arg-type]
        )


def test_priority_rejects_negative_followers(policy: FreshnessPolicy) -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        evaluate_refresh_priority(
            policy=policy,
            observed_at=AS_OF,
            as_of=AS_OF,
            followers_count=-1,
        )


def test_policy_version_is_frozen() -> None:
    assert POLICY_VERSION == 1

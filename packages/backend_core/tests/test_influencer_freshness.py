from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from backend_core.influencers.freshness import (
    ContentActivityFreshnessPolicy,
    FreshnessEvaluation,
    FreshnessPolicy,
    FreshnessStatus,
    InfluencerFreshnessSummary,
)

AS_OF = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


@pytest.fixture
def policy() -> FreshnessPolicy:
    result = FreshnessPolicy.from_day_thresholds(
        fresh_days=7,
        aging_days=30,
        stale_days=90,
    )
    assert result.fresh_duration == timedelta(days=7)
    assert result.aging_duration == timedelta(days=30)
    assert result.stale_duration == timedelta(days=90)
    return result


@pytest.mark.parametrize(
    ("elapsed", "expected_status", "expected_age_days"),
    [
        (timedelta(0), FreshnessStatus.FRESH, 0),
        (timedelta(days=7), FreshnessStatus.FRESH, 7),
        (timedelta(days=7, microseconds=1), FreshnessStatus.AGING, 7),
        (timedelta(days=30), FreshnessStatus.AGING, 30),
        (timedelta(days=30, microseconds=1), FreshnessStatus.STALE, 30),
        (timedelta(days=90), FreshnessStatus.STALE, 90),
        (timedelta(days=90, microseconds=1), FreshnessStatus.VERY_STALE, 90),
    ],
)
def test_evaluate_uses_exact_elapsed_duration_boundaries(
    policy: FreshnessPolicy,
    elapsed: timedelta,
    expected_status: FreshnessStatus,
    expected_age_days: int,
) -> None:
    evaluation = policy.evaluate(AS_OF - elapsed, as_of=AS_OF)

    assert evaluation.status is expected_status
    assert evaluation.age_days == expected_age_days
    assert evaluation.requires_refresh == (
        expected_status in {FreshnessStatus.STALE, FreshnessStatus.VERY_STALE}
    )


def test_evaluate_unknown_requires_refresh(policy: FreshnessPolicy) -> None:
    assert policy.evaluate(None, as_of=AS_OF) == FreshnessEvaluation(
        status=FreshnessStatus.UNKNOWN,
        age_days=None,
        requires_refresh=True,
    )


def test_evaluate_normalizes_aware_timestamps_to_utc(policy: FreshnessPolicy) -> None:
    observed_at = (AS_OF - timedelta(days=7, microseconds=1)).astimezone(
        timezone(timedelta(hours=8))
    )

    evaluation = policy.evaluate(observed_at, AS_OF)

    assert evaluation.status is FreshnessStatus.AGING
    assert evaluation.age_days == 7


def test_evaluate_uses_elapsed_utc_across_dst_transition(policy: FreshnessPolicy) -> None:
    as_of = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
    observed_at = (as_of - timedelta(days=7, microseconds=1)).astimezone(
        ZoneInfo("America/New_York")
    )

    evaluation = policy.evaluate(observed_at, as_of)

    assert evaluation.status is FreshnessStatus.AGING
    assert evaluation.age_days == 7


@pytest.mark.parametrize(
    ("observed_at", "as_of"),
    [
        (datetime(2026, 8, 1, 12, 0), AS_OF),
        (datetime(2026, 8, 1, 12, 0, tzinfo=UTC), datetime(2026, 8, 13, 12, 0)),
        (None, datetime(2026, 8, 13, 12, 0)),
    ],
)
def test_evaluate_rejects_timezone_naive_instants(
    policy: FreshnessPolicy,
    observed_at: datetime | None,
    as_of: datetime,
) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        policy.evaluate(observed_at, as_of=as_of)


def test_evaluate_clamps_future_observation_to_zero_age(policy: FreshnessPolicy) -> None:
    evaluation = policy.evaluate(AS_OF + timedelta(days=365), as_of=AS_OF)

    assert evaluation == FreshnessEvaluation(
        status=FreshnessStatus.FRESH,
        age_days=0,
        requires_refresh=False,
    )


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (
            [FreshnessStatus.FRESH, FreshnessStatus.AGING],
            InfluencerFreshnessSummary(FreshnessStatus.AGING, False),
        ),
        (
            [FreshnessStatus.FRESH, FreshnessStatus.STALE],
            InfluencerFreshnessSummary(FreshnessStatus.STALE, True),
        ),
        (
            [FreshnessStatus.VERY_STALE, FreshnessStatus.STALE],
            InfluencerFreshnessSummary(FreshnessStatus.VERY_STALE, True),
        ),
        (
            [FreshnessStatus.UNKNOWN, FreshnessStatus.VERY_STALE],
            InfluencerFreshnessSummary(FreshnessStatus.UNKNOWN, True),
        ),
    ],
)
def test_aggregate_uses_worst_eligible_account_status(
    policy: FreshnessPolicy,
    statuses: list[FreshnessStatus],
    expected: InfluencerFreshnessSummary,
) -> None:
    assert policy.aggregate(statuses) == expected


def test_aggregate_accepts_evaluations(policy: FreshnessPolicy) -> None:
    evaluations = [
        policy.evaluate(AS_OF - timedelta(days=1), as_of=AS_OF),
        policy.evaluate(AS_OF - timedelta(days=31), as_of=AS_OF),
    ]

    assert policy.aggregate(evaluations) == InfluencerFreshnessSummary(
        status=FreshnessStatus.STALE,
        requires_refresh=True,
    )


def test_aggregate_without_eligible_accounts_is_unknown_but_not_refreshable(
    policy: FreshnessPolicy,
) -> None:
    assert policy.aggregate([]) == InfluencerFreshnessSummary(
        status=FreshnessStatus.UNKNOWN,
        requires_refresh=False,
    )


def test_content_activity_freshness_is_separate_and_rejects_future_evidence() -> None:
    policy = ContentActivityFreshnessPolicy.from_day_threshold(7)

    assert policy.is_current(AS_OF - timedelta(days=7), AS_OF)
    assert not policy.is_current(AS_OF - timedelta(days=7, microseconds=1), AS_OF)
    assert not policy.is_current(AS_OF + timedelta(seconds=1), AS_OF)
    assert not policy.is_current(None, AS_OF)


@pytest.mark.parametrize("days", (-1, 1.5, "7"))
def test_content_activity_freshness_rejects_invalid_day_threshold(days: object) -> None:
    with pytest.raises(ValueError, match="nonnegative integer"):
        ContentActivityFreshnessPolicy.from_day_threshold(days)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("fresh_days", "aging_days", "stale_days"),
    [
        (-1, 30, 90),
        (7, 7, 90),
        (8, 7, 90),
        (7, 30, 30),
        (7, 91, 90),
    ],
)
def test_policy_rejects_invalid_day_thresholds(
    fresh_days: int,
    aging_days: int,
    stale_days: int,
) -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        FreshnessPolicy.from_day_thresholds(
            fresh_days=fresh_days,
            aging_days=aging_days,
            stale_days=stale_days,
        )

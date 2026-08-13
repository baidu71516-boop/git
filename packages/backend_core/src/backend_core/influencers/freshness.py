"""Pure UTC-elapsed freshness policy for influencer source observations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class FreshnessStatus(StrEnum):
    """Freshness bands ordered separately from their serialized values."""

    UNKNOWN = "unknown"
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    VERY_STALE = "very_stale"


_REQUIRES_REFRESH = frozenset(
    {
        FreshnessStatus.UNKNOWN,
        FreshnessStatus.STALE,
        FreshnessStatus.VERY_STALE,
    }
)
_WORST_FIRST = {
    FreshnessStatus.UNKNOWN: 0,
    FreshnessStatus.VERY_STALE: 1,
    FreshnessStatus.STALE: 2,
    FreshnessStatus.AGING: 3,
    FreshnessStatus.FRESH: 4,
}


@dataclass(frozen=True, slots=True)
class FreshnessEvaluation:
    """Freshness result for one eligible platform-account/source pair."""

    status: FreshnessStatus
    age_days: int | None
    requires_refresh: bool


@dataclass(frozen=True, slots=True)
class InfluencerFreshnessSummary:
    """Worst eligible account status and influencer-level refresh decision."""

    status: FreshnessStatus
    requires_refresh: bool


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    """Validated duration thresholds for UTC elapsed-time freshness."""

    fresh_duration: timedelta = timedelta(days=7)
    aging_duration: timedelta = timedelta(days=30)
    stale_duration: timedelta = timedelta(days=90)

    def __post_init__(self) -> None:
        if not (timedelta(0) <= self.fresh_duration < self.aging_duration < self.stale_duration):
            raise ValueError("freshness thresholds must be nonnegative and strictly increasing")

    @classmethod
    def from_day_thresholds(
        cls,
        fresh_days: int = 7,
        aging_days: int = 30,
        stale_days: int = 90,
    ) -> FreshnessPolicy:
        """Build a policy from validated whole-day Settings values."""

        if any(type(value) is not int for value in (fresh_days, aging_days, stale_days)):
            raise TypeError("freshness day thresholds must be integers")
        return cls(
            fresh_duration=timedelta(days=fresh_days),
            aging_duration=timedelta(days=aging_days),
            stale_duration=timedelta(days=stale_days),
        )

    def evaluate(
        self,
        observed_at: datetime | None,
        as_of: datetime | None = None,
    ) -> FreshnessEvaluation:
        """Evaluate one reliable observation against one server UTC instant."""

        evaluation_time = _as_utc(as_of or datetime.now(UTC), name="as_of")
        if observed_at is None:
            return FreshnessEvaluation(
                status=FreshnessStatus.UNKNOWN,
                age_days=None,
                requires_refresh=True,
            )

        observed_time = _as_utc(observed_at, name="observed_at")
        elapsed = max(evaluation_time - observed_time, timedelta(0))
        if elapsed <= self.fresh_duration:
            status = FreshnessStatus.FRESH
        elif elapsed <= self.aging_duration:
            status = FreshnessStatus.AGING
        elif elapsed <= self.stale_duration:
            status = FreshnessStatus.STALE
        else:
            status = FreshnessStatus.VERY_STALE
        return FreshnessEvaluation(
            status=status,
            age_days=elapsed.days,
            requires_refresh=status in _REQUIRES_REFRESH,
        )

    def aggregate(
        self,
        evaluations: Iterable[FreshnessEvaluation | FreshnessStatus],
    ) -> InfluencerFreshnessSummary:
        """Aggregate eligible accounts without letting a fresher account hide a worse one."""

        statuses: list[FreshnessStatus] = []
        requires_refresh = False
        for evaluation in evaluations:
            if isinstance(evaluation, FreshnessEvaluation):
                status = evaluation.status
                requires_refresh = requires_refresh or evaluation.requires_refresh
            else:
                status = FreshnessStatus(evaluation)
                requires_refresh = requires_refresh or status in _REQUIRES_REFRESH
            statuses.append(status)

        if not statuses:
            return InfluencerFreshnessSummary(
                status=FreshnessStatus.UNKNOWN,
                requires_refresh=False,
            )
        return InfluencerFreshnessSummary(
            status=min(statuses, key=_WORST_FIRST.__getitem__),
            requires_refresh=requires_refresh,
        )


def _as_utc(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


__all__ = [
    "FreshnessEvaluation",
    "FreshnessPolicy",
    "FreshnessStatus",
    "InfluencerFreshnessSummary",
]

"""Deterministic refresh priority derived from the shared freshness policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend_core.influencers.freshness import FreshnessPolicy, FreshnessStatus
from backend_core.refresh.enums import RefreshPriorityReason

POLICY_VERSION = 1

_PRIMARY_PRIORITY: dict[FreshnessStatus, tuple[int, RefreshPriorityReason]] = {
    FreshnessStatus.UNKNOWN: (1, RefreshPriorityReason.FRESHNESS_UNKNOWN),
    FreshnessStatus.VERY_STALE: (2, RefreshPriorityReason.VERY_STALE),
    FreshnessStatus.STALE: (3, RefreshPriorityReason.STALE),
    FreshnessStatus.AGING: (4, RefreshPriorityReason.AGING),
}


@dataclass(frozen=True, slots=True)
class RefreshPriorityDecision:
    """Frozen priority for one eligible platform-account/source candidate."""

    tier: int
    reasons: tuple[RefreshPriorityReason, ...]
    freshness_status: FreshnessStatus
    followers_count: int | None


def evaluate_refresh_priority(
    *,
    policy: FreshnessPolicy,
    observed_at: datetime | None,
    as_of: datetime,
    followers_count: int | None,
) -> RefreshPriorityDecision | None:
    """Return a queue priority, or ``None`` when a fresh candidate needs no refresh.

    ``FreshnessPolicy`` remains the sole threshold implementation. This function only
    maps its result onto the five frozen queue tiers and appends the stable missing-
    followers reason after any freshness reason.
    """

    _validate_followers_count(followers_count)
    freshness = policy.evaluate(observed_at, as_of=as_of)

    primary = _PRIMARY_PRIORITY.get(freshness.status)
    if primary is None:
        if followers_count is not None:
            return None
        return RefreshPriorityDecision(
            tier=5,
            reasons=(RefreshPriorityReason.FOLLOWERS_MISSING,),
            freshness_status=freshness.status,
            followers_count=None,
        )

    tier, primary_reason = primary
    reasons: tuple[RefreshPriorityReason, ...] = (primary_reason,)
    if followers_count is None:
        reasons += (RefreshPriorityReason.FOLLOWERS_MISSING,)
    return RefreshPriorityDecision(
        tier=tier,
        reasons=reasons,
        freshness_status=freshness.status,
        followers_count=followers_count,
    )


def _validate_followers_count(followers_count: int | None) -> None:
    if followers_count is None:
        return
    if type(followers_count) is not int:
        raise TypeError("followers_count must be an integer or None")
    if followers_count < 0:
        raise ValueError("followers_count must be nonnegative")


__all__ = [
    "POLICY_VERSION",
    "RefreshPriorityDecision",
    "evaluate_refresh_priority",
]

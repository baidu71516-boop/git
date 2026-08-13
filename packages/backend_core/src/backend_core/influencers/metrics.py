"""Shared validation helpers for current influencer metrics."""


def followers_count_from_metrics(metrics: object) -> int | None:
    """Return a real nonnegative integer follower count, never a coerced value."""

    raw_value = metrics.get("followers_count") if isinstance(metrics, dict) else None
    if type(raw_value) is int and raw_value >= 0:
        return raw_value
    return None


__all__ = ["followers_count_from_metrics"]

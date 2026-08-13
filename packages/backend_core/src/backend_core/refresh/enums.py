"""Refresh queue persistence enums and stable priority reason codes."""

from enum import StrEnum


class RefreshQueueStatus(StrEnum):
    OPEN = "open"
    EXPORTED = "exported"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class RefreshQueueItemStatus(StrEnum):
    PENDING = "pending"
    FULFILLED_CHANGED = "fulfilled_changed"
    FULFILLED_NO_CHANGE = "fulfilled_no_change"
    STALE_RETURN = "stale_return"
    UNRESOLVED = "unresolved"
    CANCELLED = "cancelled"


class RefreshPriorityReason(StrEnum):
    FRESHNESS_UNKNOWN = "FRESHNESS_UNKNOWN"
    VERY_STALE = "VERY_STALE"
    STALE = "STALE"
    AGING = "AGING"
    FOLLOWERS_MISSING = "FOLLOWERS_MISSING"

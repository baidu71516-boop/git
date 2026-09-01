"""Closed, platform-neutral Content Activity state enums."""

from enum import StrEnum


class ProviderAccountIdentityNamespace(StrEnum):
    """Canonical external-identity namespaces accepted by this migration."""

    XIAOHONGSHU_USERID = "xiaohongshu.userid"
    DOUYIN_HUITUN_UID = "douyin.huitun_uid"


class ProviderAccountIdentityVerificationState(StrEnum):
    """Lifecycle of one canonical identity-binding episode."""

    VERIFIED_CURRENT = "VERIFIED_CURRENT"
    SUPERSEDED = "SUPERSEDED"
    REVOKED = "REVOKED"


class ProviderAccountIdentityVerificationOutcome(StrEnum):
    """Closed result of an immutable verification event."""

    POLICY_VERIFIED = "POLICY_VERIFIED"


class ContentActivityProvider(StrEnum):
    """Registry keys for Content Activity source providers."""

    TIKHUB = "TIKHUB"
    HUITUN_DOUYIN_AWEME_LIST = "HUITUN_DOUYIN_AWEME_LIST"


class ContentActivitySemantics(StrEnum):
    """Business semantic represented by an observation."""

    CURRENT_PUBLIC_VISIBLE = "CURRENT_PUBLIC_VISIBLE"
    HUITUN_RETURNED_SCOPE = "HUITUN_RETURNED_SCOPE"


class ContentActivityObservationStatus(StrEnum):
    """Outcome of one refresh attempt, orthogonal to coverage and result."""

    COMPLETE = "COMPLETE"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
    ACCESS_RESTRICTED = "ACCESS_RESTRICTED"
    PROVIDER_AUTH_ERROR = "PROVIDER_AUTH_ERROR"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    RESULT_INCOMPLETE = "RESULT_INCOMPLETE"
    RESULT_UNTRUSTED = "RESULT_UNTRUSTED"
    UNKNOWN = "UNKNOWN"


class ContentActivityCoverageStatus(StrEnum):
    """Completeness evidence available for a normalized attempt."""

    FULL_CURRENT_PUBLIC_SET = "FULL_CURRENT_PUBLIC_SET"
    LATEST_BOUND_PROVEN = "LATEST_BOUND_PROVEN"
    LOOKBACK_BOUNDED = "LOOKBACK_BOUNDED"
    INCOMPLETE = "INCOMPLETE"
    UNKNOWN = "UNKNOWN"


class ContentActivityResult(StrEnum):
    """Business result established by an attempt, if any."""

    PUBLICATION_FOUND = "PUBLICATION_FOUND"
    NO_PUBLIC_CONTENT = "NO_PUBLIC_CONTENT"
    AT_LEAST_LOOKBACK_INACTIVE = "AT_LEAST_LOOKBACK_INACTIVE"
    UNDETERMINED = "UNDETERMINED"


class ContentActivityPublicationType(StrEnum):
    """Normalized publication type; adapter policy decides which are trusted."""

    VIDEO = "VIDEO"
    IMAGE_TEXT = "IMAGE_TEXT"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class ContentActivityProviderErrorClass(StrEnum):
    """Sanitized provider-failure classes only; raw errors never persist."""

    INPUT_UNAVAILABLE = "INPUT_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    TRANSPORT = "TRANSPORT"
    AUTHENTICATION = "AUTHENTICATION"
    RATE_LIMITED = "RATE_LIMITED"
    SEMANTIC_FAILURE = "SEMANTIC_FAILURE"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
    POLICY_REJECTED = "POLICY_REJECTED"
    UNKNOWN = "UNKNOWN"


class ContentActivityScanTerminalReason(StrEnum):
    """Closed, non-secret terminal reasons for normalized scans."""

    SINGLE_RESPONSE_COMPLETE = "SINGLE_RESPONSE_COMPLETE"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
    HAS_MORE_TRUE = "HAS_MORE_TRUE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    RESPONSE_UNTRUSTED = "RESPONSE_UNTRUSTED"
    POLICY_REJECTED = "POLICY_REJECTED"
    REQUEST_NOT_PERMITTED = "REQUEST_NOT_PERMITTED"
    UNKNOWN = "UNKNOWN"


class ContentActivityRefreshRequestState(StrEnum):
    """Durable per-account refresh-request lifecycle."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

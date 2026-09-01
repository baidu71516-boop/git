"""Normalized, fail-closed Huitun Douyin runtime-capture semantics.

This module deliberately accepts only the extension's small semantic contract.
It has no HTTP client, no browser credential handling, and no decryption code.
Raw Huitun envelopes are represented only as a sanitized failure outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from backend_core.content_activity.enums import (
    ContentActivityCoverageStatus,
    ContentActivityObservationStatus,
    ContentActivityProviderErrorClass,
    ContentActivityPublicationType,
    ContentActivityResult,
    ContentActivityScanTerminalReason,
)
from backend_core.content_activity.schemas import DouyinRuntimeCaptureIngestInput

HUITUN_DOUYIN_IDENTITY_SOURCE = "HUITUN_DOUYIN_AWEME_LIST_RUNTIME_CAPTURE"
HUITUN_DOUYIN_IDENTITY_CONTRACT_VERSION = "HUITUN_DOUYIN_AWEME_LIST_RUNTIME_SEMANTIC_V1"
HUITUN_DOUYIN_PROVENANCE_REF = "huitun_douyin_aweme_list_runtime_capture_v1"
HUITUN_DOUYIN_PROVIDER_PRODUCT = "HUITUN_DOUYIN_AWEME_LIST"
HUITUN_DOUYIN_ENDPOINT = "/user/awemeList"
HUITUN_DOUYIN_ENDPOINT_VERSION = "RUNTIME_SEMANTIC_V1"
HUITUN_DOUYIN_ADAPTER_VERSION = "HUITUN_DOUYIN_RUNTIME_CAPTURE_ADAPTER_V1"
HUITUN_DOUYIN_CAPABILITY_POLICY_VERSION = "HUITUN_DOUYIN_RUNTIME_CAPTURE_POLICY_V1"
HUITUN_DOUYIN_RESPONSE_SCHEMA_VERSION = "HUITUN_DOUYIN_AWEME_LIST_SEMANTIC_SCHEMA_V1"
HUITUN_DOUYIN_VISIBILITY_POLICY_VERSION = "HUITUN_DOUYIN_RETURNED_SCOPE_POLICY_V1"
HUITUN_DOUYIN_TIMESTAMP_ENCODING = "HUITUN_PUBLISH_TIME_ASIA_SHANGHAI"
HUITUN_DOUYIN_SOURCE_TIMEZONE = "Asia/Shanghai"
HUITUN_DOUYIN_PUBLICATION_KEY_NAMESPACE = "huitun.douyin.aweme_list.timestamp.v1"

# A response whose requested time window stops materially before the server
# observed the capture cannot prove current inactivity, even if it has data.
MAX_SCOPE_END_SKEW = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class HuitunDouyinRuntimeAttempt:
    """One safe normalized outcome ready for immutable persistence."""

    actual_uid: str | None
    observation_status: ContentActivityObservationStatus
    coverage_status: ContentActivityCoverageStatus
    activity_result: ContentActivityResult
    last_publication_at: datetime | None
    latest_publication_id: str | None
    latest_publication_type: ContentActivityPublicationType | None
    co_latest_publication_count: int | None
    coverage_start_at: datetime | None
    coverage_end_at: datetime | None
    terminal_reason: ContentActivityScanTerminalReason
    scanned_item_count: int
    provider_error_class: ContentActivityProviderErrorClass | None
    provider_error_code: str | None

    @property
    def is_accepted_semantic_result(self) -> bool:
        return (
            self.actual_uid is not None
            and self.observation_status is ContentActivityObservationStatus.COMPLETE
            and self.activity_result
            in {
                ContentActivityResult.PUBLICATION_FOUND,
                ContentActivityResult.AT_LEAST_LOOKBACK_INACTIVE,
            }
        )


def normalize_douyin_runtime_capture(
    payload: DouyinRuntimeCaptureIngestInput,
    *,
    observed_at: datetime,
) -> HuitunDouyinRuntimeAttempt:
    """Map one closed semantic bridge payload to a fail-closed attempt.

    ``observed_at`` is supplied by the server.  No client timestamp can extend
    an empty-range lower bound or turn a future publication into trusted data.
    """

    observation_time = _utc(observed_at)
    if payload.outcome == "PARTIAL":
        return _incomplete_attempt(payload.actual_uid)
    if payload.outcome == "RAW_ENCRYPTED":
        return _untrusted_attempt(
            payload.actual_uid,
            error_class=ContentActivityProviderErrorClass.SEMANTIC_FAILURE,
            error_code="ENCRYPTED_ENVELOPE",
        )
    if payload.outcome == "AUTH_FAILURE":
        return HuitunDouyinRuntimeAttempt(
            actual_uid=payload.actual_uid,
            observation_status=ContentActivityObservationStatus.PROVIDER_AUTH_ERROR,
            coverage_status=ContentActivityCoverageStatus.UNKNOWN,
            activity_result=ContentActivityResult.UNDETERMINED,
            last_publication_at=None,
            latest_publication_id=None,
            latest_publication_type=None,
            co_latest_publication_count=None,
            coverage_start_at=None,
            coverage_end_at=None,
            terminal_reason=ContentActivityScanTerminalReason.PROVIDER_FAILURE,
            scanned_item_count=0,
            provider_error_class=ContentActivityProviderErrorClass.AUTHENTICATION,
            provider_error_code="HUITUN_AUTH_FAILURE",
        )
    if payload.outcome == "RUNTIME_ERROR":
        return _untrusted_attempt(
            payload.actual_uid,
            error_class=ContentActivityProviderErrorClass.SEMANTIC_FAILURE,
            error_code="RUNTIME_EXTRACTION_FAILED",
        )
    if payload.outcome == "INVALID_PAYLOAD":
        return _untrusted_attempt(
            payload.actual_uid,
            error_class=ContentActivityProviderErrorClass.MALFORMED_RESPONSE,
            error_code=payload.rejection_reason or "SCHEMA_REJECTED",
        )

    if payload.actual_uid is None or payload.semantic_uid != payload.actual_uid:
        return _untrusted_attempt(
            payload.actual_uid,
            error_class=ContentActivityProviderErrorClass.IDENTITY_CONFLICT,
            error_code="UID_MISMATCH",
        )
    coverage_end_at = _utc_or_none(payload.coverage_end_at)
    if coverage_end_at is None or not _scope_reaches_observation(
        coverage_end_at, observation_time
    ):
        return _incomplete_attempt(payload.actual_uid, error_code="SCOPE_END_NOT_CURRENT")

    if payload.outcome == "SUCCESS":
        publications = tuple(
            publication.published_at.astimezone(UTC) for publication in payload.publications
        )
        if not publications:
            return _untrusted_attempt(
                payload.actual_uid,
                error_class=ContentActivityProviderErrorClass.MALFORMED_RESPONSE,
                error_code="SUCCESS_WITHOUT_PUBLICATIONS",
            )
        if any(published_at > observation_time for published_at in publications):
            return _untrusted_attempt(
                payload.actual_uid,
                error_class=ContentActivityProviderErrorClass.MALFORMED_RESPONSE,
                error_code="PUBLISH_TIME_FUTURE",
            )
        latest = max(publications)
        tied_count = sum(published_at == latest for published_at in publications)
        representative = sha256(
            f"{payload.actual_uid}|{latest.isoformat()}".encode()
        ).hexdigest()
        return HuitunDouyinRuntimeAttempt(
            actual_uid=payload.actual_uid,
            observation_status=ContentActivityObservationStatus.COMPLETE,
            coverage_status=ContentActivityCoverageStatus.LATEST_BOUND_PROVEN,
            activity_result=ContentActivityResult.PUBLICATION_FOUND,
            last_publication_at=latest,
            latest_publication_id=representative,
            latest_publication_type=ContentActivityPublicationType.OTHER,
            co_latest_publication_count=tied_count,
            coverage_start_at=_utc_or_none(payload.coverage_start_at),
            coverage_end_at=coverage_end_at,
            terminal_reason=ContentActivityScanTerminalReason.SINGLE_RESPONSE_COMPLETE,
            scanned_item_count=len(publications),
            provider_error_class=None,
            provider_error_code=None,
        )

    # Pydantic has already constrained the remaining semantic outcome to
    # EMPTY.  It proves absence only inside the explicit, current-ending range;
    # the result is a lower bound, never an invented last-publication timestamp.
    coverage_start_at = _utc_or_none(payload.coverage_start_at)
    if coverage_start_at is None or coverage_start_at >= coverage_end_at:
        return _untrusted_attempt(
            payload.actual_uid,
            error_class=ContentActivityProviderErrorClass.MALFORMED_RESPONSE,
            error_code="EMPTY_RANGE_INVALID",
        )
    return HuitunDouyinRuntimeAttempt(
        actual_uid=payload.actual_uid,
        observation_status=ContentActivityObservationStatus.COMPLETE,
        coverage_status=ContentActivityCoverageStatus.LOOKBACK_BOUNDED,
        activity_result=ContentActivityResult.AT_LEAST_LOOKBACK_INACTIVE,
        last_publication_at=None,
        latest_publication_id=None,
        latest_publication_type=None,
        co_latest_publication_count=None,
        coverage_start_at=coverage_start_at,
        coverage_end_at=coverage_end_at,
        terminal_reason=ContentActivityScanTerminalReason.SINGLE_RESPONSE_COMPLETE,
        scanned_item_count=0,
        provider_error_class=None,
        provider_error_code=None,
    )


def _incomplete_attempt(
    actual_uid: str | None,
    *,
    error_code: str = "PAGINATION_UNKNOWN",
) -> HuitunDouyinRuntimeAttempt:
    return HuitunDouyinRuntimeAttempt(
        actual_uid=actual_uid,
        observation_status=ContentActivityObservationStatus.RESULT_INCOMPLETE,
        coverage_status=ContentActivityCoverageStatus.INCOMPLETE,
        activity_result=ContentActivityResult.UNDETERMINED,
        last_publication_at=None,
        latest_publication_id=None,
        latest_publication_type=None,
        co_latest_publication_count=None,
        coverage_start_at=None,
        coverage_end_at=None,
        terminal_reason=ContentActivityScanTerminalReason.HAS_MORE_TRUE,
        scanned_item_count=0,
        provider_error_class=None,
        provider_error_code=error_code,
    )


def _untrusted_attempt(
    actual_uid: str | None,
    *,
    error_class: ContentActivityProviderErrorClass,
    error_code: str,
) -> HuitunDouyinRuntimeAttempt:
    return HuitunDouyinRuntimeAttempt(
        actual_uid=actual_uid,
        observation_status=ContentActivityObservationStatus.RESULT_UNTRUSTED,
        coverage_status=ContentActivityCoverageStatus.UNKNOWN,
        activity_result=ContentActivityResult.UNDETERMINED,
        last_publication_at=None,
        latest_publication_id=None,
        latest_publication_type=None,
        co_latest_publication_count=None,
        coverage_start_at=None,
        coverage_end_at=None,
        terminal_reason=ContentActivityScanTerminalReason.RESPONSE_UNTRUSTED,
        scanned_item_count=0,
        provider_error_class=error_class,
        provider_error_code=error_code,
    )


def _scope_reaches_observation(coverage_end_at: datetime, observed_at: datetime) -> bool:
    return coverage_end_at <= observed_at and observed_at - coverage_end_at <= MAX_SCOPE_END_SKEW


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("runtime capture timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _utc_or_none(value: datetime | None) -> datetime | None:
    return _utc(value) if value is not None else None


__all__ = [
    "HUITUN_DOUYIN_ADAPTER_VERSION",
    "HUITUN_DOUYIN_CAPABILITY_POLICY_VERSION",
    "HUITUN_DOUYIN_ENDPOINT",
    "HUITUN_DOUYIN_ENDPOINT_VERSION",
    "HUITUN_DOUYIN_IDENTITY_CONTRACT_VERSION",
    "HUITUN_DOUYIN_IDENTITY_SOURCE",
    "HUITUN_DOUYIN_PROVENANCE_REF",
    "HUITUN_DOUYIN_PROVIDER_PRODUCT",
    "HUITUN_DOUYIN_PUBLICATION_KEY_NAMESPACE",
    "HUITUN_DOUYIN_RESPONSE_SCHEMA_VERSION",
    "HUITUN_DOUYIN_SOURCE_TIMEZONE",
    "HUITUN_DOUYIN_TIMESTAMP_ENCODING",
    "HUITUN_DOUYIN_VISIBILITY_POLICY_VERSION",
    "HuitunDouyinRuntimeAttempt",
    "normalize_douyin_runtime_capture",
]

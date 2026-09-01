"""Fail-closed pure contracts for the Huitun Douyin semantic bridge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from backend_core.content_activity.enums import (
    ContentActivityCoverageStatus,
    ContentActivityObservationStatus,
    ContentActivityProviderErrorClass,
    ContentActivityResult,
)
from backend_core.content_activity.huitun_douyin import normalize_douyin_runtime_capture
from backend_core.content_activity.schemas import DouyinRuntimeCaptureIngestInput
from pydantic import ValidationError

OBSERVED_AT = datetime(2026, 8, 23, 12, tzinfo=UTC)


def _payload(**changes: object) -> DouyinRuntimeCaptureIngestInput:
    values: dict[str, object] = {
        "capture_request_id": uuid4(),
        "runtime_request_id": "runtime-capture-1",
        "outcome": "SUCCESS",
        "actual_uid": "huitun-uid-42",
        "semantic_uid": "huitun-uid-42",
        "publications": [
            {"published_at": "2026-08-03T14:54:37Z"},
            {"published_at": "2026-08-13T09:57:52Z"},
            {"published_at": "2026-08-03T14:56:34Z"},
        ],
        "pagination_terminal": True,
        "coverage_end_at": OBSERVED_AT,
    }
    values.update(changes)
    return DouyinRuntimeCaptureIngestInput.model_validate(values)


def test_success_uses_true_latest_timestamp_not_provider_order() -> None:
    attempt = normalize_douyin_runtime_capture(_payload(), observed_at=OBSERVED_AT)

    assert attempt.observation_status is ContentActivityObservationStatus.COMPLETE
    assert attempt.coverage_status is ContentActivityCoverageStatus.LATEST_BOUND_PROVEN
    assert attempt.activity_result is ContentActivityResult.PUBLICATION_FOUND
    assert attempt.last_publication_at == datetime(2026, 8, 13, 9, 57, 52, tzinfo=UTC)
    assert attempt.scanned_item_count == 3
    assert attempt.provider_error_code is None


def test_proven_first_page_accepts_exact_latest_without_terminal_pagination() -> None:
    attempt = normalize_douyin_runtime_capture(
        _payload(pagination_terminal=False, latest_page_proven=True),
        observed_at=OBSERVED_AT,
    )

    assert attempt.observation_status is ContentActivityObservationStatus.COMPLETE
    assert attempt.coverage_status is ContentActivityCoverageStatus.LATEST_BOUND_PROVEN
    assert attempt.activity_result is ContentActivityResult.PUBLICATION_FOUND
    assert attempt.last_publication_at == datetime(2026, 8, 13, 9, 57, 52, tzinfo=UTC)


@pytest.mark.parametrize(
    ("outcome", "status", "error_class", "error_code"),
    [
        (
            "RAW_ENCRYPTED",
            ContentActivityObservationStatus.RESULT_UNTRUSTED,
            ContentActivityProviderErrorClass.SEMANTIC_FAILURE,
            "ENCRYPTED_ENVELOPE",
        ),
        (
            "AUTH_FAILURE",
            ContentActivityObservationStatus.PROVIDER_AUTH_ERROR,
            ContentActivityProviderErrorClass.AUTHENTICATION,
            "HUITUN_AUTH_FAILURE",
        ),
        (
            "RUNTIME_ERROR",
            ContentActivityObservationStatus.RESULT_UNTRUSTED,
            ContentActivityProviderErrorClass.SEMANTIC_FAILURE,
            "RUNTIME_EXTRACTION_FAILED",
        ),
        (
            "INVALID_PAYLOAD",
            ContentActivityObservationStatus.RESULT_UNTRUSTED,
            ContentActivityProviderErrorClass.MALFORMED_RESPONSE,
            "SCHEMA_REJECTED",
        ),
    ],
)
def test_nonsemantic_runtime_outcomes_are_unknown_safe(
    outcome: str,
    status: ContentActivityObservationStatus,
    error_class: ContentActivityProviderErrorClass,
    error_code: str,
) -> None:
    attempt = normalize_douyin_runtime_capture(
        _payload(
            outcome=outcome,
            actual_uid=None,
            semantic_uid=None,
            publications=(),
            pagination_terminal=None,
            coverage_end_at=None,
            rejection_reason="SCHEMA_REJECTED" if outcome == "INVALID_PAYLOAD" else None,
        ),
        observed_at=OBSERVED_AT,
    )

    assert attempt.observation_status is status
    assert attempt.coverage_status is ContentActivityCoverageStatus.UNKNOWN
    assert attempt.activity_result is ContentActivityResult.UNDETERMINED
    assert attempt.last_publication_at is None
    assert attempt.provider_error_class is error_class
    assert attempt.provider_error_code == error_code


def test_uid_mismatch_and_future_publish_time_never_form_activity_evidence() -> None:
    mismatch = normalize_douyin_runtime_capture(
        _payload(semantic_uid="different-uid"),
        observed_at=OBSERVED_AT,
    )
    future = normalize_douyin_runtime_capture(
        _payload(publications=[{"published_at": OBSERVED_AT + timedelta(seconds=1)}]),
        observed_at=OBSERVED_AT,
    )

    assert mismatch.activity_result is ContentActivityResult.UNDETERMINED
    assert mismatch.provider_error_code == "UID_MISMATCH"
    assert future.activity_result is ContentActivityResult.UNDETERMINED
    assert future.provider_error_code == "PUBLISH_TIME_FUTURE"


def test_invalid_payload_persists_only_the_closed_rejection_reason() -> None:
    attempt = normalize_douyin_runtime_capture(
        _payload(
            outcome="INVALID_PAYLOAD",
            actual_uid="huitun-uid-42",
            semantic_uid=None,
            publications=(),
            pagination_terminal=None,
            coverage_end_at=None,
            rejection_reason="REQUEST_BINDING_MISMATCH",
        ),
        observed_at=OBSERVED_AT,
    )

    assert attempt.activity_result is ContentActivityResult.UNDETERMINED
    assert attempt.provider_error_code == "REQUEST_BINDING_MISMATCH"


def test_invalid_payload_rejects_missing_or_non_allowlisted_reason() -> None:
    with pytest.raises(ValidationError, match="sanitized rejection_reason"):
        _payload(
            outcome="INVALID_PAYLOAD",
            actual_uid=None,
            semantic_uid=None,
            publications=(),
            pagination_terminal=None,
            coverage_end_at=None,
        )
    with pytest.raises(ValidationError):
        _payload(
            outcome="INVALID_PAYLOAD",
            actual_uid=None,
            semantic_uid=None,
            publications=(),
            pagination_terminal=None,
            coverage_end_at=None,
            rejection_reason="raw-response",
        )


def test_terminal_empty_range_is_only_a_lower_bound() -> None:
    attempt = normalize_douyin_runtime_capture(
        _payload(
            outcome="EMPTY",
            publications=(),
            coverage_start_at=OBSERVED_AT - timedelta(days=60),
            coverage_end_at=OBSERVED_AT,
        ),
        observed_at=OBSERVED_AT,
    )

    assert attempt.observation_status is ContentActivityObservationStatus.COMPLETE
    assert attempt.coverage_status is ContentActivityCoverageStatus.LOOKBACK_BOUNDED
    assert attempt.activity_result is ContentActivityResult.AT_LEAST_LOOKBACK_INACTIVE
    assert attempt.last_publication_at is None
    assert attempt.coverage_start_at == OBSERVED_AT - timedelta(days=60)
    assert attempt.coverage_end_at == OBSERVED_AT


def test_partial_or_pagination_unknown_cannot_create_a_lower_bound() -> None:
    attempt = normalize_douyin_runtime_capture(
        _payload(
            outcome="PARTIAL",
            publications=(),
            pagination_terminal=False,
            coverage_end_at=None,
        ),
        observed_at=OBSERVED_AT,
    )

    assert attempt.observation_status is ContentActivityObservationStatus.RESULT_INCOMPLETE
    assert attempt.coverage_status is ContentActivityCoverageStatus.INCOMPLETE
    assert attempt.activity_result is ContentActivityResult.UNDETERMINED
    assert attempt.coverage_start_at is None
    assert attempt.coverage_end_at is None


def test_schema_rejects_success_or_empty_without_the_terminal_proof() -> None:
    with pytest.raises(ValidationError, match="terminal pagination or proven newest first page"):
        _payload(pagination_terminal=False)
    with pytest.raises(ValidationError, match="terminal pagination"):
        _payload(
            outcome="EMPTY",
            publications=(),
            pagination_terminal=False,
            coverage_start_at=OBSERVED_AT - timedelta(days=60),
            coverage_end_at=OBSERVED_AT,
        )
    with pytest.raises(ValidationError, match="explicit coverage range"):
        _payload(
            outcome="EMPTY",
            publications=(),
            coverage_start_at=None,
            coverage_end_at=OBSERVED_AT,
        )

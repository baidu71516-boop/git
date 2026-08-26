from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend_core.content_activity.enums import (
    ContentActivityCoverageStatus,
    ContentActivityObservationStatus,
    ContentActivityProvider,
    ContentActivityProviderErrorClass,
    ContentActivityPublicationType,
    ContentActivityResult,
    ContentActivityScanTerminalReason,
    ContentActivitySemantics,
)
from backend_core.content_activity.models import ContentActivityObservation
from backend_core.content_activity.projection import apply_observation, invalidate_trusted_current
from backend_core.influencers.enums import Platform


def _observation(
    *,
    observed_at: datetime,
    status: ContentActivityObservationStatus = ContentActivityObservationStatus.COMPLETE,
    coverage: ContentActivityCoverageStatus = ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET,
    result: ContentActivityResult = ContentActivityResult.PUBLICATION_FOUND,
    publication_at: datetime | None = None,
    error: ContentActivityProviderErrorClass | None = None,
) -> ContentActivityObservation:
    publication_at = publication_at or observed_at - timedelta(days=10)
    has_publication = result is ContentActivityResult.PUBLICATION_FOUND
    has_accepted_identity = (
        status is ContentActivityObservationStatus.COMPLETE
        and coverage is ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET
        and result
        in {
            ContentActivityResult.PUBLICATION_FOUND,
            ContentActivityResult.NO_PUBLIC_CONTENT,
        }
    )
    return ContentActivityObservation(
        id=uuid4(),
        platform_account_id=uuid4(),
        platform=Platform.XIAOHONGSHU,
        activity_semantics=ContentActivitySemantics.CURRENT_PUBLIC_VISIBLE,
        provider_account_identity_id=uuid4() if has_accepted_identity else None,
        provider_account_identity_verification_id=uuid4() if has_accepted_identity else None,
        activity_source_provider=ContentActivityProvider.TIKHUB,
        provider_product="XIAOHONGSHU_APP_V2",
        endpoint="/api/v1/xiaohongshu/app_v2/get_user_posted_notes",
        endpoint_version="v2",
        adapter_version="xhs-v1",
        capability_policy_version="TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1",
        response_schema_version="TIKHUB_XHS_APP_V2_GET_USER_POSTED_NOTES_RESPONSE_SCHEMA_V1",
        visibility_policy_version="TIKHUB_XHS_APP_V2_CURRENT_PUBLIC_VISIBILITY_POLICY_V1",
        attempt_started_at=observed_at - timedelta(seconds=1),
        observed_at=observed_at,
        observation_status=status,
        coverage_status=coverage,
        activity_result=result,
        last_publication_at=publication_at if has_publication else None,
        latest_publication_id_namespace="xiaohongshu.noteid" if has_publication else None,
        latest_publication_id="note-1" if has_publication else None,
        latest_publication_type=ContentActivityPublicationType.VIDEO if has_publication else None,
        co_latest_publication_count=1 if has_publication else None,
        coverage_start_at=None,
        coverage_end_at=None,
        timestamp_encoding="unix_seconds" if has_publication else None,
        source_timezone=None,
        timezone_basis="unix_seconds" if has_publication else None,
        normalized_timezone="UTC",
        request_ref="synthetic-request",
        provenance_ref="synthetic-fixture",
        scan_terminal_reason=ContentActivityScanTerminalReason.SINGLE_RESPONSE_COMPLETE,
        scanned_page_count=1,
        scanned_item_count=1 if has_publication else 0,
        provider_error_class=error,
        provider_error_code="TIMEOUT" if error is not None else None,
    )


def _same_account(
    observation: ContentActivityObservation, account_id
) -> ContentActivityObservation:
    observation.platform_account_id = account_id
    return observation


def test_provider_failure_preserves_last_trusted_projection() -> None:
    first = _observation(observed_at=datetime(2026, 8, 20, tzinfo=UTC))
    projection = apply_observation(None, first)
    previous_publication = projection.last_publication_at

    failed = _same_account(
        _observation(
            observed_at=datetime(2026, 8, 21, tzinfo=UTC),
            status=ContentActivityObservationStatus.PROVIDER_ERROR,
            coverage=ContentActivityCoverageStatus.UNKNOWN,
            result=ContentActivityResult.UNDETERMINED,
            error=ContentActivityProviderErrorClass.TIMEOUT,
        ),
        first.platform_account_id,
    )
    projection = apply_observation(projection, failed)

    assert projection.latest_attempt_observation_id == failed.id
    assert (
        projection.latest_attempt_provider_error_class is ContentActivityProviderErrorClass.TIMEOUT
    )
    assert projection.trusted_observation_id == first.id
    assert projection.last_publication_at == previous_publication


def test_later_trusted_snapshot_can_move_last_publication_backward() -> None:
    first = _observation(
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        publication_at=datetime(2026, 8, 19, tzinfo=UTC),
    )
    projection = apply_observation(None, first)
    later = _same_account(
        _observation(
            observed_at=datetime(2026, 8, 21, tzinfo=UTC),
            publication_at=datetime(2026, 8, 10, tzinfo=UTC),
        ),
        first.platform_account_id,
    )

    projection = apply_observation(projection, later)

    assert projection.trusted_observation_id == later.id
    assert projection.last_publication_at == datetime(2026, 8, 10, tzinfo=UTC)


def test_trusted_complete_empty_snapshot_clears_publication_evidence() -> None:
    first = _observation(observed_at=datetime(2026, 8, 20, tzinfo=UTC))
    projection = apply_observation(None, first)
    empty = _same_account(
        _observation(
            observed_at=datetime(2026, 8, 21, tzinfo=UTC),
            result=ContentActivityResult.NO_PUBLIC_CONTENT,
        ),
        first.platform_account_id,
    )

    projection = apply_observation(projection, empty)

    assert projection.trusted_observation_id == empty.id
    assert projection.trusted_activity_result is ContentActivityResult.NO_PUBLIC_CONTENT
    assert projection.last_publication_at is None
    assert projection.latest_publication_id is None


def test_first_page_incomplete_cannot_replace_trusted_state() -> None:
    first = _observation(observed_at=datetime(2026, 8, 20, tzinfo=UTC))
    projection = apply_observation(None, first)
    incomplete = _same_account(
        _observation(
            observed_at=datetime(2026, 8, 21, tzinfo=UTC),
            status=ContentActivityObservationStatus.RESULT_INCOMPLETE,
            coverage=ContentActivityCoverageStatus.INCOMPLETE,
            result=ContentActivityResult.UNDETERMINED,
        ),
        first.platform_account_id,
    )

    projection = apply_observation(projection, incomplete)

    assert projection.latest_attempt_observation_id == incomplete.id
    assert projection.trusted_observation_id == first.id


def test_equal_observed_at_never_silently_tie_breaks_a_conflicting_projection() -> None:
    observed_at = datetime(2026, 8, 20, tzinfo=UTC)
    first = _observation(
        observed_at=observed_at,
        publication_at=datetime(2026, 8, 19, tzinfo=UTC),
    )
    projection = apply_observation(None, first)
    conflicting = _same_account(
        _observation(
            observed_at=observed_at,
            publication_at=datetime(2026, 8, 10, tzinfo=UTC),
        ),
        first.platform_account_id,
    )

    projection = apply_observation(projection, conflicting)

    assert projection.latest_attempt_observation_id == first.id
    assert projection.trusted_observation_id == first.id
    assert projection.last_publication_at == datetime(2026, 8, 19, tzinfo=UTC)


def test_equal_observed_at_quarantined_attempt_becomes_latest_fail_closed_state() -> None:
    observed_at = datetime(2026, 8, 20, tzinfo=UTC)
    first = _observation(observed_at=observed_at)
    projection = apply_observation(None, first)
    quarantined = _same_account(
        _observation(
            observed_at=observed_at,
            status=ContentActivityObservationStatus.RESULT_UNTRUSTED,
            coverage=ContentActivityCoverageStatus.UNKNOWN,
            result=ContentActivityResult.UNDETERMINED,
            error=ContentActivityProviderErrorClass.UNKNOWN,
        ),
        first.platform_account_id,
    )
    quarantined.provider_error_code = "EQUAL_OBSERVED_AT_CONFLICT"

    projection = apply_observation(projection, quarantined)

    assert projection.latest_attempt_observation_id == quarantined.id
    assert (
        projection.latest_attempt_observation_status
        is ContentActivityObservationStatus.RESULT_UNTRUSTED
    )
    assert projection.trusted_observation_id == first.id
    assert projection.last_publication_at == first.last_publication_at


def test_identity_supersession_invalidates_only_trusted_current_projection() -> None:
    first = _observation(observed_at=datetime(2026, 8, 20, tzinfo=UTC))
    projection = apply_observation(None, first)
    previous_version = projection.version

    invalidate_trusted_current(projection)

    assert projection.latest_attempt_observation_id == first.id
    assert projection.latest_attempt_observed_at == first.observed_at
    assert projection.trusted_observation_id is None
    assert projection.trusted_observed_at is None
    assert projection.trusted_activity_result is None
    assert projection.last_publication_at is None
    assert projection.latest_publication_id is None
    assert projection.version == previous_version + 1

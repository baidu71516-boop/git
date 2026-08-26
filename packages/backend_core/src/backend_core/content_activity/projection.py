"""Pure, fail-closed projection transitions for immutable activity observations."""

from __future__ import annotations

from backend_core.content_activity.enums import (
    ContentActivityCoverageStatus,
    ContentActivityObservationStatus,
    ContentActivityResult,
)
from backend_core.content_activity.models import (
    ContentActivityObservation,
    ContentActivityProjection,
)


def is_trusted_current_public(observation: ContentActivityObservation) -> bool:
    """Return whether an observation may replace the trusted-current projection.

    The predicate deliberately has no provider-specific fallback.  In
    particular, a first-page partial result, any unknown evidence, a transport
    failure, or a trusted-looking timestamp without full current-public
    coverage cannot alter trusted state.
    """

    return (
        observation.observation_status is ContentActivityObservationStatus.COMPLETE
        and observation.coverage_status is ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET
        # A COMPLETE provider response is not a trusted observation unless it
        # is tied to the currently accepted canonical identity evidence.  The
        # database independently enforces the same relation for persisted
        # records; keeping it here protects direct/pure projection callers.
        and observation.provider_account_identity_id is not None
        and observation.provider_account_identity_verification_id is not None
        and observation.activity_result
        in {
            ContentActivityResult.PUBLICATION_FOUND,
            ContentActivityResult.NO_PUBLIC_CONTENT,
        }
    )


def apply_observation(
    projection: ContentActivityProjection | None,
    observation: ContentActivityObservation,
) -> ContentActivityProjection:
    """Return/update the one-account projection for a newly persisted observation.

    Latest-attempt and trusted-current state are deliberately independent.  A
    non-trusted observation still gives sales users a diagnostic latest check,
    while the previous trusted current-public fact remains intact.  A later
    trusted empty response clears publication evidence; a later trusted response
    with an older publication timestamp replaces—not maxes—the old value.
    """

    is_new_projection = projection is None
    if projection is None:
        projection = ContentActivityProjection(
            platform_account_id=observation.platform_account_id,
            platform=observation.platform,
            latest_attempt_observation_id=observation.id,
            latest_attempt_observed_at=observation.observed_at,
            latest_attempt_observation_status=observation.observation_status,
            latest_attempt_coverage_status=observation.coverage_status,
            latest_attempt_activity_result=observation.activity_result,
            latest_attempt_provider_error_class=observation.provider_error_class,
            latest_attempt_provider_error_code=observation.provider_error_code,
            trusted_observation_id=None,
            trusted_observed_at=None,
            trusted_observation_status=None,
            trusted_coverage_status=None,
            trusted_activity_result=None,
            trusted_capability_policy_version=None,
            last_publication_at=None,
            latest_publication_id_namespace=None,
            latest_publication_id=None,
            latest_publication_type=None,
            co_latest_publication_count=None,
            version=1,
        )
    elif observation.observed_at > projection.latest_attempt_observed_at or (
        observation.observed_at == projection.latest_attempt_observed_at
        and not is_trusted_current_public(observation)
    ):
        # Equal trusted observations are intentionally not tie-broken.  Once
        # the service quarantines an equal-time material conflict as untrusted,
        # however, that explicit latest attempt must replace the old latest
        # pointer so all current-product reads fail closed rather than silently
        # treating the retained trusted fact as present-tense.
        projection.latest_attempt_observation_id = observation.id
        projection.latest_attempt_observed_at = observation.observed_at
        projection.latest_attempt_observation_status = observation.observation_status
        projection.latest_attempt_coverage_status = observation.coverage_status
        projection.latest_attempt_activity_result = observation.activity_result
        projection.latest_attempt_provider_error_class = observation.provider_error_class
        projection.latest_attempt_provider_error_code = observation.provider_error_code
        if not is_new_projection:
            projection.version += 1

    if is_trusted_current_public(observation) and (
        projection.trusted_observed_at is None
        or observation.observed_at > projection.trusted_observed_at
    ):
        projection.trusted_observation_id = observation.id
        projection.trusted_observed_at = observation.observed_at
        projection.trusted_observation_status = observation.observation_status
        projection.trusted_coverage_status = observation.coverage_status
        projection.trusted_activity_result = observation.activity_result
        projection.trusted_capability_policy_version = observation.capability_policy_version
        if observation.activity_result is ContentActivityResult.PUBLICATION_FOUND:
            projection.last_publication_at = observation.last_publication_at
            projection.latest_publication_id_namespace = observation.latest_publication_id_namespace
            projection.latest_publication_id = observation.latest_publication_id
            projection.latest_publication_type = observation.latest_publication_type
            projection.co_latest_publication_count = observation.co_latest_publication_count
        else:
            # A complete trusted empty current-public listing clears the former
            # result.  It is intentionally not converted to a synthetic age.
            projection.last_publication_at = None
            projection.latest_publication_id_namespace = None
            projection.latest_publication_id = None
            projection.latest_publication_type = None
            projection.co_latest_publication_count = None
        if not is_new_projection:
            projection.version += 1

    return projection


def invalidate_trusted_current(projection: ContentActivityProjection) -> None:
    """Remove only trusted-current state after its identity basis is superseded.

    Observation history remains immutable and the latest-attempt diagnostic stays
    available.  This is deliberately distinct from a provider failure: failures
    preserve a still-current trusted identity result, while an explicit identity
    replacement makes every old-identity current-public fact unusable until the
    replacement identity receives a new trusted observation.
    """

    if projection.trusted_observation_id is None:
        return
    projection.trusted_observation_id = None
    projection.trusted_observed_at = None
    projection.trusted_observation_status = None
    projection.trusted_coverage_status = None
    projection.trusted_activity_result = None
    projection.trusted_capability_policy_version = None
    projection.last_publication_at = None
    projection.latest_publication_id_namespace = None
    projection.latest_publication_id = None
    projection.latest_publication_type = None
    projection.co_latest_publication_count = None
    projection.version += 1


__all__ = ["apply_observation", "invalidate_trusted_current", "is_trusted_current_public"]

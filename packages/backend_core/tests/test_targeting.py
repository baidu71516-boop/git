"""Pure WO-3A-2 policy and evaluator contract tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import permutations
from uuid import uuid4

import pytest
from backend_core.content_activity.enums import (
    ContentActivityCoverageStatus,
    ContentActivityObservationStatus,
    ContentActivityResult,
)
from backend_core.growth.buyer_taxonomy_v1 import resolve_buyer_taxonomy_v1
from backend_core.growth.enums import (
    BuyerLeadTier,
    BuyerProspectOwnerFilter,
    BuyerProspectRecentCollectionWindow,
)
from backend_core.growth.schemas import BuyerProspectRuleCreateInput
from backend_core.growth.targeting import (
    BuyerProspectRuleTargetingPolicy,
    BuyerTargetingPolicy,
    CandidateFactBundle,
    CollectionContextSnapshot,
    ContentActivityConstraint,
    ContentActivityFact,
    GreyDolphinActivityFact,
    IntegerRange,
    LongInactivityConstraint,
    SellerTargetingPolicy,
    TargetingEvaluation,
    TargetingEvaluationResult,
    TargetingReasonCode,
    TaxonomyAlias,
    TaxonomyDefinition,
    TaxonomyRelation,
    evaluate_buyer,
    evaluate_buyer_lead_tier,
    evaluate_buyer_prospect_rule,
    evaluate_seller,
    reduce_criterion_results,
)
from backend_core.imports.enums import ImportSourceType
from backend_core.imports.hashing import hash_document
from backend_core.influencers.enums import ContactFilter, ContactType, DataSource, Platform
from backend_core.influencers.freshness import (
    ContentActivityFreshnessPolicy,
    GreyDolphinActivityFreshnessPolicy,
)
from pydantic import ValidationError

CONTENT_ACTIVITY_AS_OF = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _facts(**changes: object) -> CandidateFactBundle:
    values: dict[str, object] = {
        "influencer_id": uuid4(),
        "platform_account_id": uuid4(),
        "platform": Platform.XIAOHONGSHU,
        "followers_count": 100,
        "notes_7d": 4,
        "notes_60d": 10,
        "source_tags": ("beauty",),
        "creator_classification_tags": ("beauty",),
        "collection_industry": "beauty",
        "source_collection_job_id": uuid4(),
        "source_collection_import_job_ids": (uuid4(),),
        "creator_classification_import_job_ids": (uuid4(),),
    }
    values.update(changes)
    return CandidateFactBundle.model_validate(values)


def _content_activity_fact(**changes: object) -> ContentActivityFact:
    values: dict[str, object] = {
        "trusted_observed_at": CONTENT_ACTIVITY_AS_OF - timedelta(days=1),
        "trusted_observation_status": ContentActivityObservationStatus.COMPLETE,
        "trusted_coverage_status": ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET,
        "trusted_activity_result": ContentActivityResult.PUBLICATION_FOUND,
        "last_publication_at": CONTENT_ACTIVITY_AS_OF - timedelta(days=90),
    }
    values.update(changes)
    return ContentActivityFact.model_validate(values)


def _huitun_runtime_fact(**changes: object) -> ContentActivityFact:
    observation_id = uuid4()
    values: dict[str, object] = {
        "huitun_observation_id": observation_id,
        "huitun_observed_at": CONTENT_ACTIVITY_AS_OF - timedelta(days=1),
        "huitun_observation_status": ContentActivityObservationStatus.COMPLETE,
        "huitun_coverage_status": ContentActivityCoverageStatus.LATEST_BOUND_PROVEN,
        "huitun_activity_result": ContentActivityResult.PUBLICATION_FOUND,
        "huitun_last_publication_at": CONTENT_ACTIVITY_AS_OF - timedelta(days=90),
        "huitun_coverage_start_at": None,
        "huitun_coverage_end_at": CONTENT_ACTIVITY_AS_OF - timedelta(days=1),
        "huitun_latest_observation_id": observation_id,
        "huitun_latest_observed_at": CONTENT_ACTIVITY_AS_OF - timedelta(days=1),
        "huitun_latest_observation_status": ContentActivityObservationStatus.COMPLETE,
        "huitun_latest_coverage_status": ContentActivityCoverageStatus.LATEST_BOUND_PROVEN,
        "huitun_latest_activity_result": ContentActivityResult.PUBLICATION_FOUND,
        "huitun_latest_same_instant_count": 1,
    }
    values.update(changes)
    return ContentActivityFact.model_validate(values)


def _grey_dolphin_activity_fact(**changes: object) -> GreyDolphinActivityFact:
    values: dict[str, object] = {
        "observed_at": CONTENT_ACTIVITY_AS_OF - timedelta(days=1),
        "source_updated_at": CONTENT_ACTIVITY_AS_OF - timedelta(days=1),
        "notes_7d": 0,
        "notes_60d": 0,
        "import_job_id": uuid4(),
        "import_row_id": uuid4(),
    }
    values.update(changes)
    return GreyDolphinActivityFact.model_validate(values)


@pytest.mark.parametrize(
    ("notes_7d", "notes_60d", "minimum", "expected"),
    [
        (1, 1, 30, TargetingEvaluationResult.NOT_MATCH),
        (1, 1, 60, TargetingEvaluationResult.NOT_MATCH),
        (1, 1, 90, TargetingEvaluationResult.NOT_MATCH),
        (1, 1, 180, TargetingEvaluationResult.NOT_MATCH),
        (0, 0, 30, TargetingEvaluationResult.MATCH),
        (0, 0, 60, TargetingEvaluationResult.MATCH),
        (0, 0, 90, TargetingEvaluationResult.UNKNOWN),
        (0, 0, 180, TargetingEvaluationResult.UNKNOWN),
        (0, 1, 30, TargetingEvaluationResult.UNKNOWN),
        (0, 1, 60, TargetingEvaluationResult.NOT_MATCH),
        (0, 1, 90, TargetingEvaluationResult.NOT_MATCH),
        (0, 1, 180, TargetingEvaluationResult.NOT_MATCH),
    ],
)
def test_long_inactivity_uses_frozen_grey_dolphin_truth_table(
    notes_7d: int,
    notes_60d: int,
    minimum: int,
    expected: TargetingEvaluationResult,
) -> None:
    result = evaluate_seller(
        SellerTargetingPolicy(
            long_inactivity=LongInactivityConstraint(minimum_inactive_days=minimum)
        ),
        _facts(
            content_activity=None,
            grey_dolphin_activity=_grey_dolphin_activity_fact(
                notes_7d=notes_7d,
                notes_60d=notes_60d,
            ),
        ),
        as_of=CONTENT_ACTIVITY_AS_OF,
        grey_dolphin_activity_freshness_policy=(
            GreyDolphinActivityFreshnessPolicy.from_day_threshold(7)
        ),
    )

    assert result.result is expected
    criterion = result.redacted_evidence["criteria"][0]
    assert criterion["criterion"] == "long_inactivity"
    assert criterion["observed"]["source"] == "GREY_DOLPHIN"
    assert "inactive_days" not in criterion["observed"]
    assert "last_publication_at" not in criterion["observed"]


def test_long_inactivity_prefers_fresh_trusted_content_activity_over_grey_dolphin() -> None:
    result = evaluate_seller(
        SellerTargetingPolicy(long_inactivity=LongInactivityConstraint(minimum_inactive_days=60)),
        _facts(
            content_activity=_content_activity_fact(
                last_publication_at=CONTENT_ACTIVITY_AS_OF - timedelta(days=1)
            ),
            grey_dolphin_activity=_grey_dolphin_activity_fact(notes_7d=0, notes_60d=0),
        ),
        as_of=CONTENT_ACTIVITY_AS_OF,
    )

    assert result.result is TargetingEvaluationResult.NOT_MATCH
    criterion = result.redacted_evidence["criteria"][0]
    assert criterion["observed"]["source"] == "TRUSTED_CONTENT_ACTIVITY"
    assert criterion["reason_code"] == "LONG_INACTIVITY_CACHE_RECENT"


@pytest.mark.parametrize(
    ("minimum", "inactive_days", "expected"),
    [
        (30, 29, TargetingEvaluationResult.NOT_MATCH),
        (30, 30, TargetingEvaluationResult.MATCH),
        (60, 59, TargetingEvaluationResult.NOT_MATCH),
        (60, 60, TargetingEvaluationResult.MATCH),
        (90, 89, TargetingEvaluationResult.NOT_MATCH),
        (90, 90, TargetingEvaluationResult.MATCH),
        (180, 179, TargetingEvaluationResult.NOT_MATCH),
        (180, 180, TargetingEvaluationResult.MATCH),
    ],
)
def test_huitun_runtime_exact_observation_uses_frozen_threshold_boundaries(
    minimum: int,
    inactive_days: int,
    expected: TargetingEvaluationResult,
) -> None:
    result = evaluate_seller(
        SellerTargetingPolicy(
            long_inactivity=LongInactivityConstraint(minimum_inactive_days=minimum)
        ),
        _facts(
            platform=Platform.DOUYIN,
            content_activity=_huitun_runtime_fact(
                huitun_last_publication_at=CONTENT_ACTIVITY_AS_OF - timedelta(days=inactive_days)
            ),
            grey_dolphin_activity=_grey_dolphin_activity_fact(notes_7d=0, notes_60d=0),
        ),
        as_of=CONTENT_ACTIVITY_AS_OF,
    )

    assert result.result is expected
    criterion = result.redacted_evidence["criteria"][0]
    assert criterion["observed"]["source"] == "HUITUN_DOUYIN_AWEME_LIST"
    assert criterion["observed"]["precision"] == "EXACT"
    assert criterion["observed"]["inactive_days"] == inactive_days


def test_huitun_terminal_empty_range_can_match_only_a_proven_lower_bound() -> None:
    observation_id = uuid4()
    fact = _huitun_runtime_fact(
        huitun_observation_id=observation_id,
        huitun_coverage_status=ContentActivityCoverageStatus.LOOKBACK_BOUNDED,
        huitun_activity_result=ContentActivityResult.AT_LEAST_LOOKBACK_INACTIVE,
        huitun_last_publication_at=None,
        huitun_coverage_start_at=CONTENT_ACTIVITY_AS_OF - timedelta(days=60),
        huitun_coverage_end_at=CONTENT_ACTIVITY_AS_OF,
        huitun_latest_observation_id=observation_id,
        huitun_observed_at=CONTENT_ACTIVITY_AS_OF,
        huitun_latest_observed_at=CONTENT_ACTIVITY_AS_OF,
    )

    matched = evaluate_seller(
        SellerTargetingPolicy(long_inactivity=LongInactivityConstraint(minimum_inactive_days=60)),
        _facts(platform=Platform.DOUYIN, content_activity=fact),
        as_of=CONTENT_ACTIVITY_AS_OF,
    )
    unknown = evaluate_seller(
        SellerTargetingPolicy(long_inactivity=LongInactivityConstraint(minimum_inactive_days=90)),
        _facts(platform=Platform.DOUYIN, content_activity=fact),
        as_of=CONTENT_ACTIVITY_AS_OF,
    )

    assert matched.result is TargetingEvaluationResult.MATCH
    matched_criterion = matched.redacted_evidence["criteria"][0]
    assert matched_criterion["observed"]["precision"] == "LOWER_BOUND"
    assert matched_criterion["observed"]["lower_bound_inactive_days"] == 60
    assert matched_criterion["observed"]["last_publication_at"] is None
    assert unknown.result is TargetingEvaluationResult.UNKNOWN


@pytest.mark.parametrize(
    ("status", "coverage", "result"),
    [
        (
            ContentActivityObservationStatus.RESULT_UNTRUSTED,
            ContentActivityCoverageStatus.UNKNOWN,
            ContentActivityResult.UNDETERMINED,
        ),
        (
            ContentActivityObservationStatus.PROVIDER_AUTH_ERROR,
            ContentActivityCoverageStatus.UNKNOWN,
            ContentActivityResult.UNDETERMINED,
        ),
        (
            ContentActivityObservationStatus.RESULT_INCOMPLETE,
            ContentActivityCoverageStatus.INCOMPLETE,
            ContentActivityResult.UNDETERMINED,
        ),
    ],
)
def test_huitun_raw_auth_or_partial_latest_attempt_is_unknown_and_cannot_fall_back_to_grey(
    status: ContentActivityObservationStatus,
    coverage: ContentActivityCoverageStatus,
    result: ContentActivityResult,
) -> None:
    fact = ContentActivityFact(
        huitun_latest_observation_id=uuid4(),
        huitun_latest_observed_at=CONTENT_ACTIVITY_AS_OF - timedelta(days=1),
        huitun_latest_observation_status=status,
        huitun_latest_coverage_status=coverage,
        huitun_latest_activity_result=result,
        huitun_latest_same_instant_count=1,
    )
    evaluation = evaluate_seller(
        SellerTargetingPolicy(long_inactivity=LongInactivityConstraint(minimum_inactive_days=30)),
        _facts(
            platform=Platform.DOUYIN,
            content_activity=fact,
            grey_dolphin_activity=_grey_dolphin_activity_fact(notes_7d=0, notes_60d=0),
        ),
        as_of=CONTENT_ACTIVITY_AS_OF,
    )

    assert evaluation.result is TargetingEvaluationResult.UNKNOWN
    criterion = evaluation.redacted_evidence["criteria"][0]
    assert criterion["observed"]["source"] == "HUITUN_DOUYIN_AWEME_LIST"
    assert criterion["observed"]["precision"] == "UNKNOWN"


def test_huitun_stale_or_same_instant_conflicting_evidence_is_unknown() -> None:
    stale = _huitun_runtime_fact(
        huitun_observed_at=CONTENT_ACTIVITY_AS_OF - timedelta(days=8),
        huitun_latest_observed_at=CONTENT_ACTIVITY_AS_OF - timedelta(days=8),
    )
    conflicting = _huitun_runtime_fact(huitun_latest_same_instant_count=2)

    for fact in (stale, conflicting):
        evaluation = evaluate_seller(
            SellerTargetingPolicy(
                long_inactivity=LongInactivityConstraint(minimum_inactive_days=30)
            ),
            _facts(platform=Platform.DOUYIN, content_activity=fact),
            as_of=CONTENT_ACTIVITY_AS_OF,
        )
        assert evaluation.result is TargetingEvaluationResult.UNKNOWN


@pytest.mark.parametrize(
    ("fact", "reason"),
    [
        (
            _grey_dolphin_activity_fact(observed_at=CONTENT_ACTIVITY_AS_OF - timedelta(days=8)),
            "LONG_INACTIVITY_GREY_DOLPHIN_STALE",
        ),
        (
            _grey_dolphin_activity_fact(notes_7d=2, notes_60d=1),
            "LONG_INACTIVITY_GREY_DOLPHIN_INVALID",
        ),
    ],
)
def test_long_inactivity_fails_closed_for_stale_or_inconsistent_grey_dolphin(
    fact: GreyDolphinActivityFact,
    reason: str,
) -> None:
    result = evaluate_seller(
        SellerTargetingPolicy(long_inactivity=LongInactivityConstraint(minimum_inactive_days=60)),
        _facts(content_activity=None, grey_dolphin_activity=fact),
        as_of=CONTENT_ACTIVITY_AS_OF,
    )

    assert result.result is TargetingEvaluationResult.UNKNOWN
    assert result.reason_codes == (reason,)


@pytest.mark.parametrize("minimum", (1, 29, 31, 59, 61, 365))
def test_long_inactivity_rejects_thresholds_outside_frozen_p0(minimum: int) -> None:
    with pytest.raises(ValidationError, match="30, 60, 90, or 180"):
        LongInactivityConstraint(minimum_inactive_days=minimum)


def test_seller_match_plus_match_is_match() -> None:
    policy = SellerTargetingPolicy(
        followers=IntegerRange(minimum=100, maximum=100),
        tags_exact_any=("beauty",),
    )

    result = evaluate_seller(policy, _facts())

    assert result.result is TargetingEvaluationResult.MATCH


def test_content_activity_inactive_threshold_uses_captured_as_of_timestamp() -> None:
    policy = SellerTargetingPolicy(
        content_activity=ContentActivityConstraint(minimum_inactive_days=60)
    )

    result = evaluate_seller(
        policy,
        _facts(
            content_activity=_content_activity_fact(
                last_publication_at=CONTENT_ACTIVITY_AS_OF - timedelta(days=60)
            )
        ),
        as_of=CONTENT_ACTIVITY_AS_OF,
        content_activity_freshness_policy=ContentActivityFreshnessPolicy.from_day_threshold(7),
    )

    assert result.result is TargetingEvaluationResult.MATCH
    assert result.reason_codes == (TargetingReasonCode.CONTENT_ACTIVITY_MATCH,)
    criterion = result.redacted_evidence["criteria"][0]
    assert criterion["observed"]["inactive_days"] == 60


def test_content_activity_recent_publication_is_not_match() -> None:
    result = evaluate_seller(
        SellerTargetingPolicy(content_activity=ContentActivityConstraint(minimum_inactive_days=60)),
        _facts(
            content_activity=_content_activity_fact(
                last_publication_at=CONTENT_ACTIVITY_AS_OF - timedelta(days=10)
            )
        ),
        as_of=CONTENT_ACTIVITY_AS_OF,
    )

    assert result.result is TargetingEvaluationResult.NOT_MATCH
    assert result.reason_codes == (TargetingReasonCode.CONTENT_ACTIVITY_RECENT,)


def test_content_activity_same_instant_attempts_are_unorderable_and_unknown() -> None:
    result = evaluate_seller(
        SellerTargetingPolicy(content_activity=ContentActivityConstraint(minimum_inactive_days=60)),
        _facts(
            content_activity=_content_activity_fact(
                latest_attempt_same_instant_count=2,
            )
        ),
        as_of=CONTENT_ACTIVITY_AS_OF,
    )

    assert result.result is TargetingEvaluationResult.UNKNOWN
    assert result.reason_codes == (TargetingReasonCode.CONTENT_ACTIVITY_UNTRUSTED,)


def test_content_activity_and_tag_constraint_combine_without_cross_signal_shortcut() -> None:
    result = evaluate_seller(
        SellerTargetingPolicy(
            tags_exact_any=("beauty",),
            content_activity=ContentActivityConstraint(minimum_inactive_days=60),
        ),
        _facts(
            source_tags=("beauty",),
            content_activity=_content_activity_fact(
                last_publication_at=CONTENT_ACTIVITY_AS_OF - timedelta(days=90)
            ),
        ),
        as_of=CONTENT_ACTIVITY_AS_OF,
    )

    assert result.result is TargetingEvaluationResult.MATCH
    assert [item["criterion"] for item in result.redacted_evidence["criteria"]] == [
        "tags_exact_any",
        "content_activity",
    ]
    assert result.redacted_evidence["criteria"][1]["observed"]["inactive_days"] == 90


@pytest.mark.parametrize(
    ("fact", "reason"),
    [
        (
            None,
            TargetingReasonCode.CONTENT_ACTIVITY_MISSING,
        ),
        (
            _content_activity_fact(
                trusted_observation_status=ContentActivityObservationStatus.PROVIDER_ERROR,
                trusted_coverage_status=ContentActivityCoverageStatus.UNKNOWN,
                trusted_activity_result=ContentActivityResult.UNDETERMINED,
                last_publication_at=None,
            ),
            TargetingReasonCode.CONTENT_ACTIVITY_UNTRUSTED,
        ),
        (
            _content_activity_fact(
                trusted_observation_status=ContentActivityObservationStatus.RESULT_INCOMPLETE,
                trusted_coverage_status=ContentActivityCoverageStatus.INCOMPLETE,
                trusted_activity_result=ContentActivityResult.UNDETERMINED,
                last_publication_at=None,
            ),
            TargetingReasonCode.CONTENT_ACTIVITY_INCOMPLETE,
        ),
        (
            _content_activity_fact(
                trusted_observed_at=None,
                trusted_observation_status=None,
                trusted_coverage_status=None,
                trusted_activity_result=None,
                last_publication_at=None,
                latest_attempt_observed_at=CONTENT_ACTIVITY_AS_OF,
                latest_attempt_observation_status=ContentActivityObservationStatus.RESULT_INCOMPLETE,
                latest_attempt_coverage_status=ContentActivityCoverageStatus.INCOMPLETE,
                latest_attempt_activity_result=ContentActivityResult.UNDETERMINED,
            ),
            TargetingReasonCode.CONTENT_ACTIVITY_INCOMPLETE,
        ),
        (
            _content_activity_fact(
                trusted_observed_at=None,
                trusted_observation_status=None,
                trusted_coverage_status=None,
                trusted_activity_result=None,
                last_publication_at=None,
                latest_attempt_observed_at=CONTENT_ACTIVITY_AS_OF,
                latest_attempt_observation_status=ContentActivityObservationStatus.PROVIDER_ERROR,
                latest_attempt_coverage_status=ContentActivityCoverageStatus.UNKNOWN,
                latest_attempt_activity_result=ContentActivityResult.UNDETERMINED,
            ),
            TargetingReasonCode.CONTENT_ACTIVITY_UNTRUSTED,
        ),
        (
            _content_activity_fact(
                latest_attempt_observed_at=CONTENT_ACTIVITY_AS_OF,
                latest_attempt_observation_status=ContentActivityObservationStatus.RESULT_INCOMPLETE,
                latest_attempt_coverage_status=ContentActivityCoverageStatus.INCOMPLETE,
                latest_attempt_activity_result=ContentActivityResult.UNDETERMINED,
            ),
            TargetingReasonCode.CONTENT_ACTIVITY_INCOMPLETE,
        ),
        (
            _content_activity_fact(
                latest_attempt_observed_at=CONTENT_ACTIVITY_AS_OF,
                latest_attempt_observation_status=ContentActivityObservationStatus.PROVIDER_ERROR,
                latest_attempt_coverage_status=ContentActivityCoverageStatus.UNKNOWN,
                latest_attempt_activity_result=ContentActivityResult.UNDETERMINED,
            ),
            TargetingReasonCode.CONTENT_ACTIVITY_UNTRUSTED,
        ),
        (
            _content_activity_fact(
                latest_attempt_observed_at=CONTENT_ACTIVITY_AS_OF - timedelta(days=1),
                latest_attempt_observation_status=ContentActivityObservationStatus.RESULT_UNTRUSTED,
                latest_attempt_coverage_status=ContentActivityCoverageStatus.UNKNOWN,
                latest_attempt_activity_result=ContentActivityResult.UNDETERMINED,
            ),
            TargetingReasonCode.CONTENT_ACTIVITY_UNTRUSTED,
        ),
        (
            _content_activity_fact(
                trusted_observed_at=CONTENT_ACTIVITY_AS_OF - timedelta(days=8),
            ),
            TargetingReasonCode.CONTENT_ACTIVITY_STALE,
        ),
        (
            _content_activity_fact(
                trusted_activity_result=ContentActivityResult.NO_PUBLIC_CONTENT,
                last_publication_at=None,
            ),
            TargetingReasonCode.CONTENT_ACTIVITY_NO_PUBLIC_CONTENT,
        ),
    ],
)
def test_content_activity_uncertain_or_no_public_content_is_unknown(
    fact: ContentActivityFact | None,
    reason: TargetingReasonCode,
) -> None:
    result = evaluate_seller(
        SellerTargetingPolicy(content_activity=ContentActivityConstraint(minimum_inactive_days=60)),
        _facts(content_activity=fact),
        as_of=CONTENT_ACTIVITY_AS_OF,
    )

    assert result.result is TargetingEvaluationResult.UNKNOWN
    assert result.reason_codes == (reason,)
    criterion = result.redacted_evidence["criteria"][0]
    assert criterion["observed"]["last_publication_at"] is None


def test_content_activity_constraint_does_not_change_existing_seller_v1_defaults() -> None:
    policy = SellerTargetingPolicy.model_validate(
        {"schema_version": 1, "policy_type": "SELLER_V1", "tags_exact_any": ["beauty"]}
    )

    assert policy.content_activity is None
    legacy_definition = policy.model_dump(mode="json")
    assert legacy_definition.pop("content_activity") is None
    assert legacy_definition.pop("long_inactivity") is None
    assert policy.canonical_hash == hash_document(legacy_definition)
    assert (
        SellerTargetingPolicy(
            tags_exact_any=("beauty",),
            content_activity=ContentActivityConstraint(minimum_inactive_days=60),
        ).canonical_hash
        != policy.canonical_hash
    )
    assert evaluate_seller(policy, _facts()).result is TargetingEvaluationResult.MATCH


def test_seller_contact_availability_uses_canonical_contact_types() -> None:
    email_facts = _facts(current_contact_types=(ContactType.EMAIL,))
    no_contact_facts = _facts(current_contact_types=())

    assert (
        evaluate_seller(
            SellerTargetingPolicy(contact_availability=ContactFilter.HAS_CONTACT), email_facts
        ).result
        is TargetingEvaluationResult.MATCH
    )
    assert (
        evaluate_seller(
            SellerTargetingPolicy(contact_availability=ContactFilter.HAS_EMAIL), email_facts
        ).result
        is TargetingEvaluationResult.MATCH
    )
    assert (
        evaluate_seller(
            SellerTargetingPolicy(contact_availability=ContactFilter.NO_CONTACT), no_contact_facts
        ).result
        is TargetingEvaluationResult.MATCH
    )
    assert (
        evaluate_seller(
            SellerTargetingPolicy(contact_availability=ContactFilter.HAS_EMAIL), no_contact_facts
        ).result
        is TargetingEvaluationResult.NOT_MATCH
    )


def test_seller_match_plus_unknown_is_unknown() -> None:
    policy = SellerTargetingPolicy(
        followers=IntegerRange(minimum=100),
        notes_7d=IntegerRange(minimum=1),
    )

    result = evaluate_seller(policy, _facts(notes_7d=None))

    assert result.result is TargetingEvaluationResult.UNKNOWN
    assert "ACTIVITY_MISSING" in result.reason_codes


def test_seller_not_match_plus_unknown_is_not_match() -> None:
    policy = SellerTargetingPolicy(
        followers=IntegerRange(minimum=101),
        notes_7d=IntegerRange(minimum=1),
    )

    result = evaluate_seller(policy, _facts(notes_7d=None))

    assert result.result is TargetingEvaluationResult.NOT_MATCH


def test_seller_not_match_plus_match_is_not_match() -> None:
    policy = SellerTargetingPolicy(
        followers=IntegerRange(minimum=101),
        tags_exact_any=("beauty",),
    )

    result = evaluate_seller(policy, _facts())

    assert result.result is TargetingEvaluationResult.NOT_MATCH


def test_seller_multi_criterion_reduction_is_order_independent() -> None:
    criteria = (
        TargetingEvaluationResult.MATCH,
        TargetingEvaluationResult.UNKNOWN,
        TargetingEvaluationResult.NOT_MATCH,
        TargetingEvaluationResult.MATCH,
    )

    assert {reduce_criterion_results(order) for order in permutations(criteria)} == {
        TargetingEvaluationResult.NOT_MATCH
    }


@pytest.mark.parametrize(
    ("followers", "expected"),
    [
        (99, TargetingEvaluationResult.NOT_MATCH),
        (100, TargetingEvaluationResult.MATCH),
        (200, TargetingEvaluationResult.MATCH),
        (201, TargetingEvaluationResult.NOT_MATCH),
        (None, TargetingEvaluationResult.UNKNOWN),
    ],
)
def test_seller_followers_bounds_are_inclusive(
    followers: int | None,
    expected: TargetingEvaluationResult,
) -> None:
    result = evaluate_seller(
        SellerTargetingPolicy(followers=IntegerRange(minimum=100, maximum=200)),
        _facts(followers_count=followers),
    )

    assert result.result is expected


@pytest.mark.parametrize(
    ("notes_7d", "notes_60d", "expected"),
    [
        (3, 6, TargetingEvaluationResult.MATCH),
        (2, 6, TargetingEvaluationResult.NOT_MATCH),
        (3, 5, TargetingEvaluationResult.NOT_MATCH),
        (None, 6, TargetingEvaluationResult.UNKNOWN),
        (3, None, TargetingEvaluationResult.UNKNOWN),
    ],
)
def test_seller_activity_bounds_and_missing_values(
    notes_7d: int | None,
    notes_60d: int | None,
    expected: TargetingEvaluationResult,
) -> None:
    result = evaluate_seller(
        SellerTargetingPolicy(
            notes_7d=IntegerRange(minimum=3),
            notes_60d=IntegerRange(minimum=6),
        ),
        _facts(notes_7d=notes_7d, notes_60d=notes_60d),
    )

    assert result.result is expected


def test_seller_tags_are_exact_and_missing_tags_are_unknown() -> None:
    policy = SellerTargetingPolicy(tags_exact_any=("beauty", "food"))

    assert evaluate_seller(policy, _facts(source_tags=("Beauty",))).result is (
        TargetingEvaluationResult.NOT_MATCH
    )
    assert (
        evaluate_seller(policy, _facts(source_tags=())).result is TargetingEvaluationResult.UNKNOWN
    )
    assert evaluate_seller(policy, _facts(source_tags=("food",))).result is (
        TargetingEvaluationResult.MATCH
    )


def test_policy_hash_is_deterministic_and_policy_is_immutable() -> None:
    first = SellerTargetingPolicy(
        tags_exact_any=("food", "beauty"),
        followers=IntegerRange(minimum=100),
    )
    second = SellerTargetingPolicy(
        followers=IntegerRange(minimum=100),
        tags_exact_any=("beauty", "food"),
    )

    assert first.canonical_hash == second.canonical_hash
    with pytest.raises(ValidationError):
        first.tags_exact_any = ("other",)  # type: ignore[misc]


def test_buyer_taxonomy_hash_canonicalizes_alias_and_relation_order() -> None:
    first = BuyerTargetingPolicy(
        taxonomy=TaxonomyDefinition(
            taxonomy_version="reviewed-v1",
            reviewed=True,
            categories=("beauty", "gaming"),
            aliases=(
                TaxonomyAlias(label="beauty-alias", category_id="beauty"),
                TaxonomyAlias(label="gaming-alias", category_id="gaming"),
            ),
            incompatible=(TaxonomyRelation(left_category_id="beauty", right_category_id="gaming"),),
        )
    )
    second = BuyerTargetingPolicy(
        taxonomy=TaxonomyDefinition(
            taxonomy_version="reviewed-v1",
            reviewed=True,
            categories=("gaming", "beauty"),
            aliases=(
                TaxonomyAlias(label="gaming-alias", category_id="gaming"),
                TaxonomyAlias(label="beauty-alias", category_id="beauty"),
            ),
            incompatible=(TaxonomyRelation(left_category_id="gaming", right_category_id="beauty"),),
        )
    )

    assert first.taxonomy.model_dump(mode="json") == second.taxonomy.model_dump(mode="json")
    assert first.canonical_hash == second.canonical_hash


def test_collection_context_snapshot_is_typed_and_immutable() -> None:
    snapshot = CollectionContextSnapshot(
        source_collection_job_id=uuid4(),
        industry=" beauty ",
        subdirection=" makeup ",
    )

    assert snapshot.industry == "beauty"
    assert snapshot.subdirection == "makeup"
    with pytest.raises(ValidationError):
        snapshot.industry = "gaming"  # type: ignore[misc]


def test_seller_evidence_contains_no_raw_contact_values() -> None:
    result = evaluate_seller(
        SellerTargetingPolicy(tags_exact_any=("beauty",)),
        _facts(),
    )
    rendered = str(result.redacted_evidence)

    assert "contact@example.com" not in rendered
    assert "normalized_value" not in rendered


def test_invalid_fact_tags_become_unknown_instead_of_rejecting_the_candidate() -> None:
    facts = _facts(source_tags=["beauty", 1])

    result = evaluate_seller(SellerTargetingPolicy(tags_exact_any=("beauty",)), facts)

    assert facts.source_tags is None
    assert result.result is TargetingEvaluationResult.UNKNOWN
    assert result.reason_codes == (TargetingReasonCode.TRACK_MISSING,)


def test_reason_codes_are_closed() -> None:
    with pytest.raises(ValidationError):
        TargetingEvaluation(
            result=TargetingEvaluationResult.UNKNOWN,
            reason_codes=("UNSUPPORTED_REASON",),
            redacted_evidence={"schema_version": 1},
        )


def _reviewed_taxonomy() -> TaxonomyDefinition:
    return resolve_buyer_taxonomy_v1()


def test_buyer_aligned_mismatch_and_unknown_outcomes() -> None:
    policy = BuyerTargetingPolicy(taxonomy=_reviewed_taxonomy())

    aligned = evaluate_buyer(
        policy, _facts(collection_industry="美食", creator_classification_tags=("美食",))
    )
    mismatch = evaluate_buyer(
        policy, _facts(collection_industry="美食", creator_classification_tags=("科技",))
    )
    unmapped = evaluate_buyer(
        policy, _facts(collection_industry="美食", creator_classification_tags=("unknown",))
    )

    assert aligned.result is TargetingEvaluationResult.NOT_MATCH
    assert aligned.reason_codes == ("CATEGORY_ALIGNED",)
    assert mismatch.result is TargetingEvaluationResult.MATCH
    assert mismatch.reason_codes == ("CATEGORY_MISMATCH",)
    assert unmapped.result is TargetingEvaluationResult.UNKNOWN
    assert unmapped.reason_codes == ("CREATOR_CATEGORY_UNMAPPED",)


def test_buyer_evidence_captures_provenance_without_purchase_claims() -> None:
    collection_job_id = uuid4()
    import_job_id = uuid4()

    result = evaluate_buyer(
        BuyerTargetingPolicy(taxonomy=_reviewed_taxonomy()),
        _facts(
            source_collection_job_id=collection_job_id,
            source_collection_import_job_ids=(import_job_id,),
            creator_classification_import_job_ids=(import_job_id,),
            collection_industry="美食",
            creator_classification_tags=("科技",),
        ),
    )

    evidence = result.redacted_evidence
    assert evidence["collection_context"]["source_collection_job_id"] == str(collection_job_id)
    assert evidence["collection_context"]["provenance_import_job_ids"] == [str(import_job_id)]
    assert evidence["creator_classification"]["provenance_import_job_ids"] == [str(import_job_id)]
    assert "purchased_account" not in str(evidence)


def test_buyer_without_reviewed_taxonomy_is_safely_unknown() -> None:
    policy = BuyerTargetingPolicy(
        taxonomy=TaxonomyDefinition(
            taxonomy_version="unreviewed-v1",
            reviewed=False,
            categories=("beauty",),
        )
    )

    result = evaluate_buyer(policy, _facts())

    assert result.result is TargetingEvaluationResult.UNKNOWN
    assert result.reason_codes == ("NO_COMPARISON_RULE",)


def test_buyer_missing_collection_context_is_unknown() -> None:
    result = evaluate_buyer(
        BuyerTargetingPolicy(taxonomy=_reviewed_taxonomy()),
        _facts(collection_industry=None),
    )

    assert result.result is TargetingEvaluationResult.UNKNOWN
    assert result.reason_codes == ("COLLECTION_CONTEXT_MISSING",)


def _market_policy(
    _source_collection_job_id: object,
    **changes: object,
) -> BuyerProspectRuleTargetingPolicy:
    values: dict[str, object] = {
        "taxonomy": _reviewed_taxonomy(),
        "category_ids": ("FOOD",),
        "follower_min": 100,
        "follower_max": 200,
        "buyer_lead_tiers": (BuyerLeadTier.HIGH,),
        "source_type": ImportSourceType.MANUAL_HUITUN_EXPORT,
        "recent_collection_window": BuyerProspectRecentCollectionWindow.ALL,
        "prospect_owner_filter": BuyerProspectOwnerFilter.ANY,
        "exclude_contacted": False,
    }
    values.update(changes)
    return BuyerProspectRuleTargetingPolicy.model_validate(values)


def _market_facts(source_collection_job_id: object, **changes: object) -> CandidateFactBundle:
    values: dict[str, object] = {
        "platform": Platform.DOUYIN,
        "source": DataSource.HUITUN,
        "collection_industry": "科技",
        "creator_classification_tags": ("美食",),
        "source_collection_job_id": source_collection_job_id,
        "buyer_source_type": ImportSourceType.MANUAL_HUITUN_EXPORT,
        "source_collection_import_job_ids": (uuid4(),),
        "creator_classification_import_job_ids": (uuid4(),),
        "followers_count": 100,
        "follower_metric_snapshot_id": uuid4(),
        "buyer_source_import_job_file_id": uuid4(),
        "buyer_source_import_job_id": uuid4(),
        "buyer_source_import_row_id": uuid4(),
        "buyer_source_acquired_at": CONTENT_ACTIVITY_AS_OF - timedelta(days=7),
    }
    values.update(changes)
    return _facts(**values)


def test_market_prospect_rule_validation_is_closed_and_canonical() -> None:
    operator_id = uuid4()
    valid = BuyerProspectRuleCreateInput(
        name="Market prospects",
        category_ids=("FOOD", "BEAUTY"),
        follower_min=100,
        follower_max=200,
        buyer_lead_tiers=(BuyerLeadTier.UNKNOWN, BuyerLeadTier.HIGH),
        source_type=ImportSourceType.MANUAL_HUITUN_EXPORT,
        recent_collection_window=BuyerProspectRecentCollectionWindow.DAYS_30,
        prospect_owner_filter=BuyerProspectOwnerFilter.OPERATOR,
        prospect_owner_operator_id=operator_id,
        exclude_contacted=True,
    )

    assert valid.category_ids == ("BEAUTY", "FOOD")
    assert valid.buyer_lead_tiers == (BuyerLeadTier.HIGH, BuyerLeadTier.UNKNOWN)
    unrestricted = BuyerProspectRuleCreateInput(
        name="Unrestricted current categories",
        buyer_lead_tiers=(BuyerLeadTier.CHANGED,),
        source_type=ImportSourceType.MANUAL_HUITUN_EXPORT,
        recent_collection_window=BuyerProspectRecentCollectionWindow.ALL,
    )
    assert unrestricted.category_ids == ()
    with pytest.raises(ValidationError):
        BuyerProspectRuleCreateInput(
            name="Invalid follower range",
            follower_min=201,
            follower_max=200,
            buyer_lead_tiers=(BuyerLeadTier.HIGH,),
            source_type=ImportSourceType.MANUAL_HUITUN_EXPORT,
            recent_collection_window=BuyerProspectRecentCollectionWindow.ALL,
        )
    with pytest.raises(ValidationError):
        BuyerProspectRuleCreateInput(
            name="Missing selected owner",
            buyer_lead_tiers=(BuyerLeadTier.HIGH,),
            source_type=ImportSourceType.MANUAL_HUITUN_EXPORT,
            recent_collection_window=BuyerProspectRecentCollectionWindow.ALL,
            prospect_owner_filter=BuyerProspectOwnerFilter.OPERATOR,
        )
    with pytest.raises(ValidationError):
        _market_policy(uuid4(), category_ids=("NOT_A_CATEGORY",))


def test_market_prospect_rule_source_type_participates_in_execution() -> None:
    source_collection_job_id = uuid4()

    result = evaluate_buyer_prospect_rule(
        _market_policy(source_collection_job_id),
        _market_facts(
            source_collection_job_id,
            buyer_source_type=ImportSourceType.GENERIC_CSV,
        ),
        as_of=CONTENT_ACTIVITY_AS_OF,
    )

    assert result.result is TargetingEvaluationResult.UNKNOWN
    source_criterion = next(
        item
        for item in result.redacted_evidence["criteria"]
        if item["criterion"] == "recent_collection_window"
    )
    assert source_criterion["reason_code"] == "BUYER_SOURCE_TYPE_MISMATCH"


@pytest.mark.parametrize(
    ("followers_count", "snapshot_id", "expected"),
    [
        (99, uuid4(), TargetingEvaluationResult.NOT_MATCH),
        (100, uuid4(), TargetingEvaluationResult.MATCH),
        (200, uuid4(), TargetingEvaluationResult.MATCH),
        (201, uuid4(), TargetingEvaluationResult.NOT_MATCH),
        (None, None, TargetingEvaluationResult.UNKNOWN),
    ],
)
def test_market_prospect_rule_category_tier_and_follower_filters(
    followers_count: int | None,
    snapshot_id: object,
    expected: TargetingEvaluationResult,
) -> None:
    source_collection_job_id = uuid4()
    policy = _market_policy(source_collection_job_id)
    result = evaluate_buyer_prospect_rule(
        policy,
        _market_facts(
            source_collection_job_id,
            followers_count=followers_count,
            follower_metric_snapshot_id=snapshot_id,
        ),
        as_of=CONTENT_ACTIVITY_AS_OF,
    )

    assert result.result is expected
    followers = next(
        item for item in result.redacted_evidence["criteria"] if item["criterion"] == "followers"
    )
    assert followers["result"] == expected.value

    category_not_selected = evaluate_buyer_prospect_rule(
        _market_policy(source_collection_job_id, category_ids=("BEAUTY",)),
        _market_facts(source_collection_job_id),
        as_of=CONTENT_ACTIVITY_AS_OF,
    )
    tier_not_selected = evaluate_buyer_prospect_rule(
        _market_policy(
            source_collection_job_id,
            buyer_lead_tiers=(BuyerLeadTier.SAME_CATEGORY,),
        ),
        _market_facts(source_collection_job_id),
        as_of=CONTENT_ACTIVITY_AS_OF,
    )
    assert category_not_selected.result is TargetingEvaluationResult.NOT_MATCH
    assert tier_not_selected.result is TargetingEvaluationResult.NOT_MATCH


def test_market_prospect_category_filter_is_optional_secondary_current_category_filter() -> None:
    source_collection_job_id = uuid4()
    facts = _market_facts(
        source_collection_job_id,
        collection_industry="影视",
        creator_classification_tags=("汽车",),
    )

    unrestricted = evaluate_buyer_prospect_rule(
        _market_policy(
            source_collection_job_id,
            category_ids=(),
            buyer_lead_tiers=(BuyerLeadTier.CHANGED,),
        ),
        facts,
        as_of=CONTENT_ACTIVITY_AS_OF,
    )
    narrowed = evaluate_buyer_prospect_rule(
        _market_policy(
            source_collection_job_id,
            category_ids=("AUTO",),
            buyer_lead_tiers=(BuyerLeadTier.CHANGED,),
        ),
        facts,
        as_of=CONTENT_ACTIVITY_AS_OF,
    )
    excluded = evaluate_buyer_prospect_rule(
        _market_policy(
            source_collection_job_id,
            category_ids=("TECHNOLOGY",),
            buyer_lead_tiers=(BuyerLeadTier.CHANGED,),
        ),
        facts,
        as_of=CONTENT_ACTIVITY_AS_OF,
    )

    assert unrestricted.result is TargetingEvaluationResult.MATCH
    assert narrowed.result is TargetingEvaluationResult.MATCH
    assert excluded.result is TargetingEvaluationResult.NOT_MATCH
    criterion = next(
        item
        for item in unrestricted.redacted_evidence["criteria"]
        if item["criterion"] == "category_ids"
    )
    assert criterion["configured"] == {"category_ids": []}
    assert criterion["observed"] == {"creator_category_ids": ["AUTO"]}
    relation_summary = unrestricted.redacted_evidence["buyer_relation_summary"]
    assert relation_summary["client_categories"] == ["FILM_TV"]
    assert relation_summary["creator_categories"] == ["AUTO"]


@pytest.mark.parametrize(
    ("window", "age_days", "expected"),
    [
        (BuyerProspectRecentCollectionWindow.DAYS_7, 7, TargetingEvaluationResult.MATCH),
        (BuyerProspectRecentCollectionWindow.DAYS_7, 8, TargetingEvaluationResult.NOT_MATCH),
        (BuyerProspectRecentCollectionWindow.DAYS_30, 30, TargetingEvaluationResult.MATCH),
        (BuyerProspectRecentCollectionWindow.DAYS_30, 31, TargetingEvaluationResult.NOT_MATCH),
        (BuyerProspectRecentCollectionWindow.DAYS_60, 60, TargetingEvaluationResult.MATCH),
        (BuyerProspectRecentCollectionWindow.DAYS_60, 61, TargetingEvaluationResult.NOT_MATCH),
        (BuyerProspectRecentCollectionWindow.DAYS_90, 90, TargetingEvaluationResult.MATCH),
        (BuyerProspectRecentCollectionWindow.DAYS_90, 91, TargetingEvaluationResult.NOT_MATCH),
        (BuyerProspectRecentCollectionWindow.ALL, 365, TargetingEvaluationResult.MATCH),
    ],
)
def test_market_prospect_rule_collection_windows_are_inclusive(
    window: BuyerProspectRecentCollectionWindow,
    age_days: int,
    expected: TargetingEvaluationResult,
) -> None:
    source_collection_job_id = uuid4()
    result = evaluate_buyer_prospect_rule(
        _market_policy(source_collection_job_id, recent_collection_window=window),
        _market_facts(
            source_collection_job_id,
            buyer_source_acquired_at=CONTENT_ACTIVITY_AS_OF - timedelta(days=age_days),
        ),
        as_of=CONTENT_ACTIVITY_AS_OF,
    )

    assert result.result is expected


def test_market_prospect_rule_owner_and_contacted_filters_are_evidence_based() -> None:
    source_collection_job_id = uuid4()
    owner_id = uuid4()
    other_owner_id = uuid4()
    facts = _market_facts(source_collection_job_id, owner_operator_id=owner_id)

    assert (
        evaluate_buyer_prospect_rule(
            _market_policy(source_collection_job_id), facts, as_of=CONTENT_ACTIVITY_AS_OF
        ).result
        is TargetingEvaluationResult.MATCH
    )
    assert (
        evaluate_buyer_prospect_rule(
            _market_policy(
                source_collection_job_id,
                prospect_owner_filter=BuyerProspectOwnerFilter.UNASSIGNED,
            ),
            facts,
            as_of=CONTENT_ACTIVITY_AS_OF,
        ).result
        is TargetingEvaluationResult.NOT_MATCH
    )
    assert (
        evaluate_buyer_prospect_rule(
            _market_policy(
                source_collection_job_id,
                prospect_owner_filter=BuyerProspectOwnerFilter.OPERATOR,
                prospect_owner_operator_id=owner_id,
            ),
            facts,
            as_of=CONTENT_ACTIVITY_AS_OF,
        ).result
        is TargetingEvaluationResult.MATCH
    )
    assert (
        evaluate_buyer_prospect_rule(
            _market_policy(
                source_collection_job_id,
                prospect_owner_filter=BuyerProspectOwnerFilter.OPERATOR,
                prospect_owner_operator_id=other_owner_id,
            ),
            facts,
            as_of=CONTENT_ACTIVITY_AS_OF,
        ).result
        is TargetingEvaluationResult.NOT_MATCH
    )
    assert (
        evaluate_buyer_prospect_rule(
            _market_policy(
                source_collection_job_id,
                prospect_owner_filter=BuyerProspectOwnerFilter.UNASSIGNED,
            ),
            _market_facts(source_collection_job_id, owner_operator_id=None),
            as_of=CONTENT_ACTIVITY_AS_OF,
        ).result
        is TargetingEvaluationResult.MATCH
    )

    contact_only = _market_facts(
        source_collection_job_id,
        current_contact_types=(ContactType.EMAIL,),
    )
    assert (
        evaluate_buyer_prospect_rule(
            _market_policy(source_collection_job_id, exclude_contacted=True),
            contact_only,
            as_of=CONTENT_ACTIVITY_AS_OF,
        ).result
        is TargetingEvaluationResult.MATCH
    )
    assert (
        evaluate_buyer_prospect_rule(
            _market_policy(source_collection_job_id, exclude_contacted=True),
            _market_facts(
                source_collection_job_id,
                contacted_outreach_event_id=uuid4(),
                contacted_outreach_occurred_at=CONTENT_ACTIVITY_AS_OF,
            ),
            as_of=CONTENT_ACTIVITY_AS_OF,
        ).result
        is TargetingEvaluationResult.NOT_MATCH
    )


def test_buyer_evaluator_uses_trustworthy_douyin_creator_classification() -> None:
    source_collection_job_id = uuid4()
    result = evaluate_buyer(
        BuyerTargetingPolicy(taxonomy=_reviewed_taxonomy()),
        _market_facts(source_collection_job_id),
    )

    assert result.result is TargetingEvaluationResult.MATCH
    assert result.reason_codes == (TargetingReasonCode.CATEGORY_MISMATCH,)


@pytest.mark.parametrize(
    ("source_industry", "creator_categories", "expected_tier"),
    [
        ("影视", ("影视",), BuyerLeadTier.SAME_CATEGORY),
        ("科技", ("数码",), BuyerLeadTier.RELATED),
        ("科技", ("美食",), BuyerLeadTier.HIGH),
        ("科技", ("科技", "美食"), BuyerLeadTier.CHANGED),
        # Huitun's imported 分类 is the creator's own classification, not
        # CollectionJob.industry. These different but known categories remain
        # reliable Buyer evidence rather than being treated as missing.
        ("影视", ("汽车",), BuyerLeadTier.CHANGED),
        ("影视", ("科技",), BuyerLeadTier.CHANGED),
    ],
)
def test_buyer_lead_tiers_keep_huitun_source_and_creator_categories_distinct(
    source_industry: str,
    creator_categories: tuple[str, ...],
    expected_tier: BuyerLeadTier,
) -> None:
    source_collection_job_id = uuid4()

    decision = evaluate_buyer_lead_tier(
        BuyerTargetingPolicy(taxonomy=_reviewed_taxonomy()),
        _market_facts(
            source_collection_job_id,
            collection_industry=source_industry,
            creator_classification_tags=creator_categories,
        ),
    )

    assert decision.tier is expected_tier
    assert decision.relation_summary["status"] == "RELIABLE"


@pytest.mark.parametrize(
    "creator_categories",
    [
        (),
        ("unmapped Huitun 分类",),
    ],
)
def test_buyer_lead_tier_is_unknown_only_for_missing_or_unmapped_creator_evidence(
    creator_categories: tuple[str, ...],
) -> None:
    source_collection_job_id = uuid4()

    decision = evaluate_buyer_lead_tier(
        BuyerTargetingPolicy(taxonomy=_reviewed_taxonomy()),
        _market_facts(
            source_collection_job_id,
            collection_industry="影视",
            creator_classification_tags=creator_categories,
        ),
    )

    assert decision.tier is BuyerLeadTier.UNKNOWN
    assert decision.relation_summary["status"] == "UNRELIABLE"
    assert decision.relation_summary["reason_code"] == (
        "CREATOR_CLASSIFICATION_MISSING" if not creator_categories else "CREATOR_CATEGORY_UNMAPPED"
    )

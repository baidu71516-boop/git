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
from backend_core.growth.targeting import (
    BuyerTargetingPolicy,
    CandidateFactBundle,
    CollectionContextSnapshot,
    ContentActivityConstraint,
    ContentActivityFact,
    IntegerRange,
    SellerTargetingPolicy,
    TargetingEvaluation,
    TargetingEvaluationResult,
    TargetingReasonCode,
    TaxonomyAlias,
    TaxonomyDefinition,
    TaxonomyRelation,
    evaluate_buyer,
    evaluate_seller,
    reduce_criterion_results,
)
from backend_core.imports.hashing import hash_document
from backend_core.influencers.enums import ContactFilter, ContactType, Platform
from backend_core.influencers.freshness import ContentActivityFreshnessPolicy
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
    return TaxonomyDefinition(
        taxonomy_version="reviewed-v1",
        reviewed=True,
        categories=("beauty", "gaming"),
        incompatible=(TaxonomyRelation(left_category_id="beauty", right_category_id="gaming"),),
    )


def test_buyer_aligned_mismatch_and_unknown_outcomes() -> None:
    policy = BuyerTargetingPolicy(taxonomy=_reviewed_taxonomy())

    aligned = evaluate_buyer(policy, _facts())
    mismatch = evaluate_buyer(policy, _facts(creator_classification_tags=("gaming",)))
    unmapped = evaluate_buyer(policy, _facts(creator_classification_tags=("unknown",)))

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
            creator_classification_tags=("gaming",),
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

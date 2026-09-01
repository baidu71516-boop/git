"""Approved Buyer Taxonomy V1 artifact and trust-boundary tests."""

from __future__ import annotations

from uuid import uuid4

import pytest
from backend_core.growth.buyer_taxonomy_v1 import (
    BUYER_TAXONOMY_V1_HASH,
    BUYER_TAXONOMY_V1_ID,
    BUYER_TAXONOMY_V1_VERSION,
    buyer_taxonomy_v1_document,
    is_trusted_buyer_taxonomy_v1,
    resolve_buyer_taxonomy_v1,
)
from backend_core.growth.enums import BuyerLeadTier
from backend_core.growth.targeting import (
    BuyerCategoryRelation,
    BuyerTargetingPolicy,
    CandidateFactBundle,
    TargetingEvaluationResult,
    TargetingReasonCode,
    TaxonomyDefinition,
    evaluate_buyer,
    evaluate_buyer_lead_tier,
)
from backend_core.imports.hashing import hash_document
from backend_core.influencers.enums import Platform
from pydantic import ValidationError


def _facts(
    *,
    collection: str | None = "美食",
    creator: tuple[str, ...] | None = ("美食",),
    ambiguous: bool = False,
) -> CandidateFactBundle:
    import_job_id = uuid4()
    return CandidateFactBundle(
        influencer_id=uuid4(),
        platform_account_id=uuid4(),
        platform=Platform.XIAOHONGSHU,
        source_collection_job_id=uuid4(),
        source_collection_import_job_ids=(import_job_id,),
        creator_classification_import_job_ids=(import_job_id,),
        collection_industry=collection,
        creator_classification_tags=creator,
        creator_classification_ambiguous=ambiguous,
    )


def _policy() -> BuyerTargetingPolicy:
    return BuyerTargetingPolicy(taxonomy=resolve_buyer_taxonomy_v1())


def test_artifact_hash_is_deterministic_and_snapshot_is_immutable() -> None:
    document = buyer_taxonomy_v1_document()

    assert document["taxonomy_id"] == BUYER_TAXONOMY_V1_ID
    assert document["taxonomy_version"] == BUYER_TAXONOMY_V1_VERSION
    assert hash_document(document) == BUYER_TAXONOMY_V1_HASH
    with pytest.raises(TypeError):
        document["taxonomy_id"] = "tampered"  # type: ignore[index]
    with pytest.raises(ValidationError):
        resolve_buyer_taxonomy_v1().taxonomy_version = "tampered"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("label", "category_id"),
    (("3C", "DIGITAL_3C"), ("数码", "DIGITAL_3C"), ("母婴", "PARENTING"), ("生活Vlog", "VLOG")),
)
def test_approved_aliases_resolve_exactly(label: str, category_id: str) -> None:
    assert resolve_buyer_taxonomy_v1().normalize(label) == category_id


def test_finance_parent_child_and_entertainment_marketing_no_rule() -> None:
    finance = evaluate_buyer(_policy(), _facts(collection="财经", creator=("财经资讯",)))
    entertainment_marketing = evaluate_buyer(
        _policy(), _facts(collection="娱乐", creator=("娱乐营销",))
    )

    assert finance.result is TargetingEvaluationResult.NOT_MATCH
    assert finance.reason_codes == (TargetingReasonCode.CATEGORY_ALIGNED,)
    assert entertainment_marketing.result is TargetingEvaluationResult.UNKNOWN
    assert entertainment_marketing.reason_codes == (TargetingReasonCode.NO_COMPARISON_RULE,)


def test_buyer_relation_detail_preserves_legacy_collapsed_relation_kinds() -> None:
    taxonomy = resolve_buyer_taxonomy_v1()

    assert taxonomy.buyer_relation_detail("FOOD", "FOOD") is BuyerCategoryRelation.EXACT
    assert (
        taxonomy.buyer_relation_detail("FINANCE", "FINANCE_INFORMATION")
        is BuyerCategoryRelation.PARENT_CHILD
    )
    assert taxonomy.buyer_relation_detail("HOME", "DAILY_LIFE") is BuyerCategoryRelation.COMPATIBLE
    assert (
        taxonomy.buyer_relation_detail("FOOD", "TECHNOLOGY") is BuyerCategoryRelation.INCOMPATIBLE
    )
    assert taxonomy.buyer_relation_detail("LEISURE", "DAILY_LIFE") is BuyerCategoryRelation.NO_RULE


@pytest.mark.parametrize(
    ("collection", "creator", "expected"),
    (
        ("美食", ("美食",), BuyerLeadTier.SAME_CATEGORY),
        ("财经", ("财经资讯",), BuyerLeadTier.RELATED),
        ("居家", ("生活",), BuyerLeadTier.RELATED),
        ("美食", ("科技",), BuyerLeadTier.HIGH),
        ("休闲", ("生活",), BuyerLeadTier.CHANGED),
        ("科技", ("科技", "美食"), BuyerLeadTier.CHANGED),
        ("时尚", ("美妆", "财经"), BuyerLeadTier.CHANGED),
        ("居家", ("生活", "美食"), BuyerLeadTier.CHANGED),
    ),
)
def test_buyer_lead_tier_uses_conservative_clean_or_changed_aggregation(
    collection: str,
    creator: tuple[str, ...],
    expected: BuyerLeadTier,
) -> None:
    decision = evaluate_buyer_lead_tier(_policy(), _facts(collection=collection, creator=creator))

    assert decision.tier is expected
    assert decision.relation_summary["status"] == "RELIABLE"
    assert decision.relation_summary["pairs"]


def test_buyer_tri_state_and_unknown_reason_boundaries() -> None:
    equal = evaluate_buyer(_policy(), _facts())
    parent_child = evaluate_buyer(_policy(), _facts(collection="科技", creator=("3C",)))
    compatible = evaluate_buyer(_policy(), _facts(collection="居家", creator=("生活",)))
    incompatible = evaluate_buyer(_policy(), _facts(collection="剧情", creator=("影视",)))
    missing = evaluate_buyer(_policy(), _facts(creator=()))
    unmapped = evaluate_buyer(_policy(), _facts(collection="未映射客户类目"))
    ambiguous = evaluate_buyer(_policy(), _facts(creator=("影视", "剧情"), ambiguous=True))
    no_rule = evaluate_buyer(_policy(), _facts(collection="休闲", creator=("生活",)))

    for result in (equal, parent_child, compatible):
        assert result.result is TargetingEvaluationResult.NOT_MATCH
        assert result.reason_codes == (TargetingReasonCode.CATEGORY_ALIGNED,)
    assert incompatible.result is TargetingEvaluationResult.MATCH
    assert incompatible.reason_codes == (TargetingReasonCode.CATEGORY_MISMATCH,)
    assert missing.reason_codes == (TargetingReasonCode.CREATOR_CLASSIFICATION_MISSING,)
    assert unmapped.reason_codes == (TargetingReasonCode.CLIENT_CATEGORY_UNMAPPED,)
    assert ambiguous.reason_codes == (TargetingReasonCode.AMBIGUOUS_CLASSIFICATION,)
    assert no_rule.reason_codes == (TargetingReasonCode.NO_COMPARISON_RULE,)


def test_arbitrary_or_corrupt_reviewed_taxonomy_is_not_trusted() -> None:
    arbitrary = TaxonomyDefinition(
        taxonomy_version="client-v1", reviewed=True, categories=("FOOD",)
    )
    corrupt = resolve_buyer_taxonomy_v1().model_copy(update={"artifact_hash": "0" * 64})

    assert not is_trusted_buyer_taxonomy_v1(arbitrary)
    assert not is_trusted_buyer_taxonomy_v1(corrupt)
    for taxonomy in (arbitrary, corrupt):
        result = evaluate_buyer(BuyerTargetingPolicy(taxonomy=taxonomy), _facts())
        assert result.result is TargetingEvaluationResult.UNKNOWN
        assert result.reason_codes == (TargetingReasonCode.NO_COMPARISON_RULE,)
        assert (
            evaluate_buyer_lead_tier(BuyerTargetingPolicy(taxonomy=taxonomy), _facts()).tier
            is BuyerLeadTier.UNKNOWN
        )

"""Regression tests for deterministic targeting-evidence redaction."""

from __future__ import annotations

from uuid import UUID, uuid4

from backend_core.growth.targeting import (
    BuyerTargetingPolicy,
    CandidateFactBundle,
    TaxonomyDefinition,
    TaxonomyRelation,
    evaluate_buyer,
)
from backend_core.influencers.enums import Platform


def test_buyer_provenance_uuid_that_looks_like_a_phone_is_not_redacted() -> None:
    """A canonical UUID must not be mistaken for a phone-number evidence value."""

    phone_like_import_job_id = UUID("9ebaf21c-5ecd-4e8b-b6d5-dedc2710197b")
    source_collection_job_id = uuid4()
    facts = CandidateFactBundle(
        influencer_id=uuid4(),
        platform_account_id=uuid4(),
        platform=Platform.XIAOHONGSHU,
        source_collection_job_id=source_collection_job_id,
        source_collection_import_job_ids=(phone_like_import_job_id,),
        creator_classification_import_job_ids=(phone_like_import_job_id,),
        creator_classification_tags=("gaming",),
        collection_industry="beauty",
    )
    policy = BuyerTargetingPolicy(
        taxonomy=TaxonomyDefinition(
            taxonomy_version="reviewed-v1",
            reviewed=True,
            categories=("beauty", "gaming"),
            incompatible=(TaxonomyRelation(left_category_id="beauty", right_category_id="gaming"),),
        )
    )

    evidence = evaluate_buyer(policy, facts).redacted_evidence

    assert evidence["collection_context"]["provenance_import_job_ids"] == [
        str(phone_like_import_job_id)
    ]
    assert evidence["creator_classification"]["provenance_import_job_ids"] == [
        str(phone_like_import_job_id)
    ]

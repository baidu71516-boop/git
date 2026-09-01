"""Server-owned immutable Buyer Taxonomy V1 artifact.

This module is the sole V1 trust root.  It deliberately exposes no authoring,
review, or persistence API: the approved document is frozen in source and a
Buyer policy can be runnable only when its embedded snapshot is exactly this
artifact.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from backend_core.imports.hashing import canonical_value, hash_document

BUYER_TAXONOMY_V1_ID = "buyer-taxonomy-v1"
BUYER_TAXONOMY_V1_VERSION = "1.0.0"
BUYER_TAXONOMY_V1_REVIEW_STATUS = "REVIEWED"
BUYER_TAXONOMY_V1_FROZEN_AT = "2026-09-01"
BUYER_TAXONOMY_V1_FREEZE_SOURCE = "BUSINESS_APPROVAL_BUYER_TAXONOMY_V1_FREEZE"


_NODES: tuple[tuple[str, str], ...] = (
    ("DRAMA", "剧情"),
    ("FILM_TV", "影视"),
    ("FILM_ENTERTAINMENT", "影视娱乐"),
    ("CELEBRITY_GOSSIP", "明星/八卦"),
    ("VARIETY", "综艺"),
    ("COMEDY", "搞笑"),
    ("TECHNOLOGY", "科技"),
    ("DIGITAL_3C", "3C数码"),
    ("BEAUTY", "美妆"),
    ("FASHION", "时尚"),
    ("APPEARANCE", "颜值"),
    ("PARENTING", "亲子"),
    ("FOOD", "美食"),
    ("AUTO", "汽车"),
    ("HOME", "居家"),
    ("DAILY_LIFE", "生活"),
    ("CASUAL_CAPTURE", "随拍"),
    ("EMOTION", "情感"),
    ("WORKPLACE", "职场"),
    ("PHOTOGRAPHY", "摄影"),
    ("GARDENING", "园艺"),
    ("OUTDOOR", "户外"),
    ("PERSONAL_MANAGEMENT", "个人管理"),
    ("LEISURE", "休闲"),
    ("TRAVEL", "旅游"),
    ("FINANCE", "财经"),
    ("FINANCE_INFORMATION", "财经资讯"),
    ("CAMPUS_EDUCATION", "校园教育"),
    ("EDUCATION_TRAINING", "教育培训"),
    ("SCIENCE_POP", "科普"),
    ("GAMING", "游戏"),
    ("ACG", "二次元"),
    ("SPORTS", "体育"),
    ("FITNESS", "健身"),
    ("ART", "艺术"),
    ("MUSIC", "音乐"),
    ("DANCE", "舞蹈"),
    ("PETS", "萌宠"),
    ("AGRICULTURE_RURAL", "三农"),
    ("HEALTH_MEDICAL", "医疗健康"),
    ("CURRENT_AFFAIRS_SOCIAL", "时政社会"),
    ("HUMANITIES_SOCIAL_SCIENCE", "人文社科"),
    ("PRODUCT_REVIEW", "评测种草"),
    ("SHORT_DRAMA", "短剧"),
    ("AI_SHORT_DRAMA", "AI短剧"),
    ("ENTERTAINMENT", "娱乐"),
    ("VLOG", "VLOG"),
    ("ENTERTAINMENT_MARKETING", "娱乐营销"),
)

_ALIASES: tuple[tuple[str, str], ...] = (
    ("居家", "HOME"),
    ("家居", "HOME"),
    ("影视", "FILM_TV"),
    ("美食", "FOOD"),
    ("亲子", "PARENTING"),
    ("母婴亲子", "PARENTING"),
    ("萌娃", "PARENTING"),
    ("母婴", "PARENTING"),
    ("随拍", "CASUAL_CAPTURE"),
    ("时尚", "FASHION"),
    ("穿搭", "FASHION"),
    ("汽车", "AUTO"),
    ("舞蹈", "DANCE"),
    ("个人管理", "PERSONAL_MANAGEMENT"),
    ("旅游", "TRAVEL"),
    ("旅行", "TRAVEL"),
    ("医疗健康", "HEALTH_MEDICAL"),
    ("健康", "HEALTH_MEDICAL"),
    ("科技", "TECHNOLOGY"),
    ("时政社会", "CURRENT_AFFAIRS_SOCIAL"),
    ("政务", "CURRENT_AFFAIRS_SOCIAL"),
    ("人文社科", "HUMANITIES_SOCIAL_SCIENCE"),
    ("文学", "HUMANITIES_SOCIAL_SCIENCE"),
    ("三农", "AGRICULTURE_RURAL"),
    ("健身", "FITNESS"),
    ("萌宠", "PETS"),
    ("二次元", "ACG"),
    ("动漫", "ACG"),
    ("校园教育", "CAMPUS_EDUCATION"),
    ("剧情演绎", "DRAMA"),
    ("剧情", "DRAMA"),
    ("休闲", "LEISURE"),
    ("财经", "FINANCE"),
    ("财经资讯", "FINANCE_INFORMATION"),
    ("体育", "SPORTS"),
    ("体育运动", "SPORTS"),
    ("音乐", "MUSIC"),
    ("明星/八卦", "CELEBRITY_GOSSIP"),
    ("明星", "CELEBRITY_GOSSIP"),
    ("颜值", "APPEARANCE"),
    ("美女", "APPEARANCE"),
    ("帅哥", "APPEARANCE"),
    ("综艺", "VARIETY"),
    ("游戏", "GAMING"),
    ("科普", "SCIENCE_POP"),
    ("生活", "DAILY_LIFE"),
    ("生活Vlog", "VLOG"),
    ("影视娱乐", "FILM_ENTERTAINMENT"),
    ("娱乐", "ENTERTAINMENT"),
    ("娱乐营销", "ENTERTAINMENT_MARKETING"),
    ("教育培训", "EDUCATION_TRAINING"),
    ("艺术", "ART"),
    ("才艺", "ART"),
    ("美妆", "BEAUTY"),
    ("评测种草", "PRODUCT_REVIEW"),
    ("情感", "EMOTION"),
    ("职场", "WORKPLACE"),
    ("搞笑", "COMEDY"),
    ("户外", "OUTDOOR"),
    ("摄影", "PHOTOGRAPHY"),
    ("园艺", "GARDENING"),
    ("3C", "DIGITAL_3C"),
    ("数码", "DIGITAL_3C"),
)

_PARENT_CHILD: tuple[tuple[str, str], ...] = (
    ("DRAMA", "SHORT_DRAMA"),
    ("SHORT_DRAMA", "AI_SHORT_DRAMA"),
    ("TECHNOLOGY", "DIGITAL_3C"),
    ("CAMPUS_EDUCATION", "EDUCATION_TRAINING"),
    ("SPORTS", "FITNESS"),
    ("DAILY_LIFE", "CASUAL_CAPTURE"),
    ("VARIETY", "ENTERTAINMENT"),
    ("FINANCE", "FINANCE_INFORMATION"),
)

_COMPATIBLE: tuple[tuple[str, str], ...] = (
    ("DAILY_LIFE", "VLOG"),
    ("FASHION", "APPEARANCE"),
    ("FASHION", "BEAUTY"),
    ("CELEBRITY_GOSSIP", "ENTERTAINMENT"),
    ("ART", "MUSIC"),
    ("ART", "DANCE"),
    ("FILM_ENTERTAINMENT", "FILM_TV"),
    ("FILM_ENTERTAINMENT", "ENTERTAINMENT"),
    ("HOME", "DAILY_LIFE"),
)

_INCOMPATIBLE: tuple[tuple[str, str], ...] = (
    ("DRAMA", "FILM_TV"),
    ("TECHNOLOGY", "FOOD"),
    ("TECHNOLOGY", "BEAUTY"),
    ("TECHNOLOGY", "FASHION"),
    ("TECHNOLOGY", "APPEARANCE"),
    ("TECHNOLOGY", "PARENTING"),
    ("DIGITAL_3C", "FOOD"),
    ("DIGITAL_3C", "BEAUTY"),
    ("DIGITAL_3C", "FASHION"),
    ("DIGITAL_3C", "APPEARANCE"),
    ("DIGITAL_3C", "PARENTING"),
    ("AUTO", "FOOD"),
    ("AUTO", "BEAUTY"),
    ("AUTO", "FASHION"),
    ("AUTO", "APPEARANCE"),
    ("AUTO", "PARENTING"),
    ("PARENTING", "FINANCE"),
    ("PARENTING", "FINANCE_INFORMATION"),
    ("GAMING", "HEALTH_MEDICAL"),
    ("ACG", "HEALTH_MEDICAL"),
    ("FOOD", "FINANCE"),
    ("FOOD", "FINANCE_INFORMATION"),
    ("FOOD", "CURRENT_AFFAIRS_SOCIAL"),
    ("BEAUTY", "FINANCE"),
    ("BEAUTY", "FINANCE_INFORMATION"),
    ("FASHION", "FINANCE"),
    ("FASHION", "FINANCE_INFORMATION"),
    ("APPEARANCE", "FINANCE"),
    ("APPEARANCE", "FINANCE_INFORMATION"),
    ("BEAUTY", "CURRENT_AFFAIRS_SOCIAL"),
    ("FASHION", "CURRENT_AFFAIRS_SOCIAL"),
    ("APPEARANCE", "CURRENT_AFFAIRS_SOCIAL"),
)


def _pairs(values: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
    return [
        {"left_category_id": left, "right_category_id": right}
        for left, right in sorted(tuple(sorted(pair)) for pair in values)
    ]


def buyer_taxonomy_v1_document() -> Mapping[str, Any]:
    """Return an immutable canonical artifact document suitable for hashing."""

    document = {
        "taxonomy_id": BUYER_TAXONOMY_V1_ID,
        "taxonomy_version": BUYER_TAXONOMY_V1_VERSION,
        "review_status": BUYER_TAXONOMY_V1_REVIEW_STATUS,
        "freeze_metadata": {
            "frozen_at": BUYER_TAXONOMY_V1_FROZEN_AT,
            "source": BUYER_TAXONOMY_V1_FREEZE_SOURCE,
        },
        "nodes": [
            {"id": category_id, "display_name": display_name}
            for category_id, display_name in _NODES
        ],
        "aliases": [
            {"label": label, "category_id": category_id} for label, category_id in sorted(_ALIASES)
        ],
        "parent_child": _pairs(_PARENT_CHILD),
        "compatible": _pairs(_COMPATIBLE),
        "incompatible": _pairs(_INCOMPATIBLE),
    }
    return MappingProxyType(canonical_value(document))


BUYER_TAXONOMY_V1_HASH = hash_document(buyer_taxonomy_v1_document())


def resolve_buyer_taxonomy_v1() -> Any:
    """Return the exact frozen policy snapshot; import lazily to avoid a cycle."""

    from backend_core.growth.targeting import TaxonomyAlias, TaxonomyDefinition, TaxonomyRelation

    return TaxonomyDefinition(
        taxonomy_id=BUYER_TAXONOMY_V1_ID,
        taxonomy_version=BUYER_TAXONOMY_V1_VERSION,
        artifact_hash=BUYER_TAXONOMY_V1_HASH,
        review_status=BUYER_TAXONOMY_V1_REVIEW_STATUS,
        reviewed=True,
        categories=tuple(category_id for category_id, _display_name in _NODES),
        aliases=tuple(
            TaxonomyAlias(label=label, category_id=category_id) for label, category_id in _ALIASES
        ),
        parent_child=tuple(
            TaxonomyRelation(left_category_id=left, right_category_id=right)
            for left, right in _PARENT_CHILD
        ),
        compatible=tuple(
            TaxonomyRelation(left_category_id=left, right_category_id=right)
            for left, right in _COMPATIBLE
        ),
        incompatible=tuple(
            TaxonomyRelation(left_category_id=left, right_category_id=right)
            for left, right in _INCOMPATIBLE
        ),
    )


def is_trusted_buyer_taxonomy_v1(taxonomy: object) -> bool:
    """Require byte-for-byte semantic equality with the one approved artifact."""

    model_dump = getattr(taxonomy, "model_dump", None)
    if not callable(model_dump):
        return False
    try:
        return model_dump(mode="json") == resolve_buyer_taxonomy_v1().model_dump(mode="json")
    except (TypeError, ValueError):
        return False


__all__ = [
    "BUYER_TAXONOMY_V1_FREEZE_SOURCE",
    "BUYER_TAXONOMY_V1_FROZEN_AT",
    "BUYER_TAXONOMY_V1_HASH",
    "BUYER_TAXONOMY_V1_ID",
    "BUYER_TAXONOMY_V1_REVIEW_STATUS",
    "BUYER_TAXONOMY_V1_VERSION",
    "buyer_taxonomy_v1_document",
    "is_trusted_buyer_taxonomy_v1",
    "resolve_buyer_taxonomy_v1",
]

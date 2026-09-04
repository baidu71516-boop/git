"""Central source-to-canonical field mappings for import adapters."""

from collections.abc import Mapping
from types import MappingProxyType

from backend_core.imports.errors import ImportDomainError

HUITUN_XHS_FIELD_MAPPING: Mapping[str, str] = MappingProxyType(
    {
        "达人名称": "nickname",
        "达人官方地址": "profile_url",
        "小红书号": "account_handle",
        "灰豚指数": "huitun_score",
        "性别": "gender",
        "地域": "region_raw",
        "简介": "bio",
        "联系邮箱": "email",
        "更新时间": "source_updated_at",
        "认证信息": "verification_info",
        "粉丝数": "followers_count",
        "品牌合作人": "is_brand_partner",
        "签约MCN": "mcn_name",
        "达人标签": "creator_tags",
        "认证类型": "creator_level",
        "笔记总数": "notes_count",
        "赞藏总数": "likes_collects_total",
        "商业笔记总数": "commercial_notes_count",
        "近7天笔记数": "notes_7d",
        "近60天笔记数": "notes_60d",
        "近60天爆文率": "viral_rate_60d",
        "近60天平均点赞": "avg_likes_60d",
        "近60天平均收藏": "avg_collects_60d",
        "近60天平均评论": "avg_comments_60d",
        "近60天平均分享": "avg_shares_60d",
        "活跃粉丝占比": "active_fans_raw",
        "水粉占比": "suspicious_fans_raw",
        "粉丝男/女": "fan_gender_raw",
        "粉丝地域": "fan_region_raw",
        "粉丝年龄": "fan_age_raw",
        "粉丝活跃时间": "fan_active_time_raw",
        "粉丝关注焦点": "fan_interests_raw",
        "图文笔记报价": "image_note_price",
        "图文CPE": "image_cpe",
        "图文CPM": "image_cpm",
        "视频笔记报价": "video_note_price",
        "视频CPE": "video_cpe",
        "视频CPM": "video_cpm",
    }
)

# Backwards-compatible name for the established Xiaohongshu export.
HUITUN_FIELD_MAPPING = HUITUN_XHS_FIELD_MAPPING

# Douyin V1 deliberately imports only fields with a settled canonical meaning.
# The profile URL is the sole hard identity input; ``抖音号`` is display data.
HUITUN_DOUYIN_FIELD_MAPPING: Mapping[str, str] = MappingProxyType(
    {
        "播主昵称": "nickname",
        "抖音号": "account_handle",
        "所属MCN": "mcn_name",
        "简介": "bio",
        "内容标签": "creator_tags",
        "分类": "creator_classification_tags",
        "粉丝数": "followers_count",
        "作品数": "works_count",
        "点赞数": "likes_count",
        "平均点赞": "avg_likes",
        "达人主页链接": "profile_url",
    }
)
HUITUN_DOUYIN_REQUIRED_HEADERS = frozenset({"播主昵称", "抖音号", "达人主页链接"})

CANONICAL_FIELDS = frozenset(
    {
        *HUITUN_FIELD_MAPPING.values(),
        *HUITUN_DOUYIN_FIELD_MAPPING.values(),
        "creator_classification_tags",
        "external_source_id",
        "platform_account_id",
    }
)

HUITUN_INTEGER_FIELDS = frozenset(
    {
        "followers_count",
        "works_count",
        "likes_count",
        "notes_count",
        "likes_collects_total",
        "commercial_notes_count",
        "notes_7d",
        "notes_60d",
        "avg_likes_60d",
        "avg_collects_60d",
        "avg_comments_60d",
        "avg_shares_60d",
    }
)
HUITUN_DECIMAL_FIELDS = frozenset(
    {
        "avg_likes",
        "huitun_score",
        "image_note_price",
        "image_cpe",
        "image_cpm",
        "video_note_price",
        "video_cpe",
        "video_cpm",
    }
)
HUITUN_PERCENT_FIELDS = frozenset({"viral_rate_60d"})
HUITUN_RAW_COMPOSITE_FIELDS = frozenset(
    {
        "active_fans_raw",
        "suspicious_fans_raw",
        "fan_gender_raw",
        "fan_region_raw",
        "fan_age_raw",
        "fan_active_time_raw",
        "fan_interests_raw",
    }
)


def validate_mapping(
    headers: list[str],
    mapping: Mapping[str, str],
    *,
    require_display_name: bool = True,
    require_identity: bool = True,
) -> dict[str, str]:
    """Validate an explicit source-to-canonical mapping."""

    header_set = set(headers)
    unknown_sources = sorted(set(mapping) - header_set)
    if unknown_sources:
        raise ImportDomainError(
            "MAPPING_INVALID",
            "Mapping refers to fields that are not present in the file",
            details={"unknown_source_fields": unknown_sources},
        )

    unknown_targets = sorted(set(mapping.values()) - CANONICAL_FIELDS)
    if unknown_targets:
        raise ImportDomainError(
            "MAPPING_INVALID",
            "Mapping contains unsupported canonical fields",
            details={"unknown_canonical_fields": unknown_targets},
        )

    targets = list(mapping.values())
    duplicate_targets = sorted({target for target in targets if targets.count(target) > 1})
    if duplicate_targets:
        raise ImportDomainError(
            "MAPPING_INVALID",
            "Multiple source fields cannot map to one canonical field",
            details={"duplicate_canonical_fields": duplicate_targets},
        )

    target_set = set(targets)
    if require_display_name and "nickname" not in target_set:
        raise ImportDomainError("MAPPING_INVALID", "Mapping must include nickname")
    if (
        require_identity
        and not {
            "external_source_id",
            "platform_account_id",
            "profile_url",
        }
        & target_set
    ):
        raise ImportDomainError(
            "MAPPING_INVALID",
            "Mapping must include a platform, source, or profile identity",
        )
    return dict(mapping)


def huitun_mapping_for_headers(headers: list[str]) -> dict[str, str]:
    mapping = {
        field: canonical for field, canonical in HUITUN_FIELD_MAPPING.items() if field in headers
    }
    return validate_mapping(headers, mapping)


def huitun_douyin_mapping_for_headers(headers: list[str]) -> dict[str, str]:
    missing = sorted(HUITUN_DOUYIN_REQUIRED_HEADERS - set(headers))
    if missing:
        raise ImportDomainError(
            "MAPPING_INVALID",
            "Huitun Douyin export is missing required identity headers",
            details={"missing_source_fields": missing},
        )
    mapping = {
        field: canonical
        for field, canonical in HUITUN_DOUYIN_FIELD_MAPPING.items()
        if field in headers
    }
    return validate_mapping(headers, mapping)


__all__ = [
    "CANONICAL_FIELDS",
    "HUITUN_DECIMAL_FIELDS",
    "HUITUN_DOUYIN_FIELD_MAPPING",
    "HUITUN_DOUYIN_REQUIRED_HEADERS",
    "HUITUN_FIELD_MAPPING",
    "HUITUN_INTEGER_FIELDS",
    "HUITUN_PERCENT_FIELDS",
    "HUITUN_RAW_COMPOSITE_FIELDS",
    "HUITUN_XHS_FIELD_MAPPING",
    "huitun_douyin_mapping_for_headers",
    "huitun_mapping_for_headers",
    "validate_mapping",
]

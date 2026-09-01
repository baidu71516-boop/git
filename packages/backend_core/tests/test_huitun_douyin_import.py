from decimal import Decimal

import pytest
from backend_core.imports.adapters import HuitunCsvAdapter, HuitunExcelAdapter
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.normalizers import normalize_douyin_profile_url
from backend_core.imports.parsers import RawTabularRecord
from backend_core.influencers.enums import DataSource, Platform

DOUYIN_HEADERS = [
    "播主昵称",
    "抖音号",
    "分类",
    "所属MCN",
    "企业认证信息",
    "个人认证信息",
    "简介",
    "省份",
    "城市",
    "内容标签",
    "粉丝数",
    "作品数",
    "点赞数",
    "达人主页链接",
    "带货类目",
]


def douyin_row(**overrides: str) -> RawTabularRecord:
    values = {header: "--" for header in DOUYIN_HEADERS}
    values.update(
        {
            "播主昵称": "脱敏抖音达人",
            "抖音号": "cctv.com",
            "所属MCN": "脱敏MCN",
            "企业认证信息": "企业认证原文",
            "个人认证信息": "个人认证原文",
            "简介": "来源简介",
            "省份": "上海",
            "城市": "上海",
            "内容标签": "美食,探店",
            "粉丝数": "12,345",
            "作品数": "77",
            "点赞数": "88,999",
            "达人主页链接": "http://www.douyin.com/user/profile-token/?source=test#bio",
            "分类": "美食",
            "带货类目": "食品饮料",
        }
    )
    values.update(overrides)
    return RawTabularRecord(row_number=2, raw_data=dict(values), values=values)


@pytest.mark.parametrize("handle", ["123456", "dongfangzhenxuan"])
def test_huitun_douyin_adapter_uses_profile_token_not_handle(handle: str) -> None:
    adapter = HuitunCsvAdapter()
    mapping = adapter.mapping_for_headers(DOUYIN_HEADERS)
    assert mapping["抖音号"] == "account_handle"
    assert mapping["达人主页链接"] == "profile_url"
    adapter.validate_table([douyin_row(**{"抖音号": handle})])

    adapted = adapter.adapt(douyin_row(**{"抖音号": handle}))

    assert adapted.is_valid
    assert adapted.record.platform_identity.platform is Platform.DOUYIN
    assert adapted.record.platform_identity.platform_account_id == "profile-token"
    assert adapted.record.platform_identity.account_handle == handle
    assert (
        adapted.record.platform_identity.normalized_profile_url
        == "https://www.douyin.com/user/profile-token"
    )
    assert adapted.record.source is DataSource.HUITUN
    assert adapted.record.public_profile["region_raw"] == "省份: 上海；城市: 上海"
    assert "企业认证信息: 企业认证原文" in adapted.record.public_profile["verification_info"]
    assert adapted.record.public_profile["creator_tags"] == ["美食", "探店"]
    assert adapted.record.metrics == {"followers_count": 12345}
    assert "notes_count" not in adapted.record.metrics
    assert "likes_collects_total" not in adapted.record.metrics
    assert adapted.raw_data["分类"] == "美食"
    assert adapted.raw_data["带货类目"] == "食品饮料"


def test_huitun_douyin_blank_optional_metric_is_not_zero() -> None:
    adapter = HuitunExcelAdapter()
    adapter.mapping_for_headers(DOUYIN_HEADERS)
    adapted = adapter.adapt(douyin_row(**{"粉丝数": "--"}))
    assert adapted.is_valid
    assert adapted.record.metrics == {}


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/user/profile-token",
        "https://www.douyin.com/other/profile-token",
        "https://www.douyin.com/user/",
        "javascript:alert(1)",
    ],
)
def test_douyin_profile_normalization_rejects_non_identity_urls(value: str) -> None:
    assert normalize_douyin_profile_url(value) is None


def test_huitun_douyin_missing_or_invalid_profile_never_falls_back_to_handle() -> None:
    adapter = HuitunCsvAdapter()
    adapter.mapping_for_headers(DOUYIN_HEADERS)

    adapted = adapter.adapt(
        douyin_row(**{"达人主页链接": "https://www.xiaohongshu.com/user/profile/not-douyin"})
    )

    assert not adapted.is_valid
    assert adapted.record.platform_identity.platform_account_id is None
    assert adapted.record.platform_identity.account_handle == "cctv.com"
    assert {issue.code for issue in adapted.errors} >= {
        "INVALID_PROFILE_URL",
        "MISSING_PLATFORM_IDENTITY",
    }


def test_huitun_platform_detection_rejects_ambiguous_headers_and_mixed_rows() -> None:
    with pytest.raises(ImportDomainError, match="both Douyin and Xiaohongshu"):
        HuitunCsvAdapter().mapping_for_headers(DOUYIN_HEADERS + ["小红书号"])

    adapter = HuitunCsvAdapter()
    adapter.mapping_for_headers(DOUYIN_HEADERS)
    xhs_row = douyin_row(**{"达人主页链接": "https://www.xiaohongshu.com/user/profile/xhs-profile"})
    with pytest.raises(ImportDomainError, match="mixes Douyin and Xiaohongshu"):
        adapter.validate_table([douyin_row(), xhs_row])


def test_huitun_douyin_mapping_is_fixed_and_cannot_substitute_handle_identity() -> None:
    with pytest.raises(ImportDomainError, match="mapping is fixed"):
        HuitunCsvAdapter(
            {"播主昵称": "nickname", "抖音号": "platform_account_id"}
        ).mapping_for_headers(DOUYIN_HEADERS)


def test_douyin_followers_is_a_platform_neutral_integer() -> None:
    adapter = HuitunCsvAdapter()
    adapter.mapping_for_headers(DOUYIN_HEADERS)
    adapted = adapter.adapt(douyin_row(**{"粉丝数": "456"}))
    assert adapted.record.metrics["followers_count"] == 456
    assert not isinstance(adapted.record.metrics["followers_count"], Decimal)

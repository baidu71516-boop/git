from datetime import UTC, datetime
from decimal import Decimal

import pytest
from backend_core.imports.adapters import GenericCsvAdapter, HuitunCsvAdapter, HuitunExcelAdapter
from backend_core.imports.contracts import CanonicalInfluencerRecord
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.mappings import HUITUN_FIELD_MAPPING, validate_mapping
from backend_core.imports.normalizers import (
    normalize_email,
    normalize_xhs_profile_url,
    parse_decimal,
    parse_labeled_percentages,
    parse_percent,
)
from backend_core.imports.parsers import RawTabularRecord
from backend_core.influencers.enums import ContactType, DataSource, Platform


def huitun_row(**overrides: str) -> RawTabularRecord:
    values = {header: "--" for header in HUITUN_FIELD_MAPPING}
    values.update(
        {
            "达人名称": "脱敏达人甲",
            "达人官方地址": (" https://WWW.XIAOHONGSHU.COM/user/profile/fixtureA/?x=1#section "),
            "小红书号": "fixture-handle",
            "灰豚指数": "321.45",
            "联系邮箱": "Creator@EXAMPLE.COM",
            "更新时间": "2026-08-10 12:34:56",
            "粉丝数": "123,456",
            "品牌合作人": "是",
            "达人标签": "[动画]",
            "近60天爆文率": "12.50%",
            "活跃粉丝占比": "42.64% | 192745",
            "水粉占比": "1.5% | 1200",
            "粉丝男/女": "13.6% | 86.4%",
            "粉丝地域": "上海|17.4% 北京|15.2%",
            "粉丝年龄": "25-34岁|39.1% 18-24岁|30.2%",
            "粉丝活跃时间": "20:00|34.5% 工作日|19.5%",
            "粉丝关注焦点": "二次元|17.4% 生活记录|15.2%",
            "图文笔记报价": "8000",
            "视频CPE": "3.14",
        }
    )
    values.update(overrides)
    return RawTabularRecord(row_number=2, raw_data=dict(values), values=values)


def test_huitun_mapping_is_complete_centralized_and_shared_by_file_types() -> None:
    headers = list(HUITUN_FIELD_MAPPING)
    assert len(headers) == 38
    assert HUITUN_FIELD_MAPPING["达人官方地址"] == "profile_url"
    assert HUITUN_FIELD_MAPPING["灰豚指数"] == "huitun_score"
    assert HUITUN_FIELD_MAPPING["小红书号"] == "account_handle"
    assert HUITUN_FIELD_MAPPING["联系邮箱"] == "email"
    assert HUITUN_FIELD_MAPPING["近7天笔记数"] == "notes_7d"

    csv_mapping = HuitunCsvAdapter().mapping_for_headers(headers)
    excel_mapping = HuitunExcelAdapter().mapping_for_headers(headers)
    assert csv_mapping == excel_mapping == dict(HUITUN_FIELD_MAPPING)


@pytest.mark.parametrize("raw_value, expected", [("0", 0), ("1", 1), ("2", 2), ("3", 3)])
def test_huitun_adapter_maps_notes_7d_as_a_nonnegative_integer(
    raw_value: str, expected: int
) -> None:
    raw = huitun_row(**{"近7天笔记数": raw_value})
    adapter = HuitunCsvAdapter()
    adapter.mapping_for_headers(list(raw.values))

    adapted = adapter.adapt(raw)

    assert adapted.is_valid
    assert adapted.record.metrics["notes_7d"] == expected


def test_huitun_adapter_keeps_missing_notes_7d_missing() -> None:
    raw = huitun_row(**{"近7天笔记数": "--"})
    adapter = HuitunCsvAdapter()
    adapter.mapping_for_headers(list(raw.values))

    adapted = adapter.adapt(raw)

    assert adapted.is_valid
    assert "notes_7d" not in adapted.record.metrics


def test_huitun_adapter_rejects_negative_notes_7d() -> None:
    raw = huitun_row(**{"近7天笔记数": "-1"})
    adapter = HuitunCsvAdapter()
    adapter.mapping_for_headers(list(raw.values))

    adapted = adapter.adapt(raw)

    assert adapted.is_valid
    assert "notes_7d" not in adapted.record.metrics
    assert any(
        warning.code == "INVALID_INTEGER" and warning.field == "notes_7d"
        for warning in adapted.warnings
    )


def test_mapping_requires_profile_or_another_stable_identity_not_handle_or_email() -> None:
    assert validate_mapping(
        ["name", "official_profile", "handle", "email"],
        {
            "name": "nickname",
            "official_profile": "profile_url",
            "handle": "account_handle",
            "email": "email",
        },
    ) == {
        "name": "nickname",
        "official_profile": "profile_url",
        "handle": "account_handle",
        "email": "email",
    }

    with pytest.raises(ImportDomainError) as missing_stable_identity:
        validate_mapping(
            ["name", "handle", "email"],
            {"name": "nickname", "handle": "account_handle", "email": "email"},
        )

    assert missing_stable_identity.value.code == "MAPPING_INVALID"
    assert missing_stable_identity.value.message == (
        "Mapping must include a platform, source, or profile identity"
    )


def test_huitun_adapter_builds_platform_neutral_canonical_record() -> None:
    raw = huitun_row()
    adapter = HuitunCsvAdapter()
    adapter.mapping_for_headers(list(raw.values))

    adapted = adapter.adapt(raw)

    assert adapted.is_valid
    assert adapted.raw_data == raw.raw_data
    assert adapted.record.source is DataSource.HUITUN
    assert adapted.record.platform_identity.platform is Platform.XIAOHONGSHU
    assert adapted.record.platform_identity.platform_account_id == "fixtureA"
    assert (
        adapted.record.platform_identity.normalized_profile_url
        == "https://www.xiaohongshu.com/user/profile/fixtureA"
    )
    assert adapted.record.platform_identity.account_handle == "fixture-handle"
    assert adapted.record.source_updated_at == datetime(2026, 8, 10, 4, 34, 56, tzinfo=UTC)
    assert adapted.record.public_profile["is_brand_partner"] is True
    assert adapted.record.public_profile["creator_tags"] == ["动画"]
    assert adapted.record.metrics["followers_count"] == 123456
    assert adapted.record.metrics["huitun_score"] == Decimal("321.45")
    assert adapted.record.metrics["viral_rate_60d"] == Decimal("0.125")
    assert adapted.record.metrics["active_fans_rate"] == Decimal("0.4264")
    assert adapted.record.metrics["active_fans_count"] == 192745
    assert adapted.record.metrics["fan_male_rate"] == Decimal("0.136")
    assert adapted.record.metrics["fan_female_rate"] == Decimal("0.864")
    assert adapted.record.metrics["fan_region_distribution"] == [
        {"label": "上海", "rate": Decimal("0.174")},
        {"label": "北京", "rate": Decimal("0.152")},
    ]
    assert adapted.record.contacts[0].type is ContactType.EMAIL
    assert adapted.record.contacts[0].normalized_value == "Creator@example.com"
    assert adapted.normalized_data()["metrics"]["huitun_score"] == "321.45"
    assert adapted.normalized_data()["source_updated_at"] == "2026-08-10T04:34:56Z"
    reloaded = CanonicalInfluencerRecord.model_validate(adapted.normalized_data())
    assert reloaded.platform_identity.platform_account_id == "fixtureA"
    assert reloaded.source_updated_at == adapted.record.source_updated_at


def test_null_markers_do_not_become_values_or_warnings() -> None:
    raw = huitun_row(**{"联系邮箱": " NULL ", "简介": "  -- ", "粉丝男/女": " -- "})
    adapter = HuitunCsvAdapter()
    adapter.mapping_for_headers(list(raw.values))

    adapted = adapter.adapt(raw)

    assert adapted.is_valid
    assert adapted.record.contacts == ()
    assert "bio" not in adapted.record.public_profile
    assert "fan_gender_raw" not in adapted.record.metrics
    assert not any(issue.code == "INVALID_EMAIL" for issue in adapted.warnings)


def test_invalid_email_is_warning_and_is_never_repaired_or_used_as_identity() -> None:
    first = huitun_row(**{"联系邮箱": "missing-at.example.com"})
    second = huitun_row(
        **{
            "达人名称": "脱敏达人乙",
            "达人官方地址": "https://www.xiaohongshu.com/user/profile/fixtureB",
            "联系邮箱": "missing-at.example.com",
        }
    )
    adapter = HuitunCsvAdapter()
    adapter.mapping_for_headers(list(first.values))

    adapted_first = adapter.adapt(first)
    adapted_second = adapter.adapt(second)

    assert adapted_first.record.contacts == adapted_second.record.contacts == ()
    assert adapted_first.record.platform_identity.platform_account_id == "fixtureA"
    assert adapted_second.record.platform_identity.platform_account_id == "fixtureB"
    assert any(issue.code == "INVALID_EMAIL" for issue in adapted_first.warnings)
    assert "@" not in adapted_first.normalized_data()["platform_identity"]["platform_account_id"]


def test_profile_and_email_normalization_are_strict_and_non_guessing() -> None:
    profile = normalize_xhs_profile_url(
        "https://XIAOHONGSHU.com/user/profile/AbC_123/?utm_source=test#bio"
    )
    assert profile is not None
    assert profile.platform_account_id == "AbC_123"
    assert profile.normalized_profile_url.endswith("/AbC_123")
    assert normalize_xhs_profile_url("https://example.com/user/profile/AbC_123") is None
    assert normalize_xhs_profile_url("javascript:alert(1)") is None

    email = normalize_email(" Local.Part@EXAMPLE.COM ")
    assert email is not None and email.is_valid
    assert email.normalized_value == "Local.Part@example.com"
    invalid = normalize_email("local.example.com")
    assert invalid is not None and not invalid.is_valid and invalid.normalized_value is None


def test_numeric_and_distribution_parsing_is_strict() -> None:
    assert parse_decimal("1,234.50") == Decimal("1234.50")
    assert str(parse_decimal("001.2300")) == "1.23"
    assert parse_decimal("12,34") is None
    assert parse_percent("100.01%") is None
    assert parse_labeled_percentages("广东22.3%") == [{"label": "广东", "rate": Decimal("0.223")}]


def test_optional_parse_failures_are_row_warnings_and_keep_raw_values() -> None:
    raw = huitun_row(
        **{
            "更新时间": "not-a-time",
            "粉丝数": "many",
            "近60天爆文率": "twelve percent",
            "活跃粉丝占比": "unknown composition",
        }
    )
    adapter = HuitunCsvAdapter()
    adapter.mapping_for_headers(list(raw.values))

    adapted = adapter.adapt(raw)

    assert adapted.is_valid
    assert adapted.raw_data["活跃粉丝占比"] == "unknown composition"
    assert adapted.record.metrics["active_fans_raw"] == "unknown composition"
    assert adapted.record.source_updated_at is None
    assert {warning.code for warning in adapted.warnings} >= {
        "INVALID_SOURCE_TIME",
        "INVALID_INTEGER",
        "INVALID_PERCENT",
        "INVALID_COMPOSITE_VALUE",
    }


def test_identity_failures_are_row_local_errors() -> None:
    raw = huitun_row(**{"达人官方地址": "https://example.com/not-xhs"})
    adapter = HuitunCsvAdapter()
    adapter.mapping_for_headers(list(raw.values))

    adapted = adapter.adapt(raw)

    assert not adapted.is_valid
    assert {error.code for error in adapted.errors} == {
        "INVALID_PROFILE_URL",
        "MISSING_PLATFORM_IDENTITY",
    }


def test_generic_adapter_requires_and_uses_only_explicit_mapping() -> None:
    with pytest.raises(ImportDomainError, match="explicit mapping"):
        GenericCsvAdapter({})

    raw = RawTabularRecord(
        row_number=2,
        raw_data={"name": "脱敏达人", "id": "generic-id", "mail": "same@mcn.example"},
        values={"name": "脱敏达人", "id": "generic-id", "mail": "same@mcn.example"},
    )
    adapter = GenericCsvAdapter({"name": "nickname", "id": "platform_account_id", "mail": "email"})
    assert adapter.mapping_for_headers(list(raw.values)) == {
        "name": "nickname",
        "id": "platform_account_id",
        "mail": "email",
    }
    adapted = adapter.adapt(raw)
    assert adapted.is_valid
    assert adapted.record.platform_identity.platform_account_id == "generic-id"
    assert adapted.record.contacts[0].normalized_value == "same@mcn.example"

    source_only = RawTabularRecord(
        row_number=3,
        raw_data={"name": "已有达人", "external": "provider-stable-id"},
        values={"name": "已有达人", "external": "provider-stable-id"},
    )
    source_adapter = GenericCsvAdapter({"name": "nickname", "external": "external_source_id"})
    source_adapted = source_adapter.adapt(source_only)
    assert source_adapted.is_valid
    assert source_adapted.record.platform_identity.external_source_id == "provider-stable-id"


def test_mapping_rejects_unknown_and_duplicate_canonical_targets() -> None:
    with pytest.raises(ImportDomainError) as unknown:
        validate_mapping(["name", "id"], {"name": "nickname", "missing": "profile_url"})
    assert unknown.value.code == "MAPPING_INVALID"

    with pytest.raises(ImportDomainError) as duplicate:
        validate_mapping(
            ["name", "alias", "url"],
            {"name": "nickname", "alias": "nickname", "url": "profile_url"},
        )
    assert duplicate.value.code == "MAPPING_INVALID"

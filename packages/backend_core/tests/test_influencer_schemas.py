from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from backend_core.auth.enums import OperatorStatus
from backend_core.influencers.enums import (
    ContactFilter,
    ContactType,
    ContactValidationStatus,
    ContentActivityFilter,
    CRMStage,
    DataSource,
    InfluencerStatus,
    Notes7dFilter,
    Notes60dFilter,
    Platform,
)
from backend_core.influencers.freshness import FreshnessStatus
from backend_core.influencers.schemas import (
    CurrentContactSummary,
    CurrentMetricsDetail,
    CurrentMetricsSummary,
    InfluencerContactDetail,
    InfluencerDetail,
    InfluencerFilterOptions,
    InfluencerListItem,
    InfluencerListPage,
    InfluencerListQuery,
    MetricSnapshotItem,
    MetricSnapshotPage,
    OwnerSummary,
    PlatformAccountDetail,
    PlatformAccountSummary,
    SourceIdentityDetail,
    SourceStateDetail,
)
from pydantic import ValidationError


def test_list_query_defaults_and_exact_field_set() -> None:
    query = InfluencerListQuery()

    assert query.page == 1
    assert query.page_size == 50
    assert set(InfluencerListQuery.model_fields) == {
        "q",
        "tag",
        "followers_min",
        "followers_max",
        "owner_operator_id",
        "crm_stage",
        "contact_filter",
        "notes_7d_filter",
        "notes_60d_filter",
        "content_activity_filter",
        "freshness_status",
        "requires_refresh",
        "last_huitun_observed_before",
        "last_huitun_observed_after",
        "page",
        "page_size",
    }

    with pytest.raises(ValidationError):
        InfluencerListQuery.model_validate({"platform": "xiaohongshu"})


def test_list_query_trims_q_and_treats_blank_q_as_missing() -> None:
    assert InfluencerListQuery(q="  示例达人  ").q == "示例达人"
    assert InfluencerListQuery(q=" \t\n ").q is None
    assert len(InfluencerListQuery(q="名" * 160).q or "") == 160

    with pytest.raises(ValidationError):
        InfluencerListQuery(q="名" * 161)


def test_list_query_trims_tag_without_changing_its_original_case() -> None:
    assert InfluencerListQuery(tag="").tag is None
    assert InfluencerListQuery(tag="   ").tag is None
    assert InfluencerListQuery(tag="  Beauty美妆  ").tag == "Beauty美妆"
    assert InfluencerListQuery(tag="A" * 160).tag == "A" * 160

    with pytest.raises(ValidationError):
        InfluencerListQuery(tag="A" * 161)


def test_list_query_accepts_only_closed_content_activity_filters() -> None:
    assert (
        InfluencerListQuery(content_activity_filter="inactive_60d").content_activity_filter
        is ContentActivityFilter.INACTIVE_60D
    )
    with pytest.raises(ValidationError):
        InfluencerListQuery(content_activity_filter="inactive_999d")


def test_list_query_validates_follower_range_without_coercing_boundaries() -> None:
    zero = InfluencerListQuery(followers_min=0, followers_max=0)
    assert zero.followers_min == 0
    assert zero.followers_max == 0

    with pytest.raises(ValidationError):
        InfluencerListQuery(followers_min=-1)
    with pytest.raises(ValidationError):
        InfluencerListQuery(followers_max=-1)
    with pytest.raises(ValidationError):
        InfluencerListQuery(followers_min=2, followers_max=1)
    with pytest.raises(ValidationError):
        InfluencerListQuery.model_validate({"followers_min": "1.5"})
    with pytest.raises(ValidationError):
        InfluencerListQuery.model_validate({"followers_min": "1.0"})
    with pytest.raises(ValidationError):
        InfluencerListQuery.model_validate({"followers_min": 1.0})
    with pytest.raises(ValidationError):
        InfluencerListQuery.model_validate({"followers_min": True})

    assert InfluencerListQuery.model_validate({"followers_min": "1"}).followers_min == 1


def test_list_query_uses_real_uuid_and_crm_stage_enum() -> None:
    owner_id = uuid4()
    query = InfluencerListQuery(
        owner_operator_id=owner_id,
        crm_stage=CRMStage.HIGH_INTENT,
    )

    assert query.owner_operator_id == owner_id
    assert query.crm_stage is CRMStage.HIGH_INTENT
    assert (
        InfluencerListQuery.model_validate(
            {"owner_operator_id": str(owner_id), "crm_stage": "高意向"}
        ).crm_stage
        is CRMStage.HIGH_INTENT
    )

    with pytest.raises(ValidationError):
        InfluencerListQuery(crm_stage="不存在的阶段")  # type: ignore[arg-type]


def test_list_query_accepts_only_closed_contact_and_notes_filters() -> None:
    query = InfluencerListQuery.model_validate(
        {
            "contact_filter": "has_email",
            "notes_7d_filter": "three_plus",
            "notes_60d_filter": "three_to_nine",
        }
    )

    assert query.contact_filter is ContactFilter.HAS_EMAIL
    assert query.notes_7d_filter is Notes7dFilter.THREE_PLUS
    assert query.notes_60d_filter is Notes60dFilter.THREE_TO_NINE
    for name, invalid in (
        ("contact_filter", "email"),
        ("notes_7d_filter", "7d"),
        ("notes_60d_filter", "7d"),
    ):
        with pytest.raises(ValidationError):
            InfluencerListQuery.model_validate({name: invalid})


@pytest.mark.parametrize("status", list(FreshnessStatus))
def test_list_query_accepts_each_freshness_status(status: FreshnessStatus) -> None:
    assert InfluencerListQuery(freshness_status=status).freshness_status is status
    assert (
        InfluencerListQuery.model_validate({"freshness_status": status.value}).freshness_status
        is status
    )

    with pytest.raises(ValidationError):
        InfluencerListQuery.model_validate({"freshness_status": "not-a-status"})


def test_list_query_requires_exact_raw_boolean_spelling() -> None:
    assert InfluencerListQuery(requires_refresh=True).requires_refresh is True
    assert InfluencerListQuery(requires_refresh=False).requires_refresh is False
    assert InfluencerListQuery.model_validate({"requires_refresh": "true"}).requires_refresh is True
    assert (
        InfluencerListQuery.model_validate({"requires_refresh": "false"}).requires_refresh is False
    )

    assert InfluencerListQuery.model_validate({"requires_refresh": None}).requires_refresh is None
    for invalid in ("1", "0", "TRUE", "False", "yes", "on", " true", 1, 0):
        with pytest.raises(ValidationError):
            InfluencerListQuery.model_validate({"requires_refresh": invalid})


def test_list_query_requires_aware_observed_range_and_normalizes_to_utc() -> None:
    china_time = timezone(timedelta(hours=8))
    query = InfluencerListQuery.model_validate(
        {
            "last_huitun_observed_after": datetime(2026, 8, 1, 12, 0, tzinfo=china_time),
            "last_huitun_observed_before": "2026-08-02T12:00:00+08:00",
        }
    )

    assert query.last_huitun_observed_after == datetime(2026, 8, 1, 4, 0, tzinfo=UTC)
    assert query.last_huitun_observed_before == datetime(2026, 8, 2, 4, 0, tzinfo=UTC)
    assert query.last_huitun_observed_after.tzinfo is UTC
    assert query.last_huitun_observed_before.tzinfo is UTC

    exact = datetime(2026, 8, 1, 4, 0, tzinfo=UTC)
    equal_range = InfluencerListQuery(
        last_huitun_observed_after=exact,
        last_huitun_observed_before=exact,
    )
    assert equal_range.last_huitun_observed_after == equal_range.last_huitun_observed_before

    for naive in (datetime(2026, 8, 1, 4, 0), "2026-08-01T04:00:00"):
        with pytest.raises(ValidationError):
            InfluencerListQuery.model_validate({"last_huitun_observed_after": naive})

    with pytest.raises(ValidationError):
        InfluencerListQuery.model_validate({"last_huitun_observed_after": "0"})

    with pytest.raises(ValidationError):
        InfluencerListQuery(
            last_huitun_observed_after=datetime(2026, 8, 2, tzinfo=UTC),
            last_huitun_observed_before=datetime(2026, 8, 1, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "values",
    [
        {"page": 0},
        {"page_size": 0},
        {"page_size": 101},
        {"page": "1.0"},
    ],
)
def test_list_query_validates_pagination(values: dict[str, int | str]) -> None:
    with pytest.raises(ValidationError):
        InfluencerListQuery.model_validate(values)

    assert InfluencerListQuery(page_size=100).page_size == 100


def build_list_item() -> InfluencerListItem:
    now = datetime(2026, 8, 10, tzinfo=UTC)
    return InfluencerListItem(
        id=uuid4(),
        display_name="脱敏达人",
        status=InfluencerStatus.ACTIVE,
        crm_stage=CRMStage.TO_DEVELOP,
        created_at=now,
        updated_at=now,
    )


def test_list_output_expresses_nulls_empty_collections_and_zero_followers() -> None:
    item = build_list_item()
    assert item.owner is None
    assert item.platform_accounts == []
    assert item.current_metrics == []
    assert item.current_contacts == []
    assert item.freshness_status is FreshnessStatus.UNKNOWN
    assert item.requires_refresh is False

    metric = CurrentMetricsSummary(
        platform_account_id=uuid4(),
        source=DataSource.HUITUN,
        source_updated_at=None,
        followers_count=0,
    )
    assert metric.source_updated_at is None
    assert metric.followers_count == 0

    page = InfluencerListPage(items=[item], page=1, page_size=50, total=1)
    assert page.items == [item]
    assert InfluencerListPage(page=1, page_size=50, total=0).items == []


@pytest.mark.parametrize("followers_count", [True, 1.0, "1", "1.0", -1])
def test_current_metrics_summary_requires_strict_nonnegative_integer(
    followers_count: object,
) -> None:
    with pytest.raises(ValidationError):
        CurrentMetricsSummary.model_validate(
            {
                "platform_account_id": str(uuid4()),
                "source": "huitun",
                "source_updated_at": None,
                "followers_count": followers_count,
            }
        )


def test_platform_owner_and_contact_summaries_match_the_frozen_contract() -> None:
    observed_at = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)
    imported_at = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)
    owner = OwnerSummary(id=uuid4(), name="负责人", status=OperatorStatus.DISABLED)
    account = PlatformAccountSummary(
        id=uuid4(),
        platform=Platform.XIAOHONGSHU,
        platform_account_id=None,
        account_name="脱敏账号",
        account_handle=None,
        profile_url=None,
        source=DataSource.HUITUN,
        is_active=True,
        last_huitun_observed_at=observed_at,
        last_huitun_imported_at=imported_at,
        freshness_status=FreshnessStatus.FRESH,
        freshness_age_days=0,
        requires_refresh=False,
    )
    contact = CurrentContactSummary(
        id=uuid4(),
        type=ContactType.EMAIL,
        display_value="***",
        source=DataSource.HUITUN,
        validation_status=ContactValidationStatus.VALID,
        possible_duplicate_contact=False,
    )

    assert owner.status is OperatorStatus.DISABLED
    assert account.source_tags == []
    assert account.last_huitun_observed_at == observed_at
    assert account.last_huitun_imported_at == imported_at
    assert account.freshness_status is FreshnessStatus.FRESH
    assert account.freshness_age_days == 0
    assert account.requires_refresh is False
    freshness_fields = {
        "last_huitun_observed_at",
        "last_huitun_imported_at",
        "freshness_status",
        "freshness_age_days",
        "requires_refresh",
    }
    assert freshness_fields <= set(PlatformAccountSummary.model_fields)
    assert freshness_fields <= set(PlatformAccountDetail.model_fields)
    assert contact.display_value == "***"
    assert "normalized_value" not in CurrentContactSummary.model_fields

    with pytest.raises(ValidationError):
        CurrentContactSummary.model_validate(
            {
                **contact.model_dump(),
                "normalized_value": "sensitive@example.com",
            }
        )

    non_eligible = PlatformAccountSummary(
        id=uuid4(),
        platform=Platform.XIAOHONGSHU,
        platform_account_id=None,
        account_name="非灰豚来源账号",
        account_handle=None,
        profile_url=None,
        source=DataSource.MANUAL,
        is_active=True,
    )
    assert non_eligible.last_huitun_observed_at is None
    assert non_eligible.last_huitun_imported_at is None
    assert non_eligible.freshness_status is None
    assert non_eligible.freshness_age_days is None
    assert non_eligible.requires_refresh is False

    for invalid_age in (-1, True, 1.5, "1"):
        with pytest.raises(ValidationError):
            PlatformAccountSummary.model_validate(
                {**account.model_dump(), "freshness_age_days": invalid_age}
            )


def test_metrics_preserve_recursive_json_types_and_missing_keys() -> None:
    metrics = {
        "followers_count": 0,
        "huitun_score": "321.45",
        "source_value": None,
        "distribution": [
            {"label": "上海", "rate": "0.174"},
            {"label": "北京", "rate": "0.152"},
        ],
        "verified": False,
    }
    output = CurrentMetricsDetail(
        platform_account_id=uuid4(),
        source=DataSource.HUITUN,
        source_updated_at=None,
        metrics=metrics,
        last_import_job_id=uuid4(),
        last_import_row_id=uuid4(),
    )

    assert output.metrics == metrics
    assert output.metrics["followers_count"] == 0
    assert output.metrics["huitun_score"] == "321.45"
    assert "missing_metric" not in output.metrics

    with pytest.raises(ValidationError):
        CurrentMetricsDetail(
            platform_account_id=uuid4(),
            source=DataSource.HUITUN,
            source_updated_at=None,
            metrics={"price": 1.25},
            last_import_job_id=uuid4(),
            last_import_row_id=uuid4(),
        )


def test_detail_structurally_carries_only_frozen_sections() -> None:
    now = datetime(2026, 8, 10, tzinfo=UTC)
    account_id = uuid4()
    import_job_id = uuid4()
    import_row_id = uuid4()
    detail = InfluencerDetail(
        id=uuid4(),
        display_name="脱敏达人",
        status=InfluencerStatus.ACTIVE,
        crm_stage=CRMStage.TO_DEVELOP,
        owner=None,
        created_at=now,
        updated_at=now,
        platform_accounts=[
            PlatformAccountDetail(
                id=account_id,
                platform=Platform.XIAOHONGSHU,
                platform_account_id="fixture-account",
                account_name="脱敏账号",
                account_handle=None,
                profile_url=None,
                source=DataSource.HUITUN,
                is_active=True,
                source_tags=["动画"],
                bio=None,
                gender=None,
                region_raw=None,
                verification_info=None,
                mcn_name=None,
                creator_level=None,
                is_brand_partner=None,
            )
        ],
        contacts=[
            InfluencerContactDetail(
                id=uuid4(),
                platform_account_id=account_id,
                type=ContactType.EMAIL,
                display_value="masked@example.com",
                source=DataSource.HUITUN,
                validation_status=ContactValidationStatus.VALID,
                is_current=True,
                possible_duplicate_contact=False,
                first_seen_at=now,
                last_seen_at=now,
                source_updated_at=None,
                first_import_job_id=import_job_id,
                first_import_row_id=import_row_id,
                last_import_job_id=import_job_id,
                last_import_row_id=import_row_id,
            )
        ],
        source_states=[
            SourceStateDetail(
                platform_account_id=account_id,
                source=DataSource.HUITUN,
                source_updated_at=None,
                state_version=1,
                creator_tags=["动画"],
                creator_classification_tags=["舞蹈"],
                last_import_job_id=import_job_id,
                last_import_row_id=import_row_id,
            )
        ],
        source_identities=[
            SourceIdentityDetail(
                id=uuid4(),
                platform_account_id=account_id,
                platform=Platform.XIAOHONGSHU,
                source=DataSource.HUITUN,
                external_account_id="external-id",
                first_import_job_id=import_job_id,
                first_import_row_id=import_row_id,
                last_import_job_id=import_job_id,
                last_import_row_id=import_row_id,
            )
        ],
        current_metrics=[
            CurrentMetricsDetail(
                platform_account_id=account_id,
                source=DataSource.HUITUN,
                source_updated_at=None,
                metrics={"followers_count": 0},
                last_import_job_id=import_job_id,
                last_import_row_id=import_row_id,
            )
        ],
    )

    assert set(InfluencerDetail.model_fields) == {
        "id",
        "display_name",
        "status",
        "crm_stage",
        "owner",
        "created_at",
        "updated_at",
        "platform_accounts",
        "contacts",
        "source_states",
        "source_identities",
        "current_metrics",
        "freshness_status",
        "requires_refresh",
    }
    assert detail.platform_accounts[0].source_tags == ["动画"]
    assert detail.source_states[0].creator_tags == ["动画"]
    assert detail.source_states[0].creator_classification_tags == ["舞蹈"]
    assert detail.current_metrics[0].metrics["followers_count"] == 0
    assert detail.freshness_status is FreshnessStatus.UNKNOWN
    assert detail.requires_refresh is False

    with pytest.raises(ValidationError):
        InfluencerDetail.model_validate({**detail.model_dump(), "ai_score": 99})


def test_snapshot_metrics_remain_exact_and_pages_default_to_empty() -> None:
    now = datetime(2026, 8, 10, tzinfo=UTC)
    snapshot = MetricSnapshotItem(
        id=uuid4(),
        platform_account_id=uuid4(),
        source=DataSource.HUITUN,
        source_updated_at=None,
        captured_at=now,
        metrics={"followers_count": 0, "huitun_score": "321.45"},
        import_job_id=uuid4(),
        import_row_id=uuid4(),
    )

    assert snapshot.metrics == {"followers_count": 0, "huitun_score": "321.45"}
    assert "notes_count" not in snapshot.metrics
    assert MetricSnapshotPage(page=1, page_size=50, total=0).items == []


def test_filter_options_have_only_the_three_frozen_fields() -> None:
    owner_id = UUID("00000000-0000-0000-0000-000000000001")
    options = InfluencerFilterOptions(
        owners=[OwnerSummary(id=owner_id, name="负责人", status=OperatorStatus.ACTIVE)],
        tags=["动画"],
        crm_stages=list(CRMStage),
    )

    assert set(InfluencerFilterOptions.model_fields) == {"owners", "tags", "crm_stages"}
    assert options.crm_stages == list(CRMStage)

    with pytest.raises(ValidationError):
        InfluencerFilterOptions.model_validate(
            {**options.model_dump(), "platforms": ["xiaohongshu"]}
        )

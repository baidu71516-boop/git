from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from backend_core.auth.enums import OperatorStatus
from backend_core.influencers.enums import (
    ContactType,
    ContactValidationStatus,
    CRMStage,
    DataSource,
    InfluencerStatus,
    Platform,
)
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
    assert contact.display_value == "***"
    assert "normalized_value" not in CurrentContactSummary.model_fields

    with pytest.raises(ValidationError):
        CurrentContactSummary.model_validate(
            {
                **contact.model_dump(),
                "normalized_value": "sensitive@example.com",
            }
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
    }
    assert detail.platform_accounts[0].source_tags == ["动画"]
    assert detail.source_states[0].creator_tags == ["动画"]
    assert detail.current_metrics[0].metrics["followers_count"] == 0

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

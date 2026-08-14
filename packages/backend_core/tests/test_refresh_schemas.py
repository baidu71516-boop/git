from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from backend_core.influencers.enums import DataSource, Platform
from backend_core.influencers.freshness import FreshnessPolicy, FreshnessStatus
from backend_core.refresh.enums import (
    RefreshPriorityReason,
    RefreshQueueItemStatus,
    RefreshQueueStatus,
)
from backend_core.refresh.schemas import (
    MAX_PAGE_LIMIT,
    MAX_REQUESTED_LIMIT,
    SCHEMA_VERSION,
    CriteriaSnapshot,
    IdentitySnapshot,
    RefreshQueueCreateInput,
    RefreshQueueDetailResponse,
    RefreshQueueItemListQuery,
    RefreshQueueItemPage,
    RefreshQueueItemPublic,
    RefreshQueueListPage,
    RefreshQueueListQuery,
    RefreshQueuePublic,
    RefreshQueueSummary,
)
from pydantic import ValidationError


@pytest.mark.parametrize("requested_limit", [200, 500, 800, 2_000])
def test_create_input_supports_frozen_queue_sizes(requested_limit: int) -> None:
    request = RefreshQueueCreateInput(
        requested_limit=requested_limit,
        refresh_limit=requested_limit,
        today_total_limit=requested_limit,
    )

    assert request.requested_limit == requested_limit
    assert request.department_id is None


@pytest.mark.parametrize(
    "payload",
    [
        {"requested_limit": 0, "refresh_limit": 1, "today_total_limit": 1},
        {"requested_limit": 2_001, "refresh_limit": 2_001, "today_total_limit": 2_001},
        {"requested_limit": 2, "refresh_limit": 1, "today_total_limit": 2},
        {"requested_limit": 1, "refresh_limit": 3, "today_total_limit": 2},
    ],
)
def test_create_input_enforces_positive_ordered_two_thousand_item_gate(
    payload: dict[str, int],
) -> None:
    with pytest.raises(ValidationError):
        RefreshQueueCreateInput.model_validate(payload)


@pytest.mark.parametrize("field", ["requested_limit", "refresh_limit", "today_total_limit"])
@pytest.mark.parametrize("invalid", [True, 1.0, "1"])
def test_create_input_limits_are_strict_integers(field: str, invalid: object) -> None:
    payload: dict[str, object] = {
        "requested_limit": 1,
        "refresh_limit": 1,
        "today_total_limit": 1,
    }
    payload[field] = invalid

    with pytest.raises(ValidationError):
        RefreshQueueCreateInput.model_validate(payload)


@pytest.mark.parametrize(
    "extra_field",
    ["as_of", "policy_version", "criteria", "criteria_snapshot"],
)
def test_create_input_rejects_server_owned_fields(extra_field: str) -> None:
    payload: dict[str, object] = {
        "requested_limit": 1,
        "refresh_limit": 1,
        "today_total_limit": 1,
        extra_field: {},
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RefreshQueueCreateInput.model_validate(payload)


def test_create_input_accepts_optional_target_department() -> None:
    department_id = uuid4()
    request = RefreshQueueCreateInput(
        department_id=department_id,
        requested_limit=1,
        refresh_limit=2,
        today_total_limit=3,
    )

    assert request.department_id == department_id


def test_identity_snapshot_preserves_real_text_and_has_closed_shape() -> None:
    snapshot = IdentitySnapshot(
        platform=Platform.XIAOHONGSHU,
        account_name="  =真实原值  ",
        platform_account_id=None,
        account_handle=None,
        profile_url=None,
        external_source_id=None,
        followers_count=0,
    )

    assert snapshot.account_name == "  =真实原值  "
    assert snapshot.followers_count == 0
    assert snapshot.schema_version == 1
    assert set(IdentitySnapshot.model_fields) == {
        "schema_version",
        "platform",
        "account_name",
        "platform_account_id",
        "account_handle",
        "profile_url",
        "external_source_id",
        "followers_count",
    }


@pytest.mark.parametrize("forbidden", ["contact", "email", "phone", "wechat", "metrics"])
def test_identity_snapshot_forbids_contact_and_arbitrary_documents(forbidden: str) -> None:
    payload: dict[str, object] = {
        "platform": Platform.XIAOHONGSHU,
        "account_name": "真实账号",
        forbidden: "sensitive",
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        IdentitySnapshot.model_validate(payload)


def test_identity_snapshot_requires_one_trimmed_nonblank_locator() -> None:
    with pytest.raises(ValidationError, match="nonblank exportable identity"):
        IdentitySnapshot(
            platform=Platform.XIAOHONGSHU,
            account_name=" \t\r\n ",
            platform_account_id=None,
            account_handle="   ",
            profile_url=None,
            external_source_id="\t",
        )


def test_identity_snapshot_rejects_unicode_whitespace_only_locators() -> None:
    with pytest.raises(ValidationError, match="nonblank exportable identity"):
        IdentitySnapshot(
            platform=Platform.XIAOHONGSHU,
            account_name="\u00a0\u3000",
            platform_account_id="\u2007",
            account_handle="\u202f",
            profile_url="\u205f",
            external_source_id="\u1680",
        )


@pytest.mark.parametrize("followers_count", [True, 1.0, "1", -1])
def test_identity_snapshot_followers_are_strict_nonnegative(
    followers_count: object,
) -> None:
    with pytest.raises(ValidationError):
        IdentitySnapshot.model_validate(
            {
                "platform": Platform.XIAOHONGSHU,
                "account_name": "真实账号",
                "followers_count": followers_count,
            }
        )


def test_criteria_snapshot_freezes_limits_policy_rules_and_sort() -> None:
    request = RefreshQueueCreateInput(
        requested_limit=200,
        refresh_limit=500,
        today_total_limit=800,
    )
    policy = FreshnessPolicy.from_day_thresholds(2, 10, 40)

    snapshot = CriteriaSnapshot.from_policy(create_input=request, policy=policy)

    assert snapshot.schema_version == SCHEMA_VERSION == 1
    assert snapshot.policy_version == 1
    assert snapshot.requested_limit == 200
    assert snapshot.refresh_limit == 500
    assert snapshot.today_total_limit == 800
    assert snapshot.suggested_new_acquisition == 300
    assert snapshot.freshness_thresholds.fresh_duration_seconds == int(
        timedelta(days=2).total_seconds()
    )
    assert snapshot.freshness_thresholds.aging_duration_seconds == int(
        timedelta(days=10).total_seconds()
    )
    assert snapshot.freshness_thresholds.stale_duration_seconds == int(
        timedelta(days=40).total_seconds()
    )
    assert [rule.tier for rule in snapshot.priority_rules.tiers] == [1, 2, 3, 4, 5]
    assert snapshot.sort == (
        "priority_tier ASC",
        "baseline_last_observed_at ASC NULLS FIRST",
        "influencer_id ASC",
        "platform_account_id ASC",
    )
    assert snapshot.candidate_rules.identity_any_of == (
        "platform_account_id",
        "profile_url",
        "account_handle",
        "account_name",
        "external_source_id",
    )


def test_criteria_snapshot_is_immutable_and_extra_forbid() -> None:
    snapshot = CriteriaSnapshot.from_policy(
        create_input=RefreshQueueCreateInput(
            requested_limit=1,
            refresh_limit=1,
            today_total_limit=1,
        ),
        policy=FreshnessPolicy(),
    )

    with pytest.raises(ValidationError, match="Instance is frozen"):
        snapshot.requested_limit = 2
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CriteriaSnapshot.model_validate({**snapshot.model_dump(), "contact": "forbidden"})


@pytest.mark.parametrize("query_type", [RefreshQueueListQuery, RefreshQueueItemListQuery])
def test_pagination_defaults_and_two_hundred_max(query_type: type[object]) -> None:
    query = query_type()  # type: ignore[call-arg]

    assert query.offset == 0  # type: ignore[attr-defined]
    assert query.limit == 50  # type: ignore[attr-defined]
    assert MAX_PAGE_LIMIT == 200

    assert query_type.model_validate({"offset": 1, "limit": 200}).limit == 200  # type: ignore[attr-defined]
    for payload in (
        {"offset": -1, "limit": 50},
        {"offset": 0, "limit": 201},
        {"offset": "0", "limit": 50},
        {"offset": 0, "limit": True},
        {"offset": 0, "limit": 50, "page": 1},
    ):
        with pytest.raises(ValidationError):
            query_type.model_validate(payload)  # type: ignore[attr-defined]


def test_summary_contract_has_all_required_breakdowns() -> None:
    summary = RefreshQueueSummary(
        requested=3,
        selected=2,
        unique_influencers=1,
        freshness_breakdown={FreshnessStatus.UNKNOWN: 1, FreshnessStatus.STALE: 1},
        priority_breakdown={1: 1, 3: 1},
        status_breakdown={RefreshQueueItemStatus.PENDING: 2},
    )

    assert summary.selected == 2
    assert summary.unique_influencers == 1


def test_queue_item_and_page_response_are_typed_closed_contracts() -> None:
    now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    item = RefreshQueueItemPublic(
        id=uuid4(),
        department_id=uuid4(),
        queue_id=uuid4(),
        influencer_id=uuid4(),
        platform_account_id=uuid4(),
        source=DataSource.HUITUN,
        priority_tier=3,
        priority_reasons=(
            RefreshPriorityReason.STALE,
            RefreshPriorityReason.FOLLOWERS_MISSING,
        ),
        identity_snapshot=IdentitySnapshot(
            platform=Platform.XIAOHONGSHU,
            account_name="真实账号",
            followers_count=None,
        ),
        baseline_last_observed_at=now - timedelta(days=60),
        baseline_source_updated_at=now - timedelta(days=61),
        status=RefreshQueueItemStatus.PENDING,
        created_at=now,
        updated_at=now,
    )
    page = RefreshQueueItemPage(items=[item], total=1, offset=0, limit=50)

    assert page.items[0].priority_reasons == (
        RefreshPriorityReason.STALE,
        RefreshPriorityReason.FOLLOWERS_MISSING,
    )
    assert page.items[0].identity_snapshot.followers_count is None
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RefreshQueueItemPublic.model_validate({**item.model_dump(), "contact": "forbidden"})


@pytest.mark.parametrize(
    ("tier", "reasons"),
    [
        (1, [RefreshPriorityReason.VERY_STALE]),
        (2, [RefreshPriorityReason.FOLLOWERS_MISSING, RefreshPriorityReason.VERY_STALE]),
        (3, [RefreshPriorityReason.STALE, RefreshPriorityReason.STALE]),
        (4, [RefreshPriorityReason.AGING, RefreshPriorityReason.STALE]),
        (5, [RefreshPriorityReason.FOLLOWERS_MISSING, RefreshPriorityReason.STALE]),
    ],
)
def test_item_response_rejects_unstable_reason_order(
    tier: int,
    reasons: list[RefreshPriorityReason],
) -> None:
    now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    with pytest.raises(ValidationError, match="priority|tier 5|FOLLOWERS_MISSING"):
        RefreshQueueItemPublic(
            id=uuid4(),
            department_id=uuid4(),
            queue_id=uuid4(),
            influencer_id=uuid4(),
            platform_account_id=uuid4(),
            source=DataSource.HUITUN,
            priority_tier=tier,
            priority_reasons=tuple(reasons),
            identity_snapshot=IdentitySnapshot(
                platform=Platform.XIAOHONGSHU,
                account_name="真实账号",
            ),
            status=RefreshQueueItemStatus.PENDING,
            created_at=now,
            updated_at=now,
        )


def test_queue_list_and_detail_response_include_typed_criteria_and_summary() -> None:
    now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    criteria = CriteriaSnapshot.from_policy(
        create_input=RefreshQueueCreateInput(
            requested_limit=2,
            refresh_limit=2,
            today_total_limit=3,
        ),
        policy=FreshnessPolicy(),
    )
    queue = RefreshQueuePublic(
        id=uuid4(),
        department_id=uuid4(),
        created_by_operator_id=uuid4(),
        status=RefreshQueueStatus.OPEN,
        as_of=now,
        requested_limit=2,
        today_total_limit=3,
        refresh_limit=2,
        policy_version=1,
        criteria_snapshot=criteria,
        created_at=now,
        updated_at=now,
    )
    summary = RefreshQueueSummary(
        requested=2,
        selected=1,
        unique_influencers=1,
        freshness_breakdown={FreshnessStatus.UNKNOWN: 1},
        priority_breakdown={1: 1},
        status_breakdown={RefreshQueueItemStatus.PENDING: 1},
    )

    page = RefreshQueueListPage(items=[queue], total=1, offset=0, limit=50)
    detail = RefreshQueueDetailResponse(queue=queue, summary=summary)

    assert page.items[0].as_of == now
    assert detail.summary.requested == detail.queue.requested_limit


@pytest.mark.parametrize(
    "replacement",
    [
        {"selected": 4},
        {"unique_influencers": 3},
        {"freshness_breakdown": {FreshnessStatus.UNKNOWN: 1}},
        {"priority_breakdown": {1: 1}},
        {"status_breakdown": {RefreshQueueItemStatus.PENDING: 1}},
    ],
)
def test_summary_rejects_inconsistent_counts(replacement: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "requested": 3,
        "selected": 2,
        "unique_influencers": 1,
        "freshness_breakdown": {FreshnessStatus.UNKNOWN: 2},
        "priority_breakdown": {1: 2},
        "status_breakdown": {RefreshQueueItemStatus.PENDING: 2},
    }
    payload.update(replacement)

    with pytest.raises(ValidationError):
        RefreshQueueSummary.model_validate(payload)


def test_frozen_constants_are_exact() -> None:
    assert SCHEMA_VERSION == 1
    assert MAX_REQUESTED_LIMIT == 2_000

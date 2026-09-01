"""Unit coverage for the Phase 1C read-only influencer service."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from backend_core.audit.repository import AuditRepository
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, Operator
from backend_core.auth.service import AuthContext
from backend_core.content_activity.enums import (
    ContentActivityCoverageStatus,
    ContentActivityObservationStatus,
    ContentActivityPublicationType,
    ContentActivityResult,
)
from backend_core.content_activity.models import ContentActivityProjection
from backend_core.influencers.enums import (
    ContactType,
    ContactValidationStatus,
    CRMStage,
    DataSource,
    InfluencerStatus,
    Platform,
)
from backend_core.influencers.freshness import (
    ContentActivityFreshnessPolicy,
    FreshnessPolicy,
    FreshnessStatus,
)
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
    InfluencerSourceState,
    PlatformAccountSourceIdentity,
)
from backend_core.influencers.repository import (
    AccountSourceFreshnessRecord,
    InfluencerDetailRecord,
    InfluencerListRecord,
)
from backend_core.influencers.schemas import InfluencerListQuery
from backend_core.influencers.service import InfluencerNotFoundError, InfluencerService

NOW = datetime(2026, 8, 11, 4, 0, tzinfo=UTC)
INFLUENCER_ID = UUID("00000000-0000-0000-0000-000000000101")
ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000102")
CONTACT_ID = UUID("00000000-0000-0000-0000-000000000103")


class FakeInfluencerRepository:
    def __init__(self) -> None:
        self.list_result: tuple[list[InfluencerListRecord], int] = ([], 0)
        self.detail_result: InfluencerDetailRecord | None = None
        self.snapshot_result: tuple[list[InfluencerMetricSnapshot], int] | None = ([], 0)
        self.owner_options: list[Operator] = []
        self.tag_options: list[str] = []
        self.calls: list[tuple[str, object]] = []

    async def list_influencers(
        self,
        query: InfluencerListQuery,
        *,
        as_of: datetime | None = None,
        policy: FreshnessPolicy | None = None,
        content_activity_freshness_policy: ContentActivityFreshnessPolicy | None = None,
    ) -> tuple[list[InfluencerListRecord], int]:
        self.calls.append(("list", (query, as_of, policy, content_activity_freshness_policy)))
        return self.list_result

    async def get_influencer_detail(self, influencer_id: UUID) -> InfluencerDetailRecord | None:
        self.calls.append(("detail", influencer_id))
        return self.detail_result

    async def list_metric_snapshots(
        self,
        influencer_id: UUID,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[InfluencerMetricSnapshot], int] | None:
        self.calls.append(("snapshots", (influencer_id, page, page_size)))
        return self.snapshot_result

    async def list_filter_option_owners(self) -> list[Operator]:
        self.calls.append(("owners", None))
        return self.owner_options

    async def list_filter_option_tags(self) -> list[str]:
        self.calls.append(("tags", None))
        return self.tag_options


def make_context(
    role: Role,
    *,
    operator_role: Role | None = None,
    department_id: UUID | None = None,
) -> AuthContext:
    resolved_department_id = department_id or uuid4()
    selected_role = operator_role or role
    department = Department(
        id=resolved_department_id,
        name=f"Department {role.value}",
        password_hash="not-used-by-service-test",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
        created_at=NOW,
        updated_at=NOW,
    )
    operator = Operator(
        id=uuid4(),
        department_id=resolved_department_id,
        name=f"Selected {selected_role.value}",
        role=selected_role,
        status=OperatorStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    auth_session = AuthSession(
        id=uuid4(),
        department_id=resolved_department_id,
        operator_id=operator.id,
        token_hash=uuid4().hex * 2,
        csrf_token_hash=uuid4().hex * 2,
        ip="192.0.2.10",
        user_agent="service-test",
        expires_at=NOW + timedelta(hours=12),
        revoked_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    return AuthContext(
        department=department,
        operator=operator,
        role=role,
        auth_session=auth_session,
    )


def make_owner(
    *,
    name: str = "Cross Department Owner",
    status: OperatorStatus = OperatorStatus.DISABLED,
) -> Operator:
    return Operator(
        id=uuid4(),
        department_id=uuid4(),
        name=name,
        role=Role.MANAGER,
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def make_influencer(*, owner: Operator | None = None) -> Influencer:
    return Influencer(
        id=INFLUENCER_ID,
        display_name="脱敏达人",
        owner_operator_id=owner.id if owner is not None else None,
        crm_stage=CRMStage.HIGH_INTENT,
        status=InfluencerStatus.ACTIVE,
        deleted_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


def make_account(
    *,
    tags: list[str] | None = None,
    account_id: UUID = ACCOUNT_ID,
    source: DataSource = DataSource.HUITUN,
) -> InfluencerPlatformAccount:
    return InfluencerPlatformAccount(
        id=account_id,
        influencer_id=INFLUENCER_ID,
        platform=Platform.XIAOHONGSHU,
        platform_account_id="fixture-account",
        account_name="脱敏账号",
        account_handle="fixture-handle",
        profile_url="https://example.invalid/profile/fixture-account",
        normalized_profile_url="https://example.invalid/profile/fixture-account",
        source=source,
        is_active=True,
        bio="公开简介",
        gender=None,
        region_raw=None,
        verification_info=None,
        mcn_name=None,
        source_tags=tags,
        creator_level=None,
        is_brand_partner=None,
        created_at=NOW,
        updated_at=NOW,
    )


def make_contact(
    value: str,
    *,
    contact_id: UUID = CONTACT_ID,
    contact_type: ContactType = ContactType.EMAIL,
    duplicate: bool = False,
) -> InfluencerContact:
    return InfluencerContact(
        id=contact_id,
        influencer_id=INFLUENCER_ID,
        platform_account_id=ACCOUNT_ID,
        type=contact_type,
        value=value,
        normalized_value=f"normalized-{contact_id}",
        source=DataSource.MANUAL,
        validation_status=ContactValidationStatus.UNVERIFIED,
        is_current=True,
        possible_duplicate_contact=duplicate,
        first_seen_at=NOW,
        last_seen_at=NOW,
        source_updated_at=None,
        first_import_job_id=None,
        first_import_row_id=None,
        last_import_job_id=None,
        last_import_row_id=None,
        created_at=NOW,
        updated_at=NOW,
    )


def make_metrics(value: object) -> InfluencerCurrentMetrics:
    return InfluencerCurrentMetrics(
        id=uuid4(),
        influencer_id=INFLUENCER_ID,
        platform_account_id=ACCOUNT_ID,
        source=DataSource.HUITUN,
        source_updated_at=NOW,
        metrics={"followers_count": value},
        metrics_hash=uuid4().hex * 2,
        last_import_job_id=uuid4(),
        last_import_row_id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )


def make_content_activity_projection(
    *,
    account_id: UUID = ACCOUNT_ID,
    trusted_observed_at: datetime = NOW - timedelta(days=1),
    trusted_result: ContentActivityResult = ContentActivityResult.PUBLICATION_FOUND,
    last_publication_at: datetime | None = NOW - timedelta(days=60),
    latest_observed_at: datetime | None = None,
    latest_status: ContentActivityObservationStatus | None = None,
    latest_coverage: ContentActivityCoverageStatus | None = None,
    latest_result: ContentActivityResult | None = None,
) -> ContentActivityProjection:
    has_publication = trusted_result is ContentActivityResult.PUBLICATION_FOUND
    resolved_latest_observed_at = latest_observed_at or trusted_observed_at
    resolved_latest_status = latest_status or ContentActivityObservationStatus.COMPLETE
    resolved_latest_coverage = (
        latest_coverage or ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET
    )
    resolved_latest_result = latest_result or trusted_result
    trusted_observation_id = uuid4()
    latest_attempt_observation_id = (
        trusted_observation_id
        if (
            resolved_latest_observed_at == trusted_observed_at
            and resolved_latest_status is ContentActivityObservationStatus.COMPLETE
            and resolved_latest_coverage is ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET
            and resolved_latest_result is trusted_result
        )
        else uuid4()
    )
    return ContentActivityProjection(
        id=uuid4(),
        platform_account_id=account_id,
        platform=Platform.XIAOHONGSHU,
        latest_attempt_observation_id=latest_attempt_observation_id,
        latest_attempt_observed_at=resolved_latest_observed_at,
        latest_attempt_observation_status=resolved_latest_status,
        latest_attempt_coverage_status=resolved_latest_coverage,
        latest_attempt_activity_result=resolved_latest_result,
        latest_attempt_provider_error_class=None,
        latest_attempt_provider_error_code=None,
        trusted_observation_id=trusted_observation_id,
        trusted_observed_at=trusted_observed_at,
        trusted_observation_status=ContentActivityObservationStatus.COMPLETE,
        trusted_coverage_status=ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET,
        trusted_activity_result=trusted_result,
        trusted_capability_policy_version="TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1",
        last_publication_at=last_publication_at if has_publication else None,
        latest_publication_id_namespace="xiaohongshu.noteid" if has_publication else None,
        latest_publication_id="fixture-note" if has_publication else None,
        latest_publication_type=(ContentActivityPublicationType.VIDEO if has_publication else None),
        co_latest_publication_count=1 if has_publication else None,
        version=1,
    )


def make_list_record(
    *,
    role_contact_values: tuple[str, ...] = ("fixture@example.invalid",),
    metric_values: tuple[object, ...] = (100,),
    owner: Operator | None = None,
    duplicate: bool = False,
    freshness: tuple[AccountSourceFreshnessRecord, ...] = (),
) -> InfluencerListRecord:
    contacts = tuple(
        make_contact(
            value,
            contact_id=UUID(int=CONTACT_ID.int + index),
            contact_type=ContactType.EMAIL if index == 0 else ContactType.PHONE,
            duplicate=duplicate and index == 0,
        )
        for index, value in enumerate(role_contact_values)
    )
    return InfluencerListRecord(
        influencer=make_influencer(owner=owner),
        owner=owner,
        platform_accounts=(make_account(tags=["动画"]),),
        current_metrics=tuple(make_metrics(value) for value in metric_values),
        current_contacts=contacts,
        huitun_freshness=freshness,
    )


def make_detail_record(
    *,
    contact_values: tuple[str, ...] = ("fixture@example.invalid",),
    creator_tags: object = ("动画", "电商"),
    owner: Operator | None = None,
    freshness: tuple[AccountSourceFreshnessRecord, ...] = (),
) -> InfluencerDetailRecord:
    account = make_account(tags=["动画"])
    contacts = tuple(
        make_contact(
            value,
            contact_id=UUID(int=CONTACT_ID.int + index),
            contact_type=ContactType.EMAIL if index == 0 else ContactType.PHONE,
        )
        for index, value in enumerate(contact_values)
    )
    source_state = InfluencerSourceState(
        id=uuid4(),
        influencer_id=INFLUENCER_ID,
        platform_account_id=ACCOUNT_ID,
        source=DataSource.HUITUN,
        source_updated_at=NOW,
        source_data={"creator_tags": creator_tags, "private_source_field": "do-not-expose"},
        source_data_hash=uuid4().hex * 2,
        state_version=2,
        last_import_job_id=uuid4(),
        last_import_row_id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )
    source_identity = PlatformAccountSourceIdentity(
        id=uuid4(),
        platform_account_id=ACCOUNT_ID,
        platform=Platform.XIAOHONGSHU,
        source=DataSource.HUITUN,
        external_account_id="provider-fixture-id",
        first_import_job_id=uuid4(),
        first_import_row_id=uuid4(),
        last_import_job_id=uuid4(),
        last_import_row_id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )
    metric = make_metrics(100)
    metric.metrics = {
        "followers_count": 100,
        "huitun_score": "88.5",
        "distribution": [{"label": "上海", "rate": "0.25"}],
        "verified": False,
        "missing_source_value": None,
    }
    return InfluencerDetailRecord(
        influencer=make_influencer(owner=owner),
        owner=owner,
        platform_accounts=(account,),
        contacts=contacts,
        source_states=(source_state,),
        source_identities=(source_identity,),
        current_metrics=(metric,),
        huitun_freshness=freshness,
    )


def make_snapshot() -> InfluencerMetricSnapshot:
    return InfluencerMetricSnapshot(
        id=uuid4(),
        influencer_id=INFLUENCER_ID,
        platform_account_id=ACCOUNT_ID,
        source=DataSource.HUITUN,
        source_updated_at=NOW,
        import_job_id=uuid4(),
        import_row_id=uuid4(),
        captured_at=NOW,
        metrics={"followers_count": 100, "huitun_score": "88.5"},
        metrics_hash=uuid4().hex * 2,
        snapshot_key=uuid4().hex * 2,
        created_at=NOW,
        updated_at=NOW,
    )


def make_freshness(
    *,
    account_id: UUID = ACCOUNT_ID,
    observed_at: datetime | None,
    imported_at: datetime | None = NOW,
    source: DataSource = DataSource.HUITUN,
) -> AccountSourceFreshnessRecord:
    return AccountSourceFreshnessRecord(
        platform_account_id=account_id,
        source=source,
        last_observed_at=observed_at,
        last_imported_at=imported_at,
    )


def all_mapping_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key))
            keys.update(all_mapping_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(all_mapping_keys(item))
    return keys


@pytest.mark.parametrize("role", list(Role))
def test_all_roles_can_read_all_four_company_level_capabilities(role: Role) -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        repository.list_result = ([make_list_record()], 1)
        repository.detail_result = make_detail_record()
        repository.snapshot_result = ([make_snapshot()], 1)
        repository.owner_options = [make_owner(status=OperatorStatus.ACTIVE)]
        repository.tag_options = ["动画"]
        service = InfluencerService(repository)
        context = make_context(role)

        assert (await service.list_influencers(context, InfluencerListQuery())).total == 1
        assert (await service.get_influencer_detail(context, INFLUENCER_ID)).id == INFLUENCER_ID
        assert (
            await service.list_metric_snapshots(context, INFLUENCER_ID, page=1, page_size=50)
        ).total == 1
        options = await service.get_filter_options(context)
        assert options.tags == ["动画"]

    asyncio.run(scenario())


def test_list_and_detail_map_legacy_unknown_without_faking_observed_time() -> None:
    async def scenario() -> None:
        legacy = make_freshness(observed_at=None, imported_at=NOW - timedelta(days=1))
        repository = FakeInfluencerRepository()
        repository.list_result = ([make_list_record(freshness=(legacy,))], 1)
        repository.detail_result = make_detail_record(freshness=(legacy,))
        service = InfluencerService(repository, now_factory=lambda: NOW)
        context = make_context(Role.VIEWER)

        item = (await service.list_influencers(context, InfluencerListQuery())).items[0]
        detail = await service.get_influencer_detail(context, INFLUENCER_ID)
        for output in (item, detail):
            assert output.freshness_status is FreshnessStatus.UNKNOWN
            assert output.requires_refresh is True
            account = output.platform_accounts[0]
            assert account.last_huitun_observed_at is None
            assert account.last_huitun_imported_at == NOW - timedelta(days=1)
            assert account.freshness_status is FreshnessStatus.UNKNOWN
            assert account.freshness_age_days is None
            assert account.requires_refresh is True

    asyncio.run(scenario())


def test_content_activity_is_account_scoped_and_computes_transient_inactivity() -> None:
    async def scenario() -> None:
        second_account_id = UUID(int=ACCOUNT_ID.int + 10)
        record = make_detail_record()
        second_account = make_account(account_id=second_account_id, source=DataSource.GENERIC)
        record = replace(
            record,
            platform_accounts=(record.platform_accounts[0], second_account),
            content_activity_projections=(
                make_content_activity_projection(account_id=second_account_id),
            ),
        )
        repository = FakeInfluencerRepository()
        repository.detail_result = record
        service = InfluencerService(repository, now_factory=lambda: NOW)

        detail = await service.get_influencer_detail(
            make_context(Role.VIEWER),
            INFLUENCER_ID,
        )

        first, second = detail.platform_accounts
        assert first.content_activity_state.value == "not_checked"
        assert second.content_activity_state.value == "current"
        assert second.content_activity_last_publication_at == NOW - timedelta(days=60)
        assert second.content_activity_inactive_days == 60
        assert second.content_activity_trusted_observed_at == NOW - timedelta(days=1)
        assert second.content_activity_latest_attempt_observation_status is (
            ContentActivityObservationStatus.COMPLETE
        )

    asyncio.run(scenario())


def test_content_activity_inactive_days_uses_utc_floor_with_offset_timestamp() -> None:
    async def scenario() -> None:
        publication_at = (NOW - timedelta(days=60) + timedelta(seconds=1)).astimezone(
            timezone(timedelta(hours=8))
        )
        repository = FakeInfluencerRepository()
        repository.detail_result = replace(
            make_detail_record(),
            content_activity_projections=(
                make_content_activity_projection(last_publication_at=publication_at),
            ),
        )
        service = InfluencerService(repository, now_factory=lambda: NOW)

        detail = await service.get_influencer_detail(make_context(Role.VIEWER), INFLUENCER_ID)
        account = detail.platform_accounts[0]

        assert account.content_activity_state.value == "current"
        assert account.content_activity_last_publication_at == publication_at.astimezone(UTC)
        assert account.content_activity_inactive_days == 59

    asyncio.run(scenario())


def test_content_activity_no_public_or_stale_never_emits_inactivity_days() -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        record = make_detail_record()
        repository.detail_result = replace(
            record,
            content_activity_projections=(
                make_content_activity_projection(
                    trusted_result=ContentActivityResult.NO_PUBLIC_CONTENT,
                    last_publication_at=None,
                ),
            ),
        )
        service = InfluencerService(repository, now_factory=lambda: NOW)

        empty = await service.get_influencer_detail(make_context(Role.VIEWER), INFLUENCER_ID)
        empty_account = empty.platform_accounts[0]
        assert empty_account.content_activity_state.value == "current"
        assert empty_account.content_activity_inactive_days is None
        assert empty_account.content_activity_last_publication_at is None

        repository.detail_result = replace(
            record,
            content_activity_projections=(
                make_content_activity_projection(
                    trusted_observed_at=NOW - timedelta(days=8),
                ),
            ),
        )
        stale = await service.get_influencer_detail(make_context(Role.VIEWER), INFLUENCER_ID)
        stale_account = stale.platform_accounts[0]
        assert stale_account.content_activity_state.value == "stale"
        assert stale_account.content_activity_inactive_days is None
        assert stale_account.content_activity_last_publication_at is None

    asyncio.run(scenario())


def test_content_activity_later_provider_failure_is_last_known_not_current() -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        repository.detail_result = replace(
            make_detail_record(),
            content_activity_projections=(
                make_content_activity_projection(
                    latest_observed_at=NOW,
                    latest_status=ContentActivityObservationStatus.PROVIDER_ERROR,
                    latest_coverage=ContentActivityCoverageStatus.UNKNOWN,
                    latest_result=ContentActivityResult.UNDETERMINED,
                ),
            ),
        )
        service = InfluencerService(repository, now_factory=lambda: NOW)

        detail = await service.get_influencer_detail(make_context(Role.VIEWER), INFLUENCER_ID)
        account = detail.platform_accounts[0]

        assert account.content_activity_state.value == "last_known"
        assert account.content_activity_trusted_observed_at == NOW - timedelta(days=1)
        assert account.content_activity_last_publication_at is None
        assert account.content_activity_inactive_days is None
        assert account.content_activity_latest_attempt_observation_status is (
            ContentActivityObservationStatus.PROVIDER_ERROR
        )
        assert account.content_activity_latest_attempt_result is (
            ContentActivityResult.UNDETERMINED
        )

    asyncio.run(scenario())


def test_multi_account_summary_uses_worst_eligible_huitun_status() -> None:
    async def scenario() -> None:
        stale_account_id = UUID(int=ACCOUNT_ID.int + 10)
        fresh = make_freshness(observed_at=NOW - timedelta(days=1))
        stale = make_freshness(
            account_id=stale_account_id,
            observed_at=NOW - timedelta(days=31),
        )
        record = make_list_record(freshness=(fresh, stale))
        record = InfluencerListRecord(
            influencer=record.influencer,
            owner=record.owner,
            platform_accounts=(
                record.platform_accounts[0],
                make_account(account_id=stale_account_id, source=DataSource.GENERIC),
            ),
            current_metrics=record.current_metrics,
            current_contacts=record.current_contacts,
            huitun_freshness=record.huitun_freshness,
        )
        repository = FakeInfluencerRepository()
        repository.list_result = ([record], 1)
        service = InfluencerService(repository, now_factory=lambda: NOW)

        item = (
            await service.list_influencers(make_context(Role.OPERATOR), InfluencerListQuery())
        ).items[0]
        assert item.freshness_status is FreshnessStatus.STALE
        assert item.requires_refresh is True
        assert [account.freshness_status for account in item.platform_accounts] == [
            FreshnessStatus.FRESH,
            FreshnessStatus.STALE,
        ]
        assert item.platform_accounts[1].source is DataSource.GENERIC

    asyncio.run(scenario())


def test_zero_eligible_account_summary_is_unknown_but_not_refreshable() -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        repository.list_result = ([make_list_record()], 1)
        service = InfluencerService(repository, now_factory=lambda: NOW)

        item = (
            await service.list_influencers(make_context(Role.OPERATOR), InfluencerListQuery())
        ).items[0]
        assert item.freshness_status is FreshnessStatus.UNKNOWN
        assert item.requires_refresh is False
        assert item.platform_accounts[0].freshness_status is None
        assert item.platform_accounts[0].requires_refresh is False

    asyncio.run(scenario())


def test_selected_operator_effective_role_controls_contact_access() -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        repository.list_result = ([make_list_record()], 1)
        repository.detail_result = make_detail_record()
        service = InfluencerService(repository)

        viewer_context = make_context(Role.SUPER_ADMIN, operator_role=Role.VIEWER)
        viewer_page = await service.list_influencers(viewer_context, InfluencerListQuery())
        viewer_detail = await service.get_influencer_detail(viewer_context, INFLUENCER_ID)
        assert viewer_page.items[0].current_contacts[0].display_value == "***"
        assert viewer_detail.contacts[0].display_value == "***"

        operator_context = make_context(Role.SUPER_ADMIN, operator_role=Role.OPERATOR)
        operator_page = await service.list_influencers(operator_context, InfluencerListQuery())
        operator_detail = await service.get_influencer_detail(operator_context, INFLUENCER_ID)
        assert operator_page.items[0].current_contacts[0].display_value == "fixture@example.invalid"
        assert operator_detail.contacts[0].display_value == "fixture@example.invalid"

    asyncio.run(scenario())


def test_cross_department_disabled_owner_remains_visible() -> None:
    async def scenario() -> None:
        owner = make_owner(status=OperatorStatus.DISABLED)
        repository = FakeInfluencerRepository()
        repository.list_result = ([make_list_record(owner=owner)], 1)
        repository.detail_result = make_detail_record(owner=owner)
        service = InfluencerService(repository)
        context = make_context(Role.OPERATOR, department_id=uuid4())
        assert context.department.id != owner.department_id

        page = await service.list_influencers(context, InfluencerListQuery())
        detail = await service.get_influencer_detail(context, INFLUENCER_ID)

        assert page.items[0].owner is not None
        assert page.items[0].owner.id == owner.id
        assert page.items[0].owner.status == OperatorStatus.DISABLED
        assert detail.owner is not None
        assert detail.owner.id == owner.id

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.SUPER_ADMIN, "fixture@example.invalid"),
        (Role.MANAGER, "fixture@example.invalid"),
        (Role.OPERATOR, "fixture@example.invalid"),
        (Role.VIEWER, "***"),
    ],
)
def test_list_and_detail_contact_values_follow_session_role(role: Role, expected: str) -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        repository.list_result = ([make_list_record()], 1)
        repository.detail_result = make_detail_record()
        service = InfluencerService(repository)
        context = make_context(role)

        page = await service.list_influencers(context, InfluencerListQuery())
        detail = await service.get_influencer_detail(context, INFLUENCER_ID)

        assert page.items[0].current_contacts[0].display_value == expected
        assert detail.contacts[0].display_value == expected

    asyncio.run(scenario())


def test_viewer_uses_one_constant_mask_for_email_and_phone_without_length_leak() -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        values = ("fixture@example.invalid", "12345678901")
        repository.list_result = ([make_list_record(role_contact_values=values)], 1)
        repository.detail_result = make_detail_record(contact_values=values)
        service = InfluencerService(repository)
        context = make_context(Role.VIEWER)

        page = await service.list_influencers(context, InfluencerListQuery())
        detail = await service.get_influencer_detail(context, INFLUENCER_ID)

        assert [contact.display_value for contact in page.items[0].current_contacts] == [
            "***",
            "***",
        ]
        assert [contact.display_value for contact in detail.contacts] == ["***", "***"]

    asyncio.run(scenario())


def test_empty_contact_value_is_not_turned_into_a_valid_masked_contact() -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        repository.list_result = ([make_list_record(role_contact_values=("",))], 1)
        page = await InfluencerService(repository).list_influencers(
            make_context(Role.VIEWER), InfluencerListQuery()
        )
        assert page.items[0].current_contacts[0].display_value == ""

    asyncio.run(scenario())


def test_normalized_value_never_enters_public_dto_and_duplicate_is_aggregated() -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        repository.list_result = ([make_list_record(duplicate=True)], 1)
        repository.detail_result = make_detail_record()
        service = InfluencerService(repository)
        context = make_context(Role.OPERATOR)

        page = await service.list_influencers(context, InfluencerListQuery())
        detail = await service.get_influencer_detail(context, INFLUENCER_ID)

        assert page.items[0].possible_duplicate_contact is True
        assert "normalized_value" not in all_mapping_keys(page.model_dump(mode="json"))
        assert "normalized_value" not in all_mapping_keys(detail.model_dump(mode="json"))

    asyncio.run(scenario())


def test_no_contact_maps_to_empty_collection_and_false_duplicate_flag() -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        repository.list_result = (
            [make_list_record(role_contact_values=(), metric_values=())],
            1,
        )
        page = await InfluencerService(repository).list_influencers(
            make_context(Role.MANAGER), InfluencerListQuery()
        )
        assert page.items[0].current_contacts == []
        assert page.items[0].possible_duplicate_contact is False

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0),
        (100, 100),
        (None, None),
        (True, None),
        (False, None),
        (1.0, None),
        (1.5, None),
        ("100", None),
        ("1.0", None),
        (-1, None),
        ({"value": 100}, None),
        ([100], None),
    ],
)
def test_list_followers_only_accepts_real_nonnegative_python_int(
    value: object, expected: int | None
) -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        repository.list_result = ([make_list_record(metric_values=(value,))], 1)
        page = await InfluencerService(repository).list_influencers(
            make_context(Role.OPERATOR), InfluencerListQuery()
        )
        assert page.items[0].current_metrics[0].followers_count == expected

    asyncio.run(scenario())


def test_list_followers_missing_key_maps_to_none() -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        metric = make_metrics(100)
        metric.metrics = {"notes_count": 1}
        record = make_list_record(metric_values=())
        repository.list_result = (
            [
                InfluencerListRecord(
                    influencer=record.influencer,
                    owner=record.owner,
                    platform_accounts=record.platform_accounts,
                    current_metrics=(metric,),
                    current_contacts=record.current_contacts,
                )
            ],
            1,
        )

        page = await InfluencerService(repository).list_influencers(
            make_context(Role.OPERATOR), InfluencerListQuery()
        )
        assert page.items[0].current_metrics[0].followers_count is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (["动画", "电商"], ["动画", "电商"]),
        (["动画", 7, None, False, "电商"], ["动画", "电商"]),
        (None, []),
        ({"label": "动画"}, []),
        ("动画", []),
    ],
)
def test_source_state_creator_tags_are_whitelisted_and_type_filtered(
    raw_value: object, expected: list[str]
) -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        repository.detail_result = make_detail_record(creator_tags=raw_value)
        detail = await InfluencerService(repository).get_influencer_detail(
            make_context(Role.OPERATOR), INFLUENCER_ID
        )

        assert detail.source_states[0].creator_tags == expected
        assert set(detail.source_states[0].model_dump()) == {
            "platform_account_id",
            "source",
            "source_updated_at",
            "state_version",
            "creator_tags",
            "last_import_job_id",
            "last_import_row_id",
        }

    asyncio.run(scenario())


def test_source_state_missing_creator_tags_maps_to_empty_list() -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        record = make_detail_record()
        record.source_states[0].source_data = {"other": ["not-a-tag-source"]}
        repository.detail_result = record

        detail = await InfluencerService(repository).get_influencer_detail(
            make_context(Role.OPERATOR), INFLUENCER_ID
        )
        assert detail.source_states[0].creator_tags == []

    asyncio.run(scenario())


def test_detail_maps_only_frozen_sections_and_preserves_metrics_document() -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        repository.detail_result = make_detail_record()

        detail = await InfluencerService(repository).get_influencer_detail(
            make_context(Role.MANAGER), INFLUENCER_ID
        )

        assert set(type(detail).model_fields) == {
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
        assert (
            detail.current_metrics[0].metrics == repository.detail_result.current_metrics[0].metrics
        )
        assert detail.source_identities[0].external_account_id == "provider-fixture-id"

    asyncio.run(scenario())


def test_detail_and_snapshot_not_found_use_domain_error_without_http_status() -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        repository.detail_result = None
        repository.snapshot_result = None
        service = InfluencerService(repository)
        context = make_context(Role.VIEWER)

        with pytest.raises(InfluencerNotFoundError) as detail_error:
            await service.get_influencer_detail(context, INFLUENCER_ID)
        with pytest.raises(InfluencerNotFoundError) as snapshot_error:
            await service.list_metric_snapshots(context, INFLUENCER_ID, page=1, page_size=50)

        assert detail_error.value.code == "INFLUENCER_NOT_FOUND"
        assert snapshot_error.value.code == "INFLUENCER_NOT_FOUND"
        assert not hasattr(detail_error.value, "status_code")

    asyncio.run(scenario())


def test_snapshot_page_preserves_repository_page_total_and_metrics() -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        repository.snapshot_result = ([], 7)
        service = InfluencerService(repository)

        empty_page = await service.list_metric_snapshots(
            make_context(Role.OPERATOR), INFLUENCER_ID, page=3, page_size=3
        )
        assert empty_page.items == []
        assert empty_page.page == 3
        assert empty_page.page_size == 3
        assert empty_page.total == 7

        snapshot = make_snapshot()
        repository.snapshot_result = ([snapshot], 1)
        populated = await service.list_metric_snapshots(
            make_context(Role.OPERATOR), INFLUENCER_ID, page=1, page_size=50
        )
        assert populated.items[0].metrics == snapshot.metrics

    asyncio.run(scenario())


def test_filter_options_only_map_repository_values_and_real_crm_enum() -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        owner = make_owner(status=OperatorStatus.DISABLED)
        repository.owner_options = [owner]
        repository.tag_options = ["Beauty", "动画"]

        options = await InfluencerService(repository).get_filter_options(
            make_context(Role.SUPER_ADMIN)
        )

        assert set(type(options).model_fields) == {"owners", "tags", "crm_stages"}
        assert options.owners[0].id == owner.id
        assert options.owners[0].status == OperatorStatus.DISABLED
        assert options.tags == ["Beauty", "动画"]
        assert options.crm_stages == list(CRMStage)

    asyncio.run(scenario())


def test_service_has_no_mutation_methods_and_reads_do_not_write_audit() -> None:
    forbidden_methods = {
        "assign_owner",
        "update_crm_stage",
        "add_tag",
        "remove_tag",
        "add_contact",
        "update_contact",
        "delete_contact",
        "create_influencer",
        "update_influencer",
    }
    assert forbidden_methods.isdisjoint(dir(InfluencerService))

    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        repository.list_result = ([make_list_record()], 1)
        repository.detail_result = make_detail_record()
        repository.snapshot_result = ([make_snapshot()], 1)
        context = make_context(Role.OPERATOR)
        service = InfluencerService(repository)

        with patch.object(
            AuditRepository,
            "add",
            side_effect=AssertionError("read service must not create audit"),
        ) as audit_add:
            await service.list_influencers(context, InfluencerListQuery())
            await service.get_influencer_detail(context, INFLUENCER_ID)
            await service.list_metric_snapshots(context, INFLUENCER_ID, page=1, page_size=50)
            await service.get_filter_options(context)
        audit_add.assert_not_called()

    asyncio.run(scenario())


def test_dto_assembly_does_not_modify_repository_orm_objects() -> None:
    async def scenario() -> None:
        repository = FakeInfluencerRepository()
        list_record = make_list_record(duplicate=True)
        detail_record = make_detail_record()
        repository.list_result = ([list_record], 1)
        repository.detail_result = detail_record
        before = {
            "contact_value": list_record.current_contacts[0].value,
            "contact_normalized": list_record.current_contacts[0].normalized_value,
            "list_metrics": deepcopy(list_record.current_metrics[0].metrics),
            "account_tags": deepcopy(list_record.platform_accounts[0].source_tags),
            "source_data": deepcopy(detail_record.source_states[0].source_data),
            "detail_metrics": deepcopy(detail_record.current_metrics[0].metrics),
        }
        service = InfluencerService(repository)
        context = make_context(Role.VIEWER)

        await service.list_influencers(context, InfluencerListQuery())
        await service.get_influencer_detail(context, INFLUENCER_ID)

        assert list_record.current_contacts[0].value == before["contact_value"]
        assert list_record.current_contacts[0].normalized_value == before["contact_normalized"]
        assert list_record.current_metrics[0].metrics == before["list_metrics"]
        assert list_record.platform_accounts[0].source_tags == before["account_tags"]
        assert detail_record.source_states[0].source_data == before["source_data"]
        assert detail_record.current_metrics[0].metrics == before["detail_metrics"]

    asyncio.run(scenario())

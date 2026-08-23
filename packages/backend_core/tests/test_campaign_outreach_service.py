"""Focused domain tests for Phase 3A Campaign and Outreach services.

SQLite exercises service-level contracts and transaction ownership through a
minimal compatible table set. PostgreSQL-specific row-lock races remain covered
by the opt-in integration test module.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from backend_core.audit.enums import AuditAction
from backend_core.audit.models import AuditLog
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, Operator
from backend_core.auth.service import AuthContext
from backend_core.campaigns.channels import StaticChannelEnablementRegistry
from backend_core.campaigns.errors import CampaignOutreachError
from backend_core.campaigns.repository import CampaignMemberProjectionRecord
from backend_core.campaigns.schemas import (
    CampaignCreateInput,
    CampaignCursor,
    CampaignMemberAddItem,
    CampaignMemberBulkAddInput,
    CampaignMemberBulkAddResult,
    CampaignMemberFromCandidateRunBulkAddInput,
    CampaignMemberRemoveInput,
    CampaignMemberResult,
    CampaignStatusTransitionInput,
    CampaignUpdateInput,
)
from backend_core.campaigns.service import CampaignService
from backend_core.config.settings import Settings
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.growth.enums import (
    CampaignReviewMode,
    CampaignStatus,
    CandidatePoolKind,
    CandidatePoolRunStatus,
    CandidatePoolStatus,
    CandidateResult,
    DuplicateHistoryPolicy,
    Phase3AOperationScope,
)
from backend_core.growth.idempotency import canonical_request_hash
from backend_core.growth.models import (
    Campaign,
    CampaignMember,
    CandidatePool,
    CandidatePoolMember,
    CandidatePoolRun,
    Phase3AIdempotencyRecord,
    TargetingPolicy,
)
from backend_core.influencers.enums import (
    ContactType,
    ContactValidationStatus,
    CRMStage,
    DataSource,
    InfluencerStatus,
    Platform,
)
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerPlatformAccount,
)
from backend_core.influencers.schemas import (
    InfluencerIdentitySummary,
    PlatformAccountIdentitySummary,
)
from backend_core.outreach.enums import (
    OutreachChannel,
    OutreachEventType,
    OutreachPriority,
    OutreachPrioritySource,
    OutreachTaskState,
)
from backend_core.outreach.models import OutreachEvent, OutreachTarget, OutreachTask
from backend_core.outreach.schemas import (
    HistoryWarning,
    OutreachTargetCreateInput,
    OutreachTaskCreateInput,
    OutreachTaskTransitionInput,
)
from backend_core.outreach.service import OutreachService
from pydantic import SecretStr, ValidationError
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

SQLITE_TABLES = (
    "departments",
    "operators",
    "influencers",
    "influencer_platform_accounts",
    "influencer_contacts",
    "candidate_pools",
    "targeting_policies",
    "candidate_pool_runs",
    "candidate_pool_members",
    "campaigns",
    "campaign_members",
    "outreach_targets",
    "outreach_tasks",
    "outreach_events",
    "phase3a_idempotency_records",
    "audit_logs",
)

OUTREACH_FINGERPRINT_TEST_KEY = "phase3a-outreach-fingerprint-test-key"
OUTREACH_FINGERPRINT_TEST_SETTINGS = Settings(
    app_env="test",
    app_master_key=SecretStr(OUTREACH_FINGERPRINT_TEST_KEY),
    _env_file=None,
)


def test_endpoint_fingerprint_is_keyed_deterministic_and_nonplaintext() -> None:
    kind = "contact:email"
    endpoint = "creator@example.invalid"
    signing_key = OUTREACH_FINGERPRINT_TEST_KEY.encode("utf-8")

    fingerprint = OutreachService._endpoint_fingerprint(signing_key, kind, endpoint)

    assert fingerprint == OutreachService._endpoint_fingerprint(signing_key, kind, endpoint)
    assert fingerprint != OutreachService._endpoint_fingerprint(
        signing_key,
        kind,
        "other-creator@example.invalid",
    )
    assert (
        fingerprint
        == hmac.new(
            signing_key,
            b"phase3a:outreach:endpoint-fingerprint:v1\x1fcontact:email\x1f"
            b"creator@example.invalid",
            hashlib.sha256,
        ).hexdigest()
    )
    assert (
        fingerprint
        != hashlib.sha256(b"phase3a:endpoint:v1:contact:email:creator@example.invalid").hexdigest()
    )
    assert endpoint not in fingerprint


@pytest.mark.parametrize("master_key", (None, "", " \t\n "))
def test_endpoint_fingerprint_rejects_blank_production_master_key(master_key: str | None) -> None:
    settings = Settings.model_construct(
        app_env="production",
        app_master_key=SecretStr(master_key) if master_key is not None else None,
    )

    with pytest.raises(ValueError, match="APP_MASTER_KEY.*non-blank"):
        OutreachService._endpoint_fingerprint_signing_key(settings)


def async_test(function: object) -> object:
    """Run an async scenario without adding a project-wide pytest plugin."""

    @wraps(function)
    def wrapper() -> None:
        asyncio.run(function())  # type: ignore[operator]

    return wrapper


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 18, 4, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current


@dataclass(slots=True)
class DomainHarness:
    session: AsyncSession
    context: AuthContext
    campaign_service: CampaignService
    outreach_service: OutreachService
    clock: MutableClock


@dataclass(frozen=True, slots=True)
class InfluencerFixture:
    """Stable identifiers retained across rollback-driven ORM expiry."""

    influencer_id: UUID
    account_id: UUID
    contact_id: UUID


@dataclass(frozen=True, slots=True)
class CandidateRunFixture:
    run_id: UUID
    member_id: UUID | None


@asynccontextmanager
async def domain_harness(
    *,
    enabled_channels: frozenset[OutreachChannel] | None = None,
) -> AsyncIterator[DomainHarness]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        for table_name in SQLITE_TABLES:
            await connection.run_sync(
                lambda sync_connection, name=table_name: Base.metadata.tables[name].create(
                    sync_connection
                )
            )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            context = await seed_context(session)
            await session.commit()
            clock = MutableClock()
            registry = StaticChannelEnablementRegistry(
                enabled_channels
                if enabled_channels is not None
                else frozenset(
                    {
                        OutreachChannel.EMAIL,
                        OutreachChannel.WECHAT,
                        OutreachChannel.XIAOHONGSHU_PRIVATE_MESSAGE,
                        OutreachChannel.MANUAL,
                    }
                )
            )
            yield DomainHarness(
                session=session,
                context=context,
                campaign_service=CampaignService(
                    session,
                    channel_registry=registry,
                    clock=clock,
                ),
                outreach_service=OutreachService(
                    session,
                    channel_registry=registry,
                    clock=clock,
                    settings=OUTREACH_FINGERPRINT_TEST_SETTINGS,
                ),
                clock=clock,
            )
    finally:
        await engine.dispose()


async def seed_context(session: AsyncSession, *, role: Role = Role.OPERATOR) -> AuthContext:
    department = Department(
        name=f"Campaign Outreach {uuid4().hex}",
        password_hash="not-used-by-domain-test",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    session.add(department)
    await session.flush()
    operator = Operator(
        department_id=department.id,
        name="Campaign Outreach Operator",
        role=Role.OPERATOR,
        status=OperatorStatus.ACTIVE,
    )
    session.add(operator)
    await session.flush()
    auth_session = AuthSession(
        department_id=department.id,
        operator_id=operator.id,
        token_hash=uuid4().hex + uuid4().hex,
        csrf_token_hash=uuid4().hex + uuid4().hex,
        ip="127.0.0.1",
        user_agent="campaign-outreach-domain-test",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        revoked_at=None,
    )
    return AuthContext(
        department=department,
        operator=operator,
        role=role,
        auth_session=auth_session,
    )


async def seed_influencer(
    session: AsyncSession,
    context: AuthContext,
    *,
    contact_type: ContactType = ContactType.EMAIL,
    contact_status: ContactValidationStatus = ContactValidationStatus.VALID,
    contact_value: str | None = None,
) -> InfluencerFixture:
    assert context.operator is not None
    influencer = Influencer(
        display_name=f"Influencer {uuid4().hex}",
        owner_operator_id=context.operator.id,
        crm_stage=CRMStage.TO_DEVELOP,
        status=InfluencerStatus.ACTIVE,
        deleted_at=None,
    )
    session.add(influencer)
    await session.flush()
    account = InfluencerPlatformAccount(
        influencer_id=influencer.id,
        platform=Platform.XIAOHONGSHU,
        platform_account_id=uuid4().hex,
        account_name=f"Account {uuid4().hex}",
        source=DataSource.MANUAL,
        is_active=True,
    )
    session.add(account)
    await session.flush()
    now = datetime.now(UTC)
    value = contact_value or f"{uuid4().hex}@example.invalid"
    contact = InfluencerContact(
        influencer_id=influencer.id,
        platform_account_id=None,
        type=contact_type,
        value=value,
        normalized_value=value.lower(),
        source=DataSource.MANUAL,
        validation_status=contact_status,
        is_current=True,
        possible_duplicate_contact=False,
        first_seen_at=now,
        last_seen_at=now,
        source_updated_at=None,
        first_import_job_id=None,
        first_import_row_id=None,
        last_import_job_id=None,
        last_import_row_id=None,
    )
    session.add(contact)
    await session.flush()
    return InfluencerFixture(
        influencer_id=influencer.id,
        account_id=account.id,
        contact_id=contact.id,
    )


async def refresh_context_after_rollback(harness: DomainHarness) -> None:
    """Reload the auth fields read by services after rollback-driven ORM expiry."""

    await harness.session.refresh(harness.context.department)
    if harness.context.operator is not None:
        await harness.session.refresh(harness.context.operator)


async def seed_candidate_pool_run(
    harness: DomainHarness,
    fixture: InfluencerFixture,
    *,
    status: CandidatePoolRunStatus,
    include_member: bool,
    member_result: CandidateResult = CandidateResult.MATCH,
) -> CandidateRunFixture:
    """Seed only the persisted run provenance required by Campaign member tests."""

    assert harness.context.operator is not None
    pool = CandidatePool(
        department_id=harness.context.department.id,
        owner_operator_id=harness.context.operator.id,
        name=f"Pool {uuid4().hex}",
        kind=CandidatePoolKind.POTENTIAL_SELLER,
        source_collection_job_id=None,
        status=CandidatePoolStatus.ACTIVE,
        current_policy_id=None,
        version=1,
    )
    harness.session.add(pool)
    await harness.session.flush()
    policy = TargetingPolicy(
        pool_id=pool.id,
        version=1,
        schema_version=1,
        definition={},
        canonical_hash="0" * 64,
        created_by_operator_id=harness.context.operator.id,
    )
    harness.session.add(policy)
    await harness.session.flush()
    run = CandidatePoolRun(
        pool_id=pool.id,
        policy_id=policy.id,
        as_of=harness.clock.now(),
        input_watermark=None,
        status=status,
        match_count=1 if include_member and member_result is CandidateResult.MATCH else 0,
        unknown_count=1 if include_member and member_result is CandidateResult.UNKNOWN else 0,
        not_match_count=0,
        error_code=None,
        error_message=None,
        idempotency_key=f"run-{uuid4().hex}",
        request_hash="0" * 64,
    )
    harness.session.add(run)
    await harness.session.flush()
    member_id: UUID | None = None
    if include_member:
        member_id = await add_candidate_pool_member(
            harness,
            run_id=run.id,
            fixture=fixture,
            result=member_result,
        )
    return CandidateRunFixture(run_id=run.id, member_id=member_id)


async def add_candidate_pool_member(
    harness: DomainHarness,
    *,
    run_id: UUID,
    fixture: InfluencerFixture,
    result: CandidateResult,
    platform_account_id: UUID | None = None,
) -> UUID:
    member = CandidatePoolMember(
        run_id=run_id,
        influencer_id=fixture.influencer_id,
        platform_account_id=platform_account_id or fixture.account_id,
        result=result,
        reason_codes=[],
        redacted_evidence={},
        evidence_hash="0" * 64,
    )
    harness.session.add(member)
    await harness.session.flush()
    return member.id


async def create_campaign(harness: DomainHarness, *, name: str = "Campaign") -> UUID:
    result = await harness.campaign_service.create_campaign(
        harness.context,
        CampaignCreateInput(name=name),
        idempotency_key=f"campaign-{uuid4().hex}",
        ip="127.0.0.1",
        user_agent="campaign-outreach-domain-test",
    )
    return result.result.id


async def add_member(
    harness: DomainHarness,
    campaign_id: UUID,
    fixture: InfluencerFixture,
) -> CampaignMember:
    await harness.campaign_service.bulk_add_members(
        harness.context,
        campaign_id,
        CampaignMemberBulkAddInput(
            members=(
                CampaignMemberAddItem(
                    influencer_id=fixture.influencer_id,
                    preferred_platform_account_id=fixture.account_id,
                ),
            )
        ),
        idempotency_key=f"member-{uuid4().hex}",
        ip="127.0.0.1",
        user_agent="campaign-outreach-domain-test",
    )
    members = await harness.campaign_service.repository.list_members_for_influencers(
        campaign_id,
        (fixture.influencer_id,),
    )
    assert len(members) == 1
    return members[0]


async def create_email_target(
    harness: DomainHarness,
    campaign_id: UUID,
    member_id: UUID,
    fixture: InfluencerFixture,
) -> OutreachTarget:
    result = await harness.outreach_service.create_target(
        harness.context,
        campaign_id,
        OutreachTargetCreateInput(
            member_id=member_id,
            influencer_id=fixture.influencer_id,
            channel=OutreachChannel.EMAIL,
            contact_id=fixture.contact_id,
        ),
        idempotency_key=f"target-{uuid4().hex}",
        ip="127.0.0.1",
        user_agent="campaign-outreach-domain-test",
    )
    target = await harness.outreach_service.repository.get_target(
        result.result.id,
        department_id=harness.context.department.id,
    )
    assert target is not None
    return target


async def activate_campaign(harness: DomainHarness, campaign_id: UUID) -> None:
    await harness.campaign_service.transition_campaign_status(
        harness.context,
        campaign_id,
        CampaignStatusTransitionInput(
            to_status=CampaignStatus.ACTIVE,
            expected_version=1,
        ),
        ip="127.0.0.1",
        user_agent="campaign-outreach-domain-test",
    )


async def count_rows(session: AsyncSession, model: type[object]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def count_audits(session: AsyncSession, action: AuditAction) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == action)
        )
        or 0
    )


def test_history_warning_never_contains_cross_department_entity_ids() -> None:
    warning = HistoryWarning(
        channel=OutreachChannel.EMAIL,
        last_sent_at=datetime(2026, 8, 18, 4, 0, tzinfo=UTC),
    )
    assert set(warning.model_dump()) == {"channel", "last_sent_at"}
    with pytest.raises(ValidationError):
        HistoryWarning.model_validate(
            {
                "campaign_id": str(uuid4()),
                "task_id": str(uuid4()),
                "channel": OutreachChannel.EMAIL,
                "last_sent_at": datetime(2026, 8, 18, 4, 0, tzinfo=UTC),
            }
        )


def test_task_create_input_requires_timezone_aware_due_at() -> None:
    with pytest.raises(ValidationError):
        OutreachTaskCreateInput()
    with pytest.raises(ValidationError):
        OutreachTaskCreateInput(due_at=datetime(2026, 8, 18, 12, 0))


def test_canonical_member_identity_summaries_keep_required_nullable_contracts() -> None:
    influencer = InfluencerIdentitySummary.model_validate(
        {"id": uuid4(), "display_name": "Identity", "status": "active"}
    )
    account = PlatformAccountIdentitySummary.model_validate(
        {
            "id": uuid4(),
            "platform": "xiaohongshu",
            "platform_account_id": None,
            "account_name": "Account identity",
            "account_handle": None,
            "is_active": False,
        }
    )
    assert influencer.status is InfluencerStatus.ACTIVE
    assert account.platform is Platform.XIAOHONGSHU
    assert account.platform_account_id is None
    assert account.account_handle is None
    with pytest.raises(ValidationError):
        InfluencerIdentitySummary.model_validate({"id": uuid4(), "display_name": "Identity"})
    with pytest.raises(ValidationError):
        PlatformAccountIdentitySummary.model_validate(
            {
                "id": uuid4(),
                "platform": "xiaohongshu",
                "platform_account_id": None,
                "account_name": None,
                "account_handle": None,
                "is_active": True,
            }
        )


@async_test
async def test_campaign_member_display_projection_is_joined_and_retains_historical_identity() -> (
    None
):
    async with domain_harness() as harness:
        first_fixture = await seed_influencer(harness.session, harness.context)
        second_fixture = await seed_influencer(harness.session, harness.context)
        await harness.session.commit()
        campaign_id = await create_campaign(harness)
        first_member = await add_member(harness, campaign_id, first_fixture)
        await add_member(harness, campaign_id, second_fixture)

        influencer = await harness.session.get(Influencer, first_fixture.influencer_id)
        account = await harness.session.get(InfluencerPlatformAccount, first_fixture.account_id)
        assert influencer is not None and account is not None
        influencer.status = InfluencerStatus.DISABLED
        influencer.deleted_at = harness.clock.now()
        account.is_active = False
        await harness.session.commit()

        statements: list[str] = []
        assert harness.session.bind is not None

        def capture(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: object,
        ) -> None:
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(harness.session.bind.sync_engine, "before_cursor_execute", capture)
        try:
            page = await harness.campaign_service.list_members(
                harness.context,
                campaign_id,
                cursor=None,
                limit=50,
            )
        finally:
            event.remove(harness.session.bind.sync_engine, "before_cursor_execute", capture)

        projected = next(item for item in page.items if item.id == first_member.id)
        assert len(page.items) == 2
        assert projected.influencer.id == projected.influencer_id == first_fixture.influencer_id
        assert projected.influencer.display_name == influencer.display_name
        assert projected.influencer.status is InfluencerStatus.DISABLED
        assert projected.preferred_platform_account.id == first_fixture.account_id
        assert projected.preferred_platform_account.is_active is False
        assert projected.is_active is True
        assert "contacts" not in projected.model_dump()
        assert len(statements) == 2
        joined_statement = next(
            statement for statement in statements if "campaign_members" in statement
        )
        assert "JOIN campaigns" in joined_statement
        assert "JOIN influencers" in joined_statement
        assert "JOIN influencer_platform_accounts" in joined_statement
        assert "influencer_contacts" not in joined_statement

        detail = await harness.campaign_service.get_member(
            harness.context,
            campaign_id,
            first_member.id,
        )
        assert detail == projected

        removed = await harness.campaign_service.remove_member(
            harness.context,
            campaign_id,
            first_member.id,
            CampaignMemberRemoveInput(expected_version=detail.version),
            ip="127.0.0.1",
            user_agent="campaign-member-display-test",
        )
        assert removed.removed_at is not None
        assert removed.is_active is False
        assert removed.influencer.status is InfluencerStatus.DISABLED
        assert removed.preferred_platform_account.is_active is False
        removed_detail = await harness.campaign_service.get_member(
            harness.context,
            campaign_id,
            first_member.id,
        )
        assert removed_detail == removed
        with pytest.raises(CampaignOutreachError) as stale_remove:
            await harness.campaign_service.remove_member(
                harness.context,
                campaign_id,
                first_member.id,
                CampaignMemberRemoveInput(expected_version=detail.version),
                ip="127.0.0.1",
                user_agent="campaign-member-display-test",
            )
        assert stale_remove.value.code == "VERSION_CONFLICT"

        influencer.status = InfluencerStatus.ACTIVE
        influencer.deleted_at = None
        account.is_active = True
        await harness.session.commit()
        assert harness.context.operator is not None
        await harness.session.refresh(harness.context.operator)
        await harness.session.refresh(harness.context.department)

        restored = await harness.campaign_service.bulk_add_members(
            harness.context,
            campaign_id,
            CampaignMemberBulkAddInput(
                members=(
                    CampaignMemberAddItem(
                        influencer_id=first_fixture.influencer_id,
                        preferred_platform_account_id=first_fixture.account_id,
                    ),
                )
            ),
            idempotency_key="member-display-restore",
            ip="127.0.0.1",
            user_agent="campaign-member-display-test",
        )
        restored_detail = await harness.campaign_service.get_member(
            harness.context,
            campaign_id,
            first_member.id,
        )
        assert restored.result.restored_count == 1
        assert restored_detail.id == first_member.id
        assert restored_detail.created_at == detail.created_at
        assert restored_detail.version == removed.version + 1
        assert restored_detail.is_active is True
        assert restored_detail.influencer.status is InfluencerStatus.ACTIVE
        assert restored_detail.preferred_platform_account.is_active is True

        member_model = await harness.session.get(CampaignMember, first_member.id)
        other_account = await harness.session.get(
            InfluencerPlatformAccount, second_fixture.account_id
        )
        assert member_model is not None and other_account is not None
        invalid_projection = CampaignMemberProjectionRecord(
            member=member_model,
            influencer=influencer,
            preferred_platform_account=other_account,
        )
        with pytest.raises(CampaignOutreachError) as invalid_relation:
            harness.campaign_service._member_result_from_projection(invalid_projection)
        assert invalid_relation.value.code == "CAMPAIGN_MEMBER_PROJECTION_INVALID"

        invalid_influencer = {
            **restored_detail.model_dump(),
            "influencer": {**restored_detail.influencer.model_dump(), "id": uuid4()},
        }
        with pytest.raises(ValidationError):
            CampaignMemberResult.model_validate(invalid_influencer)
        with pytest.raises(ValidationError):
            CampaignMemberResult.model_validate(
                {**restored_detail.model_dump(), "is_active": False}
            )


@async_test
async def test_campaign_member_projection_keeps_read_scope_and_viewer_boundary() -> None:
    async with domain_harness() as harness:
        fixture = await seed_influencer(harness.session, harness.context)
        await harness.session.commit()
        campaign_id = await create_campaign(harness)
        member = await add_member(harness, campaign_id, fixture)
        viewer = AuthContext(
            department=harness.context.department,
            operator=None,
            role=Role.VIEWER,
            auth_session=harness.context.auth_session,
        )

        page = await harness.campaign_service.list_members(
            viewer,
            campaign_id,
            cursor=None,
            limit=50,
        )
        assert [item.id for item in page.items] == [member.id]
        assert (
            await harness.campaign_service.get_member(viewer, campaign_id, member.id)
        ).influencer.id == fixture.influencer_id
        with pytest.raises(CampaignOutreachError) as viewer_mutation:
            await harness.campaign_service.remove_member(
                viewer,
                campaign_id,
                member.id,
                CampaignMemberRemoveInput(expected_version=member.version),
                ip="127.0.0.1",
                user_agent="campaign-member-display-test",
            )
        assert viewer_mutation.value.status_code == 403
        assert viewer_mutation.value.code == "PERMISSION_DENIED"

        other_context = await seed_context(harness.session)
        await harness.session.commit()
        with pytest.raises(CampaignOutreachError) as hidden_list:
            await harness.campaign_service.list_members(
                other_context,
                campaign_id,
                cursor=None,
                limit=50,
                department_id=harness.context.department.id,
            )
        assert hidden_list.value.status_code == 404
        assert hidden_list.value.code == "RESOURCE_NOT_FOUND"
        with pytest.raises(CampaignOutreachError) as hidden_detail:
            await harness.campaign_service.get_member(
                other_context,
                campaign_id,
                member.id,
                department_id=harness.context.department.id,
            )
        assert hidden_detail.value.status_code == 404
        assert hidden_detail.value.code == "RESOURCE_NOT_FOUND"


@async_test
async def test_campaign_create_shared_idempotency_replays_and_conflicts_without_second_audit() -> (
    None
):
    async with domain_harness() as harness:
        first = await harness.campaign_service.create_campaign(
            harness.context,
            CampaignCreateInput(name="Durable replay"),
            idempotency_key="campaign-create-key",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        replay = await harness.campaign_service.create_campaign(
            harness.context,
            CampaignCreateInput(name="Durable replay"),
            idempotency_key="campaign-create-key",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )

        assert first.status_code == replay.status_code == 201
        assert not first.replayed and replay.replayed
        assert replay.result == first.result
        assert await count_rows(harness.session, Campaign) == 1
        assert await count_rows(harness.session, Phase3AIdempotencyRecord) == 1
        assert await count_audits(harness.session, AuditAction.CAMPAIGN_CREATED) == 1

        with pytest.raises(CampaignOutreachError) as error:
            await harness.campaign_service.create_campaign(
                harness.context,
                CampaignCreateInput(name="Different semantic request"),
                idempotency_key="campaign-create-key",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert error.value.status_code == 409
        assert error.value.code == "IDEMPOTENCY_KEY_REUSED"
        assert await count_rows(harness.session, Campaign) == 1
        assert await count_audits(harness.session, AuditAction.CAMPAIGN_CREATED) == 1


@async_test
async def test_campaign_create_replay_enriches_owner_state_after_initial_success() -> None:
    async with domain_harness() as harness:
        assert harness.context.operator is not None
        owner = Operator(
            department_id=harness.context.department.id,
            name=f"Campaign Owner {uuid4().hex}",
            role=Role.OPERATOR,
            status=OperatorStatus.ACTIVE,
        )
        harness.session.add(owner)
        await harness.session.flush()
        request = CampaignCreateInput(
            name="Owner-state-independent replay",
            owner_operator_id=owner.id,
        )
        first = await harness.campaign_service.create_campaign(
            harness.context,
            request,
            idempotency_key="campaign-owner-state-key",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )

        record = await harness.session.scalar(
            select(Phase3AIdempotencyRecord).where(
                Phase3AIdempotencyRecord.operation_scope == Phase3AOperationScope.CAMPAIGN_CREATE,
                Phase3AIdempotencyRecord.idempotency_key == "campaign-owner-state-key",
            )
        )
        assert record is not None
        record.result_payload = {
            key: value for key, value in record.result_payload.items() if key != "owner"
        }

        owner.status = OperatorStatus.DISABLED
        await harness.session.commit()

        replay = await harness.campaign_service.create_campaign(
            harness.context,
            request,
            idempotency_key="campaign-owner-state-key",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert replay.replayed
        assert replay.result.model_dump(exclude={"owner"}) == first.result.model_dump(
            exclude={"owner"}
        )
        assert replay.result.owner.id == owner.id
        assert replay.result.owner.name == owner.name
        assert replay.result.owner.status is OperatorStatus.DISABLED
        assert await count_rows(harness.session, Campaign) == 1
        assert await count_audits(harness.session, AuditAction.CAMPAIGN_CREATED) == 1


@async_test
async def test_campaign_owner_projection_and_disabled_owner_full_put_preservation() -> None:
    async with domain_harness() as harness:
        created = await harness.campaign_service.create_campaign(
            harness.context,
            CampaignCreateInput(name="Campaign owner projection"),
            idempotency_key="campaign-owner-projection",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert harness.context.operator is not None
        assert created.result.owner.id == created.result.owner_operator_id
        assert created.result.owner.name == harness.context.operator.name
        assert created.result.owner.status is OperatorStatus.ACTIVE

        detail = await harness.campaign_service.get_campaign(harness.context, created.result.id)
        listed = await harness.campaign_service.list_campaigns(
            harness.context,
            cursor=None,
            limit=50,
        )
        assert detail.owner == created.result.owner
        assert listed.items[0].owner == created.result.owner

        harness.context.operator.status = OperatorStatus.DISABLED
        await harness.session.commit()

        disabled_detail = await harness.campaign_service.get_campaign(
            harness.context,
            created.result.id,
        )
        disabled_list = await harness.campaign_service.list_campaigns(
            harness.context,
            cursor=None,
            limit=50,
        )
        assert disabled_detail.owner.name == harness.context.operator.name
        assert disabled_detail.owner.status is OperatorStatus.DISABLED
        assert disabled_list.items[0].owner.status is OperatorStatus.DISABLED

        preserved = await harness.campaign_service.update_campaign(
            harness.context,
            created.result.id,
            CampaignUpdateInput(
                name="Campaign owner projection renamed",
                owner_operator_id=created.result.owner_operator_id,
                review_mode=created.result.review_mode,
                review_count=created.result.review_count,
                duplicate_history_policy=created.result.duplicate_history_policy,
                duplicate_window_days=created.result.duplicate_window_days,
                expected_version=created.result.version,
            ),
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert preserved.owner.id == created.result.owner_operator_id
        assert preserved.owner.status is OperatorStatus.DISABLED


@async_test
async def test_campaign_list_owner_projection_uses_one_joined_query() -> None:
    async with domain_harness() as harness:
        await create_campaign(harness, name="First owner projection")
        await create_campaign(harness, name="Second owner projection")
        statements: list[str] = []
        assert harness.session.bind is not None

        def capture(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: object,
        ) -> None:
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(harness.session.bind.sync_engine, "before_cursor_execute", capture)
        try:
            page = await harness.campaign_service.list_campaigns(
                harness.context,
                cursor=None,
                limit=50,
            )
        finally:
            event.remove(harness.session.bind.sync_engine, "before_cursor_execute", capture)

        assert len(page.items) == 2
        assert len(statements) == 1
        assert "JOIN operators" in statements[0]


@async_test
async def test_campaign_changed_owner_must_be_active_in_the_resolved_department() -> None:
    async with domain_harness() as harness:
        campaign_id = await create_campaign(harness)
        campaign = await harness.campaign_service.get_campaign(harness.context, campaign_id)
        disabled_owner = Operator(
            department_id=harness.context.department.id,
            name=f"Disabled Campaign Owner {uuid4().hex}",
            role=Role.OPERATOR,
            status=OperatorStatus.DISABLED,
        )
        active_owner = Operator(
            department_id=harness.context.department.id,
            name=f"Active Campaign Owner {uuid4().hex}",
            role=Role.OPERATOR,
            status=OperatorStatus.ACTIVE,
        )
        harness.session.add_all((disabled_owner, active_owner))
        await harness.session.commit()
        disabled_owner_id = disabled_owner.id
        active_owner_id = active_owner.id

        def update(owner_id: UUID, expected_version: int) -> CampaignUpdateInput:
            return CampaignUpdateInput(
                name="Changed Campaign Owner",
                owner_operator_id=owner_id,
                review_mode=campaign.review_mode,
                review_count=campaign.review_count,
                duplicate_history_policy=campaign.duplicate_history_policy,
                duplicate_window_days=campaign.duplicate_window_days,
                expected_version=expected_version,
            )

        with pytest.raises(CampaignOutreachError) as disabled:
            await harness.campaign_service.update_campaign(
                harness.context,
                campaign_id,
                update(disabled_owner_id, campaign.version),
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert disabled.value.code == "OPERATOR_NOT_FOUND"
        await refresh_context_after_rollback(harness)

        changed = await harness.campaign_service.update_campaign(
            harness.context,
            campaign_id,
            update(active_owner_id, campaign.version),
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert changed.owner.id == active_owner_id
        assert changed.owner.status is OperatorStatus.ACTIVE


@async_test
async def test_campaign_full_put_keeps_matching_version_closed_semantics() -> None:
    async with domain_harness() as harness:
        campaign_id = await create_campaign(harness)
        campaign = await harness.campaign_service.get_campaign(harness.context, campaign_id)
        stored = await harness.session.get(Campaign, campaign_id)
        assert stored is not None
        stored.status = CampaignStatus.CLOSED
        await harness.session.commit()

        with pytest.raises(CampaignOutreachError) as closed:
            await harness.campaign_service.update_campaign(
                harness.context,
                campaign_id,
                CampaignUpdateInput(
                    name="Closed Campaign",
                    owner_operator_id=campaign.owner_operator_id,
                    review_mode=campaign.review_mode,
                    review_count=campaign.review_count,
                    duplicate_history_policy=campaign.duplicate_history_policy,
                    duplicate_window_days=campaign.duplicate_window_days,
                    expected_version=campaign.version,
                ),
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert closed.value.code == "CAMPAIGN_CLOSED"


@async_test
async def test_super_admin_cross_department_campaign_owner_projection_and_validation() -> None:
    async with domain_harness() as harness:
        assert harness.context.operator is not None
        target_department = Department(
            name=f"Target Campaign Department {uuid4().hex}",
            password_hash="not-used-by-domain-test",
            status=DepartmentStatus.ACTIVE,
            session_days=30,
        )
        third_department = Department(
            name=f"Third Campaign Department {uuid4().hex}",
            password_hash="not-used-by-domain-test",
            status=DepartmentStatus.ACTIVE,
            session_days=30,
        )
        harness.session.add_all((target_department, third_department))
        await harness.session.flush()
        target_owner = Operator(
            department_id=target_department.id,
            name="Target Campaign Owner",
            role=Role.OPERATOR,
            status=OperatorStatus.ACTIVE,
        )
        replacement_owner = Operator(
            department_id=target_department.id,
            name="Target Campaign Replacement",
            role=Role.OPERATOR,
            status=OperatorStatus.ACTIVE,
        )
        third_owner = Operator(
            department_id=third_department.id,
            name="Third Campaign Owner",
            role=Role.OPERATOR,
            status=OperatorStatus.ACTIVE,
        )
        harness.session.add_all((target_owner, replacement_owner, third_owner))
        await harness.session.commit()
        target_department_id = target_department.id
        target_owner_id = target_owner.id
        replacement_owner_id = replacement_owner.id
        third_owner_id = third_owner.id
        super_context = AuthContext(
            department=harness.context.department,
            operator=harness.context.operator,
            role=Role.SUPER_ADMIN,
            auth_session=harness.context.auth_session,
        )
        created = await harness.campaign_service.create_campaign(
            super_context,
            CampaignCreateInput(
                department_id=target_department_id,
                name="Cross-department Campaign",
                owner_operator_id=target_owner_id,
            ),
            idempotency_key="cross-department-owner-projection",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert created.result.owner.id == target_owner_id
        assert created.result.owner.name == target_owner.name
        detail = await harness.campaign_service.get_campaign(
            super_context,
            created.result.id,
            department_id=target_department_id,
        )
        assert detail.owner == created.result.owner

        def update(owner_id: UUID) -> CampaignUpdateInput:
            return CampaignUpdateInput(
                name="Cross-department Campaign changed",
                owner_operator_id=owner_id,
                review_mode=created.result.review_mode,
                review_count=created.result.review_count,
                duplicate_history_policy=created.result.duplicate_history_policy,
                duplicate_window_days=created.result.duplicate_window_days,
                expected_version=created.result.version,
                department_id=target_department_id,
            )

        for wrong_owner in (harness.context.operator.id, third_owner_id):
            with pytest.raises(CampaignOutreachError) as rejected:
                await harness.campaign_service.update_campaign(
                    super_context,
                    created.result.id,
                    update(wrong_owner),
                    ip="127.0.0.1",
                    user_agent="campaign-outreach-domain-test",
                )
            assert rejected.value.code == "OPERATOR_NOT_FOUND"
            await refresh_context_after_rollback(harness)

        changed = await harness.campaign_service.update_campaign(
            super_context,
            created.result.id,
            update(replacement_owner_id),
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert changed.owner.id == replacement_owner_id


@async_test
async def test_non_super_cross_department_mutations_do_not_disclose_scope() -> None:
    async with domain_harness() as harness:
        with pytest.raises(CampaignOutreachError) as error:
            await harness.campaign_service.create_campaign(
                harness.context,
                CampaignCreateInput(
                    department_id=uuid4(),
                    name="Cross-department Campaign",
                ),
                idempotency_key="cross-department-campaign",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert error.value.status_code == 404
        assert error.value.code == "RESOURCE_NOT_FOUND"
        assert await count_rows(harness.session, Campaign) == 0
        assert await count_rows(harness.session, Phase3AIdempotencyRecord) == 0


@async_test
async def test_bulk_member_replay_restore_noop_and_bounded_payload() -> None:
    async with domain_harness() as harness:
        fixture = await seed_influencer(harness.session, harness.context)
        await harness.session.commit()
        campaign_id = await create_campaign(harness)
        add_input = CampaignMemberBulkAddInput(
            members=(
                CampaignMemberAddItem(
                    influencer_id=fixture.influencer_id,
                    preferred_platform_account_id=fixture.account_id,
                ),
            )
        )
        added = await harness.campaign_service.bulk_add_members(
            harness.context,
            campaign_id,
            add_input,
            idempotency_key="member-add-key",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        replay = await harness.campaign_service.bulk_add_members(
            harness.context,
            campaign_id,
            add_input,
            idempotency_key="member-add-key",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert added.result.added_count == 1
        assert replay.replayed
        assert replay.result == added.result
        assert await count_audits(harness.session, AuditAction.CAMPAIGN_MEMBERS_ADDED) == 1

        members = await harness.campaign_service.repository.list_members_for_influencers(
            campaign_id,
            (fixture.influencer_id,),
        )
        assert len(members) == 1
        member = members[0]
        removed = await harness.campaign_service.remove_member(
            harness.context,
            campaign_id,
            member.id,
            CampaignMemberRemoveInput(expected_version=member.version),
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert removed.removed_at is not None

        restored = await harness.campaign_service.bulk_add_members(
            harness.context,
            campaign_id,
            add_input,
            idempotency_key="member-restore-key",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        no_op = await harness.campaign_service.bulk_add_members(
            harness.context,
            campaign_id,
            add_input,
            idempotency_key="member-noop-key",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert restored.result.restored_count == 1
        assert no_op.result.already_active_count == 1
        assert no_op.result.active_count_after == 1
        assert set(no_op.result.to_replay_payload()) == {
            "campaign_id",
            "source_pool_run_id",
            "requested_count",
            "added_count",
            "restored_count",
            "already_active_count",
            "active_count_after",
        }
        assert await count_rows(harness.session, CampaignMember) == 1
        # First add and restore mutate. The all-active no-op still has a durable
        # idempotency record, but correctly produces no Member mutation Audit.
        assert await count_audits(harness.session, AuditAction.CAMPAIGN_MEMBERS_ADDED) == 2
        assert await count_rows(harness.session, Phase3AIdempotencyRecord) == 4

        ten_thousand = tuple(
            CampaignMemberAddItem(
                influencer_id=uuid4(),
                preferred_platform_account_id=uuid4(),
            )
            for _ in range(10_000)
        )
        bulk_input = CampaignMemberBulkAddInput(members=ten_thousand)
        canonical = harness.campaign_service._canonical_members(bulk_input.members)
        summary = CampaignMemberBulkAddResult(
            campaign_id=campaign_id,
            source_pool_run_id=None,
            requested_count=len(canonical),
            added_count=len(canonical),
            restored_count=0,
            already_active_count=0,
            active_count_after=len(canonical),
        ).to_replay_payload()
        assert len(canonical) == 10_000
        assert len(json.dumps(summary)) < 1_000
        assert "members" not in summary


@async_test
async def test_selected_run_member_ids_require_completed_matching_run_and_replay() -> None:
    async with domain_harness() as harness:
        selected = await seed_influencer(harness.session, harness.context)
        pending = await seed_influencer(harness.session, harness.context)
        mismatched = await seed_influencer(harness.session, harness.context)
        completed_run = await seed_candidate_pool_run(
            harness,
            selected,
            status=CandidatePoolRunStatus.COMPLETED,
            include_member=True,
        )
        pending_run = await seed_candidate_pool_run(
            harness,
            pending,
            status=CandidatePoolRunStatus.PENDING,
            include_member=True,
        )
        mismatched_run = await seed_candidate_pool_run(
            harness,
            mismatched,
            status=CandidatePoolRunStatus.COMPLETED,
            include_member=True,
        )
        assert completed_run.member_id is not None
        assert pending_run.member_id is not None
        assert mismatched_run.member_id is not None
        await harness.session.commit()
        campaign_id = await create_campaign(harness)

        selected_input = CampaignMemberFromCandidateRunBulkAddInput(
            run_id=completed_run.run_id,
            member_ids=(completed_run.member_id,),
        )
        added = await harness.campaign_service.bulk_add_members_from_candidate_run(
            harness.context,
            campaign_id,
            selected_input,
            idempotency_key="member-source-run-key",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        replay = await harness.campaign_service.bulk_add_members_from_candidate_run(
            harness.context,
            campaign_id,
            selected_input,
            idempotency_key="member-source-run-key",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        members = await harness.campaign_service.repository.list_members_for_influencers(
            campaign_id,
            (selected.influencer_id,),
        )
        assert added.result.source_pool_run_id == completed_run.run_id
        assert replay.replayed
        assert replay.result == added.result
        assert len(members) == 1
        assert members[0].source_pool_run_id == completed_run.run_id
        record = await harness.session.scalar(
            select(Phase3AIdempotencyRecord).where(
                Phase3AIdempotencyRecord.operation_scope
                == Phase3AOperationScope.CAMPAIGN_MEMBER_BULK_ADD,
                Phase3AIdempotencyRecord.idempotency_key == "member-source-run-key",
            )
        )
        assert record is not None
        assert record.result_payload["source_pool_run_id"] == str(completed_run.run_id)
        assert record.request_hash == canonical_request_hash(
            {
                "campaign_id": campaign_id,
                "source_pool_run_id": completed_run.run_id,
                "member_ids": (completed_run.member_id,),
            }
        )

        with pytest.raises(CampaignOutreachError) as incomplete:
            await harness.campaign_service.bulk_add_members_from_candidate_run(
                harness.context,
                campaign_id,
                CampaignMemberFromCandidateRunBulkAddInput(
                    run_id=pending_run.run_id,
                    member_ids=(pending_run.member_id,),
                ),
                idempotency_key="member-pending-run-key",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert incomplete.value.code == "CANDIDATE_POOL_RUN_NOT_COMPLETED"
        await refresh_context_after_rollback(harness)

        with pytest.raises(CampaignOutreachError) as wrong_run_member:
            await harness.campaign_service.bulk_add_members_from_candidate_run(
                harness.context,
                campaign_id,
                CampaignMemberFromCandidateRunBulkAddInput(
                    run_id=mismatched_run.run_id,
                    member_ids=(completed_run.member_id,),
                ),
                idempotency_key="member-mismatched-run-key",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert wrong_run_member.value.code == "CANDIDATE_POOL_RUN_MEMBER_NOT_FOUND"
        assert await count_rows(harness.session, CampaignMember) == 1
        assert await count_audits(harness.session, AuditAction.CAMPAIGN_MEMBERS_ADDED) == 1
        for key in ("member-pending-run-key", "member-mismatched-run-key"):
            assert (
                await harness.session.scalar(
                    select(Phase3AIdempotencyRecord).where(
                        Phase3AIdempotencyRecord.operation_scope
                        == Phase3AOperationScope.CAMPAIGN_MEMBER_BULK_ADD,
                        Phase3AIdempotencyRecord.idempotency_key == key,
                    )
                )
                is None
            )


@async_test
async def test_selected_run_member_ids_keep_unknown_selection_explicit_and_restore_in_place() -> (
    None
):
    async with domain_harness() as harness:
        match_fixture = await seed_influencer(harness.session, harness.context)
        selected_unknown_fixture = await seed_influencer(harness.session, harness.context)
        omitted_unknown_fixture = await seed_influencer(harness.session, harness.context)
        source_run = await seed_candidate_pool_run(
            harness,
            match_fixture,
            status=CandidatePoolRunStatus.COMPLETED,
            include_member=True,
        )
        assert source_run.member_id is not None
        selected_unknown_id = await add_candidate_pool_member(
            harness,
            run_id=source_run.run_id,
            fixture=selected_unknown_fixture,
            result=CandidateResult.UNKNOWN,
        )
        omitted_unknown_id = await add_candidate_pool_member(
            harness,
            run_id=source_run.run_id,
            fixture=omitted_unknown_fixture,
            result=CandidateResult.UNKNOWN,
        )
        await harness.session.commit()
        campaign_id = await create_campaign(harness)
        selected_input = CampaignMemberFromCandidateRunBulkAddInput(
            run_id=source_run.run_id,
            member_ids=(source_run.member_id, selected_unknown_id),
        )

        first = await harness.campaign_service.bulk_add_members_from_candidate_run(
            harness.context,
            campaign_id,
            selected_input,
            idempotency_key="selected-match-and-unknown",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        replay = await harness.campaign_service.bulk_add_members_from_candidate_run(
            harness.context,
            campaign_id,
            selected_input,
            idempotency_key="selected-match-and-unknown",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert first.result.added_count == 2
        assert first.result.source_pool_run_id == source_run.run_id
        assert replay.replayed
        assert replay.result == first.result

        selected_members = await harness.campaign_service.repository.list_members_for_influencers(
            campaign_id,
            (match_fixture.influencer_id, selected_unknown_fixture.influencer_id),
        )
        assert {member.influencer_id for member in selected_members} == {
            match_fixture.influencer_id,
            selected_unknown_fixture.influencer_id,
        }
        assert (
            await harness.campaign_service.repository.list_members_for_influencers(
                campaign_id,
                (omitted_unknown_fixture.influencer_id,),
            )
            == []
        )
        assert selected_input.member_ids is not None
        assert omitted_unknown_id not in selected_input.member_ids

        match_member = next(
            member
            for member in selected_members
            if member.influencer_id == match_fixture.influencer_id
        )
        removed = await harness.campaign_service.remove_member(
            harness.context,
            campaign_id,
            match_member.id,
            CampaignMemberRemoveInput(expected_version=match_member.version),
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert removed.removed_at is not None
        restored = await harness.campaign_service.bulk_add_members_from_candidate_run(
            harness.context,
            campaign_id,
            CampaignMemberFromCandidateRunBulkAddInput(
                run_id=source_run.run_id,
                member_ids=(source_run.member_id,),
            ),
            idempotency_key="selected-match-restore",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        active = await harness.campaign_service.bulk_add_members_from_candidate_run(
            harness.context,
            campaign_id,
            CampaignMemberFromCandidateRunBulkAddInput(
                run_id=source_run.run_id,
                member_ids=(source_run.member_id,),
            ),
            idempotency_key="selected-match-active",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        restored_member = await harness.campaign_service.get_member(
            harness.context,
            campaign_id,
            match_member.id,
        )
        assert restored.result.restored_count == 1
        assert active.result.already_active_count == 1
        assert restored_member.id == match_member.id
        assert restored_member.source_pool_run_id == source_run.run_id
        assert restored_member.removed_at is None


@async_test
async def test_selected_run_rejects_multiple_accounts_for_one_influencer() -> None:
    async with domain_harness() as harness:
        fixture = await seed_influencer(harness.session, harness.context)
        source_run = await seed_candidate_pool_run(
            harness,
            fixture,
            status=CandidatePoolRunStatus.COMPLETED,
            include_member=True,
        )
        assert source_run.member_id is not None
        alternate_account = InfluencerPlatformAccount(
            influencer_id=fixture.influencer_id,
            platform=Platform.XIAOHONGSHU,
            platform_account_id=f"alternate-{uuid4().hex}",
            account_name="Explicit conflict alternate account",
            account_handle="explicit-conflict",
            source=DataSource.MANUAL,
            is_active=True,
        )
        harness.session.add(alternate_account)
        await harness.session.flush()
        alternate_account_id = alternate_account.id
        alternate_member_id = await add_candidate_pool_member(
            harness,
            run_id=source_run.run_id,
            fixture=fixture,
            result=CandidateResult.UNKNOWN,
            platform_account_id=alternate_account_id,
        )
        await harness.session.commit()
        campaign_id = await create_campaign(harness)

        with pytest.raises(CampaignOutreachError) as ambiguous:
            await harness.campaign_service.bulk_add_members_from_candidate_run(
                harness.context,
                campaign_id,
                CampaignMemberFromCandidateRunBulkAddInput(
                    run_id=source_run.run_id,
                    member_ids=(source_run.member_id, alternate_member_id),
                ),
                idempotency_key="ambiguous-selected-run-accounts",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert ambiguous.value.status_code == 422
        assert ambiguous.value.code == "CANDIDATE_POOL_RUN_MEMBER_AMBIGUOUS"
        details = ambiguous.value.safe_details
        assert details is not None
        influencer = await harness.session.get(Influencer, fixture.influencer_id)
        assert influencer is not None
        assert details["conflicting_influencer"] == {
            "id": str(fixture.influencer_id),
            "display_name": influencer.display_name,
        }
        assert set(details["conflicting_member_ids"]) == {
            str(source_run.member_id),
            str(alternate_member_id),
        }
        accounts = details["conflicting_accounts"]
        assert isinstance(accounts, list)
        assert {account["platform_account_id"] for account in accounts} == {
            str(fixture.account_id),
            str(alternate_account_id),
        }
        assert any(
            account["account_name"] == "Explicit conflict alternate account" for account in accounts
        )
        assert "contact" not in json.dumps(details).lower()
        assert await count_rows(harness.session, CampaignMember) == 0


def test_all_match_candidate_run_input_has_one_strict_alternative_shape() -> None:
    run_id = uuid4()
    member_id = uuid4()

    explicit = CampaignMemberFromCandidateRunBulkAddInput(
        run_id=run_id,
        member_ids=(member_id,),
    )
    all_match = CampaignMemberFromCandidateRunBulkAddInput(
        run_id=run_id,
        selection_mode="ALL_MATCH",
        excluded_member_ids=(),
    )

    assert explicit.selection_mode is None
    assert explicit.member_ids == (member_id,)
    assert explicit.excluded_member_ids is None
    assert all_match.member_ids is None
    assert all_match.excluded_member_ids == ()
    for invalid in (
        {"run_id": run_id},
        {"run_id": run_id, "excluded_member_ids": ()},
        {"run_id": run_id, "selection_mode": "ALL_MATCH"},
        {
            "run_id": run_id,
            "selection_mode": "ALL_MATCH",
            "member_ids": (member_id,),
            "excluded_member_ids": (),
        },
        {
            "run_id": run_id,
            "selection_mode": "ALL_MATCH",
            "excluded_member_ids": (member_id, member_id),
        },
    ):
        with pytest.raises(ValidationError):
            CampaignMemberFromCandidateRunBulkAddInput.model_validate(invalid)


@async_test
async def test_all_match_resolves_server_side_in_chunks_excludes_only_match_and_replays() -> None:
    async with domain_harness() as harness:
        included_first = await seed_influencer(harness.session, harness.context)
        excluded = await seed_influencer(harness.session, harness.context)
        included_last = await seed_influencer(harness.session, harness.context)
        unknown = await seed_influencer(harness.session, harness.context)
        source_run = await seed_candidate_pool_run(
            harness,
            included_first,
            status=CandidatePoolRunStatus.COMPLETED,
            include_member=True,
        )
        assert source_run.member_id is not None
        excluded_member_id = await add_candidate_pool_member(
            harness,
            run_id=source_run.run_id,
            fixture=excluded,
            result=CandidateResult.MATCH,
        )
        await add_candidate_pool_member(
            harness,
            run_id=source_run.run_id,
            fixture=included_last,
            result=CandidateResult.MATCH,
        )
        await add_candidate_pool_member(
            harness,
            run_id=source_run.run_id,
            fixture=unknown,
            result=CandidateResult.UNKNOWN,
        )
        await harness.session.commit()
        campaign_id = await create_campaign(harness)
        input_payload = CampaignMemberFromCandidateRunBulkAddInput(
            run_id=source_run.run_id,
            selection_mode="ALL_MATCH",
            excluded_member_ids=(excluded_member_id,),
        )
        page_requests: list[tuple[UUID | None, int]] = []
        original_page_reader = (
            harness.campaign_service.repository.list_matching_source_run_members_page
        )

        async def record_page(
            *,
            source_pool_run_id: UUID,
            after_member_id: UUID | None,
            limit: int,
        ) -> object:
            assert source_pool_run_id == source_run.run_id
            page_requests.append((after_member_id, limit))
            return await original_page_reader(
                source_pool_run_id=source_pool_run_id,
                after_member_id=after_member_id,
                limit=limit,
            )

        with (
            patch.object(CampaignService, "_ALL_MATCH_SOURCE_CHUNK_SIZE", 2),
            patch.object(
                harness.campaign_service.repository,
                "list_matching_source_run_members_page",
                new=record_page,
            ),
        ):
            first = await harness.campaign_service.bulk_add_members_from_candidate_run(
                harness.context,
                campaign_id,
                input_payload,
                idempotency_key="all-match-chunked",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        replay = await harness.campaign_service.bulk_add_members_from_candidate_run(
            harness.context,
            campaign_id,
            input_payload,
            idempotency_key="all-match-chunked",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )

        assert first.result.requested_count == 2
        assert first.result.added_count == 2
        assert first.result.restored_count == 0
        assert first.result.already_active_count == 0
        assert first.result.active_count_after == 2
        assert replay.replayed
        assert replay.result == first.result
        assert [limit for _cursor, limit in page_requests] == [2, 2, 2]
        assert page_requests[0][0] is None
        assert page_requests[1][0] is not None
        assert page_requests[2][0] is not None

        campaign_members = await harness.campaign_service.repository.list_members_for_influencers(
            campaign_id,
            (
                included_first.influencer_id,
                excluded.influencer_id,
                included_last.influencer_id,
                unknown.influencer_id,
            ),
        )
        assert {member.influencer_id for member in campaign_members} == {
            included_first.influencer_id,
            included_last.influencer_id,
        }
        assert await count_audits(harness.session, AuditAction.CAMPAIGN_MEMBERS_ADDED) == 1

        member_to_restore = next(
            member
            for member in campaign_members
            if member.influencer_id == included_first.influencer_id
        )
        removed = await harness.campaign_service.remove_member(
            harness.context,
            campaign_id,
            member_to_restore.id,
            CampaignMemberRemoveInput(expected_version=member_to_restore.version),
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert removed.removed_at is not None
        rejoined = await harness.campaign_service.bulk_add_members_from_candidate_run(
            harness.context,
            campaign_id,
            input_payload,
            idempotency_key="all-match-rejoin",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert rejoined.result.requested_count == 2
        assert rejoined.result.added_count == 0
        assert rejoined.result.restored_count == 1
        assert rejoined.result.already_active_count == 1
        assert rejoined.result.active_count_after == 2
        assert await count_audits(harness.session, AuditAction.CAMPAIGN_MEMBERS_ADDED) == 2


@async_test
async def test_all_match_rejects_nonmatch_wrong_run_or_empty_selection() -> None:
    async with domain_harness() as harness:
        match = await seed_influencer(harness.session, harness.context)
        unknown = await seed_influencer(harness.session, harness.context)
        other = await seed_influencer(harness.session, harness.context)
        source_run = await seed_candidate_pool_run(
            harness,
            match,
            status=CandidatePoolRunStatus.COMPLETED,
            include_member=True,
        )
        assert source_run.member_id is not None
        unknown_member_id = await add_candidate_pool_member(
            harness,
            run_id=source_run.run_id,
            fixture=unknown,
            result=CandidateResult.UNKNOWN,
        )
        other_run = await seed_candidate_pool_run(
            harness,
            other,
            status=CandidatePoolRunStatus.COMPLETED,
            include_member=True,
        )
        empty_run = await seed_candidate_pool_run(
            harness,
            await seed_influencer(harness.session, harness.context),
            status=CandidatePoolRunStatus.COMPLETED,
            include_member=False,
        )
        assert other_run.member_id is not None
        await harness.session.commit()
        campaign_id = await create_campaign(harness)

        for exclusion, key in (
            (unknown_member_id, "all-match-unknown-exclusion"),
            (other_run.member_id, "all-match-wrong-run-exclusion"),
        ):
            with pytest.raises(CampaignOutreachError) as rejected:
                await harness.campaign_service.bulk_add_members_from_candidate_run(
                    harness.context,
                    campaign_id,
                    CampaignMemberFromCandidateRunBulkAddInput(
                        run_id=source_run.run_id,
                        selection_mode="ALL_MATCH",
                        excluded_member_ids=(exclusion,),
                    ),
                    idempotency_key=key,
                    ip="127.0.0.1",
                    user_agent="campaign-outreach-domain-test",
                )
            assert rejected.value.status_code == 422
            assert rejected.value.code == "CANDIDATE_POOL_RUN_MEMBER_NOT_FOUND"
            await refresh_context_after_rollback(harness)

        with pytest.raises(CampaignOutreachError) as empty_selection:
            await harness.campaign_service.bulk_add_members_from_candidate_run(
                harness.context,
                campaign_id,
                CampaignMemberFromCandidateRunBulkAddInput(
                    run_id=empty_run.run_id,
                    selection_mode="ALL_MATCH",
                    excluded_member_ids=(),
                ),
                idempotency_key="all-match-zero",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert empty_selection.value.status_code == 422
        assert empty_selection.value.code == "CANDIDATE_POOL_RUN_SELECTION_EMPTY"
        assert await count_rows(harness.session, CampaignMember) == 0
        assert await count_audits(harness.session, AuditAction.CAMPAIGN_MEMBERS_ADDED) == 0
        assert (
            await harness.session.scalar(
                select(Phase3AIdempotencyRecord).where(
                    Phase3AIdempotencyRecord.idempotency_key == "all-match-zero"
                )
            )
            is None
        )


@async_test
async def test_all_match_validates_ten_thousand_exclusions_in_small_bind_chunks() -> None:
    async with domain_harness() as harness:
        fixture = await seed_influencer(harness.session, harness.context)
        source_run = await seed_candidate_pool_run(
            harness,
            fixture,
            status=CandidatePoolRunStatus.COMPLETED,
            include_member=True,
        )
        assert source_run.member_id is not None
        await harness.session.commit()
        campaign_id = await create_campaign(harness)
        exclusions = (
            source_run.member_id,
            *(UUID(int=value + 1_000_000) for value in range(9_999)),
        )
        assert len(exclusions) == 10_000
        assert harness.session.bind is not None
        parameter_counts: list[int] = []

        def capture(
            _connection: object,
            _cursor: object,
            statement: str,
            parameters: object,
            _context: object,
            _executemany: object,
        ) -> None:
            if "count(candidate_pool_members.id)" not in statement.lower():
                return
            if isinstance(parameters, (dict, tuple)):
                parameter_counts.append(len(parameters))
            else:
                parameter_counts.append(1)

        event.listen(harness.session.bind.sync_engine, "before_cursor_execute", capture)
        try:
            with pytest.raises(CampaignOutreachError) as invalid_exclusion:
                await harness.campaign_service.bulk_add_members_from_candidate_run(
                    harness.context,
                    campaign_id,
                    CampaignMemberFromCandidateRunBulkAddInput(
                        run_id=source_run.run_id,
                        selection_mode="ALL_MATCH",
                        excluded_member_ids=exclusions,
                    ),
                    idempotency_key="all-match-ten-thousand-exclusions",
                    ip="127.0.0.1",
                    user_agent="campaign-outreach-domain-test",
                )
        finally:
            event.remove(harness.session.bind.sync_engine, "before_cursor_execute", capture)

        assert invalid_exclusion.value.status_code == 422
        assert invalid_exclusion.value.code == "CANDIDATE_POOL_RUN_MEMBER_NOT_FOUND"
        assert len(parameter_counts) == 20
        # 500 requested UUIDs plus the run and MATCH predicates, comfortably
        # below SQLite's conservative bind ceiling and nowhere near 10,000.
        assert max(parameter_counts) <= 502


@async_test
async def test_all_match_fails_closed_with_safe_actionable_account_conflict_details() -> None:
    async with domain_harness() as harness:
        fixture = await seed_influencer(harness.session, harness.context)
        source_run = await seed_candidate_pool_run(
            harness,
            fixture,
            status=CandidatePoolRunStatus.COMPLETED,
            include_member=True,
        )
        assert source_run.member_id is not None
        alternate_account = InfluencerPlatformAccount(
            influencer_id=fixture.influencer_id,
            platform=Platform.XIAOHONGSHU,
            platform_account_id=f"alternate-{uuid4().hex}",
            account_name="Actionable alternate account",
            account_handle="alternate-handle",
            source=DataSource.MANUAL,
            is_active=True,
        )
        harness.session.add(alternate_account)
        await harness.session.flush()
        alternate_account_id = alternate_account.id
        alternate_member_id = await add_candidate_pool_member(
            harness,
            run_id=source_run.run_id,
            fixture=fixture,
            result=CandidateResult.MATCH,
            platform_account_id=alternate_account_id,
        )
        await harness.session.commit()
        campaign_id = await create_campaign(harness)

        with pytest.raises(CampaignOutreachError) as ambiguous:
            await harness.campaign_service.bulk_add_members_from_candidate_run(
                harness.context,
                campaign_id,
                CampaignMemberFromCandidateRunBulkAddInput(
                    run_id=source_run.run_id,
                    selection_mode="ALL_MATCH",
                    excluded_member_ids=(),
                ),
                idempotency_key="all-match-ambiguous",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert ambiguous.value.status_code == 422
        assert ambiguous.value.code == "CANDIDATE_POOL_RUN_MEMBER_AMBIGUOUS"
        details = ambiguous.value.safe_details
        assert details is not None
        influencer = await harness.session.get(Influencer, fixture.influencer_id)
        assert influencer is not None
        assert details["conflicting_influencer"] == {
            "id": str(fixture.influencer_id),
            "display_name": influencer.display_name,
        }
        assert set(details["conflicting_member_ids"]) == {
            str(source_run.member_id),
            str(alternate_member_id),
        }
        accounts = details["conflicting_accounts"]
        assert isinstance(accounts, list)
        assert {account["member_id"] for account in accounts} == set(
            details["conflicting_member_ids"]
        )
        assert {account["platform_account_id"] for account in accounts} == {
            str(fixture.account_id),
            str(alternate_account_id),
        }
        assert any(
            account["account_name"] == "Actionable alternate account" for account in accounts
        )
        assert "contact" not in json.dumps(details).lower()
        assert await count_rows(harness.session, CampaignMember) == 0
        await refresh_context_after_rollback(harness)

        resolved = await harness.campaign_service.bulk_add_members_from_candidate_run(
            harness.context,
            campaign_id,
            CampaignMemberFromCandidateRunBulkAddInput(
                run_id=source_run.run_id,
                selection_mode="ALL_MATCH",
                excluded_member_ids=(alternate_member_id,),
            ),
            idempotency_key="all-match-ambiguity-resolved",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert resolved.result.requested_count == 1
        assert resolved.result.added_count == 1
        assert await count_audits(harness.session, AuditAction.CAMPAIGN_MEMBERS_ADDED) == 1


@async_test
async def test_all_match_ambiguity_preflight_never_expands_large_exclusions_into_sql() -> None:
    async with domain_harness() as harness:
        first = await seed_influencer(harness.session, harness.context)
        second = await seed_influencer(harness.session, harness.context)
        source_run = await seed_candidate_pool_run(
            harness,
            first,
            status=CandidatePoolRunStatus.COMPLETED,
            include_member=True,
        )
        await add_candidate_pool_member(
            harness,
            run_id=source_run.run_id,
            fixture=second,
            result=CandidateResult.MATCH,
        )
        await harness.session.commit()
        assert harness.session.bind is not None
        statements: list[str] = []
        parameter_counts: list[int] = []

        def capture(
            _connection: object,
            _cursor: object,
            statement: str,
            parameters: object,
            _context: object,
            _executemany: object,
        ) -> None:
            if (
                "FROM candidate_pool_members" not in statement
                or "JOIN influencers" not in statement
            ):
                return
            statements.append(statement)
            if isinstance(parameters, (dict, tuple)):
                parameter_counts.append(len(parameters))
            else:
                parameter_counts.append(1)

        event.listen(harness.session.bind.sync_engine, "before_cursor_execute", capture)
        try:
            with patch.object(CampaignService, "_ALL_MATCH_AMBIGUITY_SOURCE_CHUNK_SIZE", 1):
                ambiguity = await harness.campaign_service._first_all_match_ambiguity(
                    source_pool_run_id=source_run.run_id,
                    excluded_member_ids={UUID(int=value + 1_000_000) for value in range(10_000)},
                )
        finally:
            event.remove(harness.session.bind.sync_engine, "before_cursor_execute", capture)

        assert ambiguity is None
        # One ordered stream, even with a one-row consumer partition.  This
        # guards against reissuing an unindexed influencer sort per page.
        assert len(statements) == 1
        assert all("NOT IN" not in statement.upper() for statement in statements)
        assert max(parameter_counts) < 10


@async_test
async def test_all_match_ambiguity_stream_finds_a_conflict_after_many_partitions() -> None:
    async with domain_harness() as harness:
        fixtures = [await seed_influencer(harness.session, harness.context) for _ in range(12)]
        source_run = await seed_candidate_pool_run(
            harness,
            fixtures[0],
            status=CandidatePoolRunStatus.COMPLETED,
            include_member=True,
        )
        assert source_run.member_id is not None
        member_ids: dict[UUID, UUID] = {fixtures[0].influencer_id: source_run.member_id}
        for fixture in fixtures[1:]:
            member_ids[fixture.influencer_id] = await add_candidate_pool_member(
                harness,
                run_id=source_run.run_id,
                fixture=fixture,
                result=CandidateResult.MATCH,
            )
        late_fixture = max(fixtures, key=lambda fixture: fixture.influencer_id.int)
        alternate_account = InfluencerPlatformAccount(
            influencer_id=late_fixture.influencer_id,
            platform=Platform.XIAOHONGSHU,
            platform_account_id=f"late-conflict-{uuid4().hex}",
            account_name="Late conflict account",
            account_handle="late-conflict",
            source=DataSource.MANUAL,
            is_active=True,
        )
        harness.session.add(alternate_account)
        await harness.session.flush()
        alternate_member_id = await add_candidate_pool_member(
            harness,
            run_id=source_run.run_id,
            fixture=late_fixture,
            result=CandidateResult.MATCH,
            platform_account_id=alternate_account.id,
        )
        await harness.session.commit()
        assert harness.session.bind is not None
        statements: list[str] = []

        def capture(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: object,
        ) -> None:
            if "FROM candidate_pool_members" in statement and "JOIN influencers" in statement:
                statements.append(statement)

        event.listen(harness.session.bind.sync_engine, "before_cursor_execute", capture)
        try:
            with patch.object(CampaignService, "_ALL_MATCH_AMBIGUITY_SOURCE_CHUNK_SIZE", 1):
                ambiguity = await harness.campaign_service._first_all_match_ambiguity(
                    source_pool_run_id=source_run.run_id,
                    excluded_member_ids=set(),
                )
        finally:
            event.remove(harness.session.bind.sync_engine, "before_cursor_execute", capture)

        assert ambiguity is not None
        assert ambiguity.influencer_id == late_fixture.influencer_id
        assert {account.member_id for account in ambiguity.accounts} == {
            member_ids[late_fixture.influencer_id],
            alternate_member_id,
        }
        assert len(statements) == 1


@async_test
async def test_direct_bulk_add_rejects_all_duplicate_influencer_entries() -> None:
    influencer_id = uuid4()
    account_id = uuid4()
    with pytest.raises(ValidationError):
        CampaignMemberBulkAddInput(
            members=(
                CampaignMemberAddItem(
                    influencer_id=influencer_id,
                    preferred_platform_account_id=account_id,
                ),
                CampaignMemberAddItem(
                    influencer_id=influencer_id,
                    preferred_platform_account_id=account_id,
                ),
            )
        )
    with pytest.raises(ValidationError):
        CampaignMemberBulkAddInput(
            members=(
                CampaignMemberAddItem(
                    influencer_id=influencer_id,
                    preferred_platform_account_id=account_id,
                ),
                CampaignMemberAddItem(
                    influencer_id=influencer_id,
                    preferred_platform_account_id=uuid4(),
                ),
            )
        )


@async_test
async def test_direct_bulk_add_rejects_legacy_source_run_field() -> None:
    async with domain_harness() as harness:
        fixture = await seed_influencer(harness.session, harness.context)
        await harness.session.commit()
        campaign_id = await create_campaign(harness)

        with pytest.raises(CampaignOutreachError) as rejected:
            await harness.campaign_service.bulk_add_members(
                harness.context,
                campaign_id,
                CampaignMemberBulkAddInput(
                    source_pool_run_id=uuid4(),
                    members=(
                        CampaignMemberAddItem(
                            influencer_id=fixture.influencer_id,
                            preferred_platform_account_id=fixture.account_id,
                        ),
                    ),
                ),
                idempotency_key="direct-legacy-source-run",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert rejected.value.status_code == 422
        assert rejected.value.code == "CAMPAIGN_MEMBER_SOURCE_RUN_NOT_ALLOWED"
        assert await count_rows(harness.session, CampaignMember) == 0


@async_test
async def test_campaign_member_page_excludes_removed_and_retains_original_order_on_restore() -> (
    None
):
    async with domain_harness() as harness:
        first_fixture = await seed_influencer(harness.session, harness.context)
        removed_fixture = await seed_influencer(harness.session, harness.context)
        third_fixture = await seed_influencer(harness.session, harness.context)
        await harness.session.commit()
        campaign_id = await create_campaign(harness)
        first_member = await add_member(harness, campaign_id, first_fixture)
        removed_member = await add_member(harness, campaign_id, removed_fixture)
        third_member = await add_member(harness, campaign_id, third_fixture)
        original_created_at = datetime(2026, 8, 18, 4, 0, tzinfo=UTC)
        await harness.session.execute(
            update(CampaignMember)
            .where(CampaignMember.id.in_((first_member.id, removed_member.id, third_member.id)))
            .values(created_at=original_created_at)
        )
        await harness.session.commit()
        removed = await harness.campaign_service.remove_member(
            harness.context,
            campaign_id,
            removed_member.id,
            CampaignMemberRemoveInput(expected_version=removed_member.version),
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        removed_read = await harness.campaign_service.get_member(
            harness.context,
            campaign_id,
            removed_member.id,
        )
        active_before_restore = await harness.campaign_service.list_members(
            harness.context,
            campaign_id,
            cursor=None,
            limit=10,
        )
        assert removed.removed_at is not None
        assert removed_read.removed_at is not None
        assert removed_member.id not in {item.id for item in active_before_restore.items}

        restored = await harness.campaign_service.bulk_add_members(
            harness.context,
            campaign_id,
            CampaignMemberBulkAddInput(
                members=(
                    CampaignMemberAddItem(
                        influencer_id=removed_fixture.influencer_id,
                        preferred_platform_account_id=removed_fixture.account_id,
                    ),
                )
            ),
            idempotency_key="restore-member-list-order",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert restored.result.restored_count == 1
        restored_read = await harness.campaign_service.get_member(
            harness.context,
            campaign_id,
            removed_member.id,
        )
        assert restored_read.id == removed_member.id
        assert restored_read.created_at == removed_read.created_at
        assert restored_read.removed_at is None

        expected_ids = sorted(
            (first_member.id, removed_member.id, third_member.id),
            key=str,
        )
        first_page = await harness.campaign_service.list_members(
            harness.context,
            campaign_id,
            cursor=None,
            limit=1,
        )
        assert first_page.next_cursor is not None
        second_page = await harness.campaign_service.list_members(
            harness.context,
            campaign_id,
            cursor=first_page.next_cursor,
            limit=1,
        )
        assert second_page.next_cursor is not None
        third_page = await harness.campaign_service.list_members(
            harness.context,
            campaign_id,
            cursor=second_page.next_cursor,
            limit=1,
        )
        assert third_page.next_cursor is None
        assert [item.id for item in (*first_page.items, *second_page.items, *third_page.items)] == (
            expected_ids
        )

        other_campaign_id = await create_campaign(harness, name="Other cursor Campaign")
        with pytest.raises(CampaignOutreachError) as wrong_campaign:
            await harness.campaign_service.list_members(
                harness.context,
                other_campaign_id,
                cursor=first_page.next_cursor,
                limit=1,
            )
        assert wrong_campaign.value.code == "CURSOR_MISMATCH"
        wrong_department_cursor = first_page.next_cursor.model_copy(
            update={"department_id": uuid4()}
        )
        with pytest.raises(CampaignOutreachError) as wrong_department:
            await harness.campaign_service.list_members(
                harness.context,
                campaign_id,
                cursor=wrong_department_cursor,
                limit=1,
            )
        assert wrong_department.value.code == "CURSOR_MISMATCH"


@async_test
async def test_campaign_page_uses_updated_at_desc_id_desc_keyset() -> None:
    async with domain_harness() as harness:
        first_campaign_id = await create_campaign(harness, name="First Campaign page")
        second_campaign_id = await create_campaign(harness, name="Second Campaign page")
        older = datetime(2026, 8, 18, 4, 0, tzinfo=UTC)
        newer = datetime(2026, 8, 18, 5, 0, tzinfo=UTC)
        await harness.session.execute(
            update(Campaign).where(Campaign.id == first_campaign_id).values(updated_at=older)
        )
        await harness.session.execute(
            update(Campaign).where(Campaign.id == second_campaign_id).values(updated_at=newer)
        )
        await harness.session.commit()
        first_page = await harness.campaign_service.list_campaigns(
            harness.context,
            cursor=None,
            limit=1,
        )
        assert first_page.items[0].id == second_campaign_id
        assert first_page.next_cursor == CampaignCursor(
            updated_at=first_page.items[0].updated_at,
            id=second_campaign_id,
        )
        second_page = await harness.campaign_service.list_campaigns(
            harness.context,
            cursor=first_page.next_cursor,
            limit=1,
        )
        assert [item.id for item in second_page.items] == [first_campaign_id]
        assert second_page.next_cursor is None


@async_test
async def test_target_shared_replay_is_redacted_and_viewer_cannot_mutate() -> None:
    async with domain_harness() as harness:
        fixture = await seed_influencer(
            harness.session,
            harness.context,
            contact_value="secret-contact@example.invalid",
        )
        await harness.session.commit()
        campaign_id = await create_campaign(harness)
        member = await add_member(harness, campaign_id, fixture)
        request = OutreachTargetCreateInput(
            member_id=member.id,
            influencer_id=fixture.influencer_id,
            channel=OutreachChannel.EMAIL,
            contact_id=fixture.contact_id,
        )
        first = await harness.outreach_service.create_target(
            harness.context,
            campaign_id,
            request,
            idempotency_key="target-create-key",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        replay = await harness.outreach_service.create_target(
            harness.context,
            campaign_id,
            request,
            idempotency_key="target-create-key",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert replay.replayed
        assert replay.result.masked_display == "***"
        assert await count_audits(harness.session, AuditAction.OUTREACH_TARGET_CREATED) == 1
        record = await harness.session.scalar(
            select(Phase3AIdempotencyRecord).where(
                Phase3AIdempotencyRecord.operation_scope
                == Phase3AOperationScope.OUTREACH_TARGET_CREATE,
                Phase3AIdempotencyRecord.idempotency_key == "target-create-key",
            )
        )
        assert record is not None
        encoded_payload = json.dumps(record.result_payload)
        assert "secret-contact@example.invalid" not in encoded_payload
        assert "contact_id" in record.result_payload

        with pytest.raises(CampaignOutreachError) as error:
            await harness.outreach_service.create_target(
                harness.context,
                campaign_id,
                OutreachTargetCreateInput(
                    member_id=member.id,
                    influencer_id=fixture.influencer_id,
                    channel=OutreachChannel.EMAIL,
                    contact_id=uuid4(),
                ),
                idempotency_key="target-create-key",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert error.value.code == "IDEMPOTENCY_KEY_REUSED"
        await refresh_context_after_rollback(harness)

        viewer = AuthContext(
            department=harness.context.department,
            operator=harness.context.operator,
            role=Role.VIEWER,
            auth_session=harness.context.auth_session,
        )
        target = await harness.outreach_service.get_target(viewer, first.result.id)
        assert target.masked_display == "***"
        with pytest.raises(CampaignOutreachError) as viewer_error:
            await harness.outreach_service.create_target(
                viewer,
                campaign_id,
                request,
                idempotency_key="viewer-key",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert viewer_error.value.status_code == 403


@async_test
async def test_campaign_lifecycle_activation_cas_and_terminal_closed() -> None:
    async with domain_harness() as harness:
        fixture = await seed_influencer(harness.session, harness.context)
        await harness.session.commit()
        campaign_id = await create_campaign(harness)
        with pytest.raises(CampaignOutreachError) as no_member:
            await activate_campaign(harness, campaign_id)
        assert no_member.value.code == "CAMPAIGN_ACTIVATION_REQUIRES_MEMBER"
        await refresh_context_after_rollback(harness)

        member = await add_member(harness, campaign_id, fixture)
        member_id = member.id
        with pytest.raises(CampaignOutreachError) as no_target:
            await activate_campaign(harness, campaign_id)
        assert no_target.value.code == "CAMPAIGN_ACTIVATION_REQUIRES_TARGET"
        await refresh_context_after_rollback(harness)

        await create_email_target(harness, campaign_id, member_id, fixture)
        active = await harness.campaign_service.transition_campaign_status(
            harness.context,
            campaign_id,
            CampaignStatusTransitionInput(
                to_status=CampaignStatus.ACTIVE,
                expected_version=1,
            ),
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert active.status is CampaignStatus.ACTIVE
        assert active.version == 2

        with pytest.raises(CampaignOutreachError) as stale:
            await harness.campaign_service.update_campaign(
                harness.context,
                campaign_id,
                CampaignUpdateInput(
                    name="CAS",
                    owner_operator_id=active.owner_operator_id,
                    review_mode=CampaignReviewMode.FIRST_N,
                    review_count=50,
                    duplicate_history_policy=DuplicateHistoryPolicy.ALLOW_WITH_WARNING,
                    duplicate_window_days=None,
                    expected_version=1,
                ),
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert stale.value.code == "VERSION_CONFLICT"
        await refresh_context_after_rollback(harness)

        with pytest.raises(CampaignOutreachError) as direct_close:
            await harness.campaign_service.transition_campaign_status(
                harness.context,
                campaign_id,
                CampaignStatusTransitionInput(
                    to_status=CampaignStatus.CLOSED,
                    expected_version=2,
                ),
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert direct_close.value.code == "INVALID_CAMPAIGN_TRANSITION"
        await refresh_context_after_rollback(harness)

        paused = await harness.campaign_service.transition_campaign_status(
            harness.context,
            campaign_id,
            CampaignStatusTransitionInput(
                to_status=CampaignStatus.PAUSED,
                expected_version=2,
            ),
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        closed = await harness.campaign_service.transition_campaign_status(
            harness.context,
            campaign_id,
            CampaignStatusTransitionInput(
                to_status=CampaignStatus.CLOSED,
                expected_version=paused.version,
            ),
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert closed.status is CampaignStatus.CLOSED
        with pytest.raises(CampaignOutreachError) as terminal:
            await harness.campaign_service.transition_campaign_status(
                harness.context,
                campaign_id,
                CampaignStatusTransitionInput(
                    to_status=CampaignStatus.ACTIVE,
                    expected_version=closed.version,
                ),
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert terminal.value.code == "INVALID_CAMPAIGN_TRANSITION"


@async_test
async def test_task_state_machine_event_replay_and_atomic_rollback() -> None:
    async with domain_harness() as harness:
        fixture = await seed_influencer(
            harness.session,
            harness.context,
            contact_value="sent-proof@example.invalid",
        )
        await harness.session.commit()
        campaign_id = await create_campaign(harness)
        member = await add_member(harness, campaign_id, fixture)
        target = await create_email_target(harness, campaign_id, member.id, fixture)
        await activate_campaign(harness, campaign_id)

        created = await harness.outreach_service.create_task(
            harness.context,
            target.id,
            OutreachTaskCreateInput(due_at=harness.clock.now() + timedelta(days=1)),
            idempotency_key="task-create-key",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        replayed_create = await harness.outreach_service.create_task(
            harness.context,
            target.id,
            OutreachTaskCreateInput(due_at=harness.clock.now() + timedelta(days=1)),
            idempotency_key="task-create-key",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert created.result.task.state is OutreachTaskState.REVIEW_REQUIRED
        assert replayed_create.replayed
        assert await count_audits(harness.session, AuditAction.OUTREACH_TASK_CREATED) == 1

        contact = await harness.session.get(InfluencerContact, fixture.contact_id)
        assert contact is not None
        contact.validation_status = ContactValidationStatus.INVALID
        await harness.session.commit()
        with pytest.raises(CampaignOutreachError) as invalid_ready:
            await harness.outreach_service.transition_task(
                harness.context,
                created.result.task.id,
                OutreachTaskTransitionInput(
                    to_state=OutreachTaskState.READY,
                    expected_version=1,
                ),
                idempotency_key="invalid-ready-key",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert invalid_ready.value.code == "OUTREACH_CONTACT_INVALID"
        await refresh_context_after_rollback(harness)
        contact = await harness.session.get(InfluencerContact, fixture.contact_id)
        assert contact is not None
        contact.validation_status = ContactValidationStatus.VALID
        await harness.session.commit()

        approved = await harness.outreach_service.transition_task(
            harness.context,
            created.result.task.id,
            OutreachTaskTransitionInput(
                to_state=OutreachTaskState.READY,
                expected_version=1,
            ),
            idempotency_key="approve-key",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        replayed_approval = await harness.outreach_service.transition_task(
            harness.context,
            created.result.task.id,
            OutreachTaskTransitionInput(
                to_state=OutreachTaskState.READY,
                expected_version=1,
            ),
            idempotency_key="approve-key",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert replayed_approval.replayed
        assert replayed_approval.event_id == approved.event_id

        with pytest.raises(CampaignOutreachError) as reused_key:
            await harness.outreach_service.transition_task(
                harness.context,
                created.result.task.id,
                OutreachTaskTransitionInput(
                    to_state=OutreachTaskState.STOPPED,
                    expected_version=1,
                ),
                idempotency_key="approve-key",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert reused_key.value.code == "IDEMPOTENCY_KEY_REUSED"
        await refresh_context_after_rollback(harness)

        sent = await harness.outreach_service.transition_task(
            harness.context,
            created.result.task.id,
            OutreachTaskTransitionInput(
                to_state=OutreachTaskState.SENT,
                expected_version=approved.version,
            ),
            idempotency_key="sent-key",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        event = await harness.session.get(OutreachEvent, sent.event_id)
        assert event is not None
        assert event.event_type.value == "OUTREACH_SENT"
        assert event.redacted_target_snapshot is not None
        assert "sent-proof@example.invalid" not in json.dumps(event.redacted_target_snapshot)
        assert "endpoint_fingerprint" in event.redacted_target_snapshot
        fingerprint = event.redacted_target_snapshot["endpoint_fingerprint"]
        assert (
            fingerprint
            == hmac.new(
                OUTREACH_FINGERPRINT_TEST_KEY.encode("utf-8"),
                b"phase3a:outreach:endpoint-fingerprint:v1\x1fcontact:email\x1f"
                b"sent-proof@example.invalid",
                hashlib.sha256,
            ).hexdigest()
        )
        assert (
            fingerprint
            != hashlib.sha256(
                b"phase3a:endpoint:v1:contact:email:sent-proof@example.invalid"
            ).hexdigest()
        )
        assert "sent-proof@example.invalid" not in fingerprint

        with pytest.raises(CampaignOutreachError) as terminal:
            await harness.outreach_service.transition_task(
                harness.context,
                created.result.task.id,
                OutreachTaskTransitionInput(
                    to_state=OutreachTaskState.STOPPED,
                    expected_version=sent.version,
                ),
                idempotency_key="different-after-sent",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert terminal.value.code == "INVALID_OUTREACH_TASK_TRANSITION"
        await refresh_context_after_rollback(harness)
        assert await count_rows(harness.session, OutreachEvent) == 3
        assert await count_audits(harness.session, AuditAction.OUTREACH_TASK_TRANSITIONED) == 2

        assert OutreachService._is_legal_task_transition(
            OutreachTaskState.REVIEW_REQUIRED,
            OutreachTaskState.READY,
        )
        assert OutreachService._is_legal_task_transition(
            OutreachTaskState.REVIEW_REQUIRED,
            OutreachTaskState.STOPPED,
        )
        assert OutreachService._is_legal_task_transition(
            OutreachTaskState.READY,
            OutreachTaskState.FAILED,
        )
        assert OutreachService._is_legal_task_transition(
            OutreachTaskState.FAILED,
            OutreachTaskState.READY,
        )
        assert not OutreachService._is_legal_task_transition(
            OutreachTaskState.SENT,
            OutreachTaskState.READY,
        )

        with patch.object(
            harness.campaign_service.audit, "add", side_effect=RuntimeError("audit fail")
        ):
            with pytest.raises(RuntimeError, match="audit fail"):
                await harness.campaign_service.create_campaign(
                    harness.context,
                    CampaignCreateInput(name="Must rollback"),
                    idempotency_key="rollback-key",
                    ip="127.0.0.1",
                    user_agent="campaign-outreach-domain-test",
                )
        stored = await harness.session.scalar(
            select(Campaign).where(Campaign.name == "Must rollback")
        )
        assert stored is None
        assert (
            await harness.session.scalar(
                select(Phase3AIdempotencyRecord).where(
                    Phase3AIdempotencyRecord.idempotency_key == "rollback-key"
                )
            )
            is None
        )


@async_test
async def test_task_execution_requires_active_member_and_template_rendering() -> None:
    async with domain_harness() as harness:
        fixture = await seed_influencer(harness.session, harness.context)
        await harness.session.commit()
        campaign_id = await create_campaign(harness)
        member = await add_member(harness, campaign_id, fixture)
        member_id = member.id
        member_version = member.version
        target = await create_email_target(harness, campaign_id, member.id, fixture)
        await activate_campaign(harness, campaign_id)
        created = await harness.outreach_service.create_task(
            harness.context,
            target.id,
            OutreachTaskCreateInput(due_at=harness.clock.now() + timedelta(days=1)),
            idempotency_key="guarded-task-create",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        ready = await harness.outreach_service.transition_task(
            harness.context,
            created.result.task.id,
            OutreachTaskTransitionInput(
                to_state=OutreachTaskState.READY,
                expected_version=1,
            ),
            idempotency_key="guarded-task-ready",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )

        task = await harness.session.get(OutreachTask, created.result.task.id)
        assert task is not None
        task.template_version_id = uuid4()
        await harness.session.commit()
        with pytest.raises(CampaignOutreachError) as template_error:
            await harness.outreach_service.transition_task(
                harness.context,
                created.result.task.id,
                OutreachTaskTransitionInput(
                    to_state=OutreachTaskState.SENT,
                    expected_version=ready.version,
                ),
                idempotency_key="guarded-task-template-send",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert template_error.value.code == "TEMPLATE_RENDERING_UNAVAILABLE"
        await refresh_context_after_rollback(harness)

        task = await harness.session.get(OutreachTask, created.result.task.id)
        assert task is not None
        task.template_version_id = None
        await harness.session.commit()
        await harness.campaign_service.remove_member(
            harness.context,
            campaign_id,
            member_id,
            CampaignMemberRemoveInput(expected_version=member_version),
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        with pytest.raises(CampaignOutreachError) as inactive_member:
            await harness.outreach_service.transition_task(
                harness.context,
                created.result.task.id,
                OutreachTaskTransitionInput(
                    to_state=OutreachTaskState.SENT,
                    expected_version=ready.version,
                ),
                idempotency_key="guarded-task-inactive-send",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert inactive_member.value.code == "CAMPAIGN_MEMBER_INACTIVE"
        await refresh_context_after_rollback(harness)
        assert (
            len(
                await harness.outreach_service.list_task_events(
                    harness.context, created.result.task.id
                )
            )
            == 2
        )


@async_test
async def test_target_channel_enablement_and_invalid_endpoint_rules() -> None:
    async with domain_harness(enabled_channels=frozenset()) as disabled_harness:
        fixture = await seed_influencer(disabled_harness.session, disabled_harness.context)
        await disabled_harness.session.commit()
        campaign_id = await create_campaign(disabled_harness)
        member = await add_member(disabled_harness, campaign_id, fixture)
        with pytest.raises(CampaignOutreachError) as disabled:
            await disabled_harness.outreach_service.create_target(
                disabled_harness.context,
                campaign_id,
                OutreachTargetCreateInput(
                    member_id=member.id,
                    influencer_id=fixture.influencer_id,
                    channel=OutreachChannel.EMAIL,
                    contact_id=fixture.contact_id,
                ),
                idempotency_key="disabled-channel",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert disabled.value.code == "CHANNEL_DISABLED"

    async with domain_harness() as harness:
        invalid = await seed_influencer(
            harness.session,
            harness.context,
            contact_status=ContactValidationStatus.INVALID,
        )
        valid = await seed_influencer(harness.session, harness.context)
        await harness.session.commit()
        campaign_id = await create_campaign(harness)
        invalid_member = await add_member(harness, campaign_id, invalid)
        with pytest.raises(CampaignOutreachError) as invalid_error:
            await harness.outreach_service.create_target(
                harness.context,
                campaign_id,
                OutreachTargetCreateInput(
                    member_id=invalid_member.id,
                    influencer_id=invalid.influencer_id,
                    channel=OutreachChannel.EMAIL,
                    contact_id=invalid.contact_id,
                ),
                idempotency_key="invalid-contact",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert invalid_error.value.code == "OUTREACH_CONTACT_INVALID"
        await refresh_context_after_rollback(harness)

        valid_member = await add_member(harness, campaign_id, valid)
        xhs_target = await harness.outreach_service.create_target(
            harness.context,
            campaign_id,
            OutreachTargetCreateInput(
                member_id=valid_member.id,
                influencer_id=valid.influencer_id,
                channel=OutreachChannel.XIAOHONGSHU_PRIVATE_MESSAGE,
                platform_account_id=valid.account_id,
            ),
            idempotency_key="xhs-target",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert xhs_target.result.channel is OutreachChannel.XIAOHONGSHU_PRIVATE_MESSAGE


@async_test
async def test_task_manual_priority_and_duplicate_step_are_enforced() -> None:
    async with domain_harness() as harness:
        fixture = await seed_influencer(harness.session, harness.context)
        await harness.session.commit()
        campaign = await harness.campaign_service.create_campaign(
            harness.context,
            CampaignCreateInput(
                name="Manual priority",
                review_mode=CampaignReviewMode.AUTO,
                review_count=None,
            ),
            idempotency_key="manual-priority-campaign",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        member = await add_member(harness, campaign.result.id, fixture)
        target = await create_email_target(harness, campaign.result.id, member.id, fixture)
        await activate_campaign(harness, campaign.result.id)

        created = await harness.outreach_service.create_task(
            harness.context,
            target.id,
            OutreachTaskCreateInput(
                due_at=harness.clock.now() + timedelta(days=1),
                priority=OutreachPriority.HIGH,
                priority_reason_codes=("MANUAL_PRIORITY",),
            ),
            idempotency_key="manual-priority-task",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert created.result.task.state is OutreachTaskState.READY
        assert created.result.task.priority is OutreachPriority.HIGH
        assert created.result.task.priority_source is OutreachPrioritySource.MANUAL
        assert created.result.task.priority_reason_codes == ("MANUAL_PRIORITY",)

        with pytest.raises(CampaignOutreachError) as duplicate:
            await harness.outreach_service.create_task(
                harness.context,
                target.id,
                OutreachTaskCreateInput(due_at=harness.clock.now() + timedelta(days=2)),
                idempotency_key="manual-priority-duplicate-step",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert duplicate.value.code == "OUTREACH_TASK_STEP_ALREADY_EXISTS"


@async_test
async def test_task_transition_matrix_executes_legal_and_rejects_invalid_edges() -> None:
    async with domain_harness() as harness:
        fixture = await seed_influencer(harness.session, harness.context)
        await harness.session.commit()

        async def create_state_task(
            review_mode: CampaignReviewMode,
            *,
            suffix: str,
        ) -> tuple[UUID, OutreachTaskState]:
            campaign = await harness.campaign_service.create_campaign(
                harness.context,
                CampaignCreateInput(
                    name=f"State matrix {suffix}",
                    review_mode=review_mode,
                    review_count=None if review_mode is CampaignReviewMode.AUTO else 1,
                ),
                idempotency_key=f"state-matrix-campaign-{suffix}",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
            member = await add_member(harness, campaign.result.id, fixture)
            target = await create_email_target(harness, campaign.result.id, member.id, fixture)
            await activate_campaign(harness, campaign.result.id)
            task = await harness.outreach_service.create_task(
                harness.context,
                target.id,
                OutreachTaskCreateInput(due_at=harness.clock.now() + timedelta(days=1)),
                idempotency_key=f"state-matrix-task-{suffix}",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
            return task.result.task.id, task.result.task.state

        review_task_id, review_state = await create_state_task(
            CampaignReviewMode.FIRST_N,
            suffix="review-ready-sent",
        )
        assert review_state is OutreachTaskState.REVIEW_REQUIRED
        approved = await harness.outreach_service.transition_task(
            harness.context,
            review_task_id,
            OutreachTaskTransitionInput(
                to_state=OutreachTaskState.READY,
                expected_version=1,
            ),
            idempotency_key="state-matrix-review-ready",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        sent = await harness.outreach_service.transition_task(
            harness.context,
            review_task_id,
            OutreachTaskTransitionInput(
                to_state=OutreachTaskState.SENT,
                expected_version=approved.version,
            ),
            idempotency_key="state-matrix-ready-sent",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert sent.state is OutreachTaskState.SENT

        stopped_review_task_id, stopped_review_state = await create_state_task(
            CampaignReviewMode.FIRST_N,
            suffix="review-stopped",
        )
        assert stopped_review_state is OutreachTaskState.REVIEW_REQUIRED
        stopped_from_review = await harness.outreach_service.transition_task(
            harness.context,
            stopped_review_task_id,
            OutreachTaskTransitionInput(
                to_state=OutreachTaskState.STOPPED,
                expected_version=1,
            ),
            idempotency_key="state-matrix-review-stopped",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert stopped_from_review.state is OutreachTaskState.STOPPED

        retry_task_id, retry_state = await create_state_task(
            CampaignReviewMode.AUTO,
            suffix="failed-retry-stopped",
        )
        assert retry_state is OutreachTaskState.READY
        failed = await harness.outreach_service.transition_task(
            harness.context,
            retry_task_id,
            OutreachTaskTransitionInput(
                to_state=OutreachTaskState.FAILED,
                expected_version=1,
                reason_code="TEST_FAILURE",
            ),
            idempotency_key="state-matrix-ready-failed",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        retried = await harness.outreach_service.transition_task(
            harness.context,
            retry_task_id,
            OutreachTaskTransitionInput(
                to_state=OutreachTaskState.READY,
                expected_version=failed.version,
            ),
            idempotency_key="state-matrix-failed-ready",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        stopped_from_ready = await harness.outreach_service.transition_task(
            harness.context,
            retry_task_id,
            OutreachTaskTransitionInput(
                to_state=OutreachTaskState.STOPPED,
                expected_version=retried.version,
            ),
            idempotency_key="state-matrix-ready-stopped",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert stopped_from_ready.state is OutreachTaskState.STOPPED

        with pytest.raises(ValidationError):
            OutreachTaskTransitionInput(
                to_state=OutreachTaskState.REVIEW_REQUIRED,
                expected_version=1,
            )

        async def assert_invalid_transition(
            from_state: OutreachTaskState,
            to_state: OutreachTaskState,
        ) -> None:
            task = await harness.session.get(OutreachTask, review_task_id)
            assert task is not None
            task.state = from_state
            task.version = 1
            await harness.session.commit()
            with pytest.raises(CampaignOutreachError) as invalid:
                await harness.outreach_service.transition_task(
                    harness.context,
                    review_task_id,
                    OutreachTaskTransitionInput(
                        to_state=to_state,
                        expected_version=1,
                        reason_code=(
                            "TEST_FAILURE" if to_state is OutreachTaskState.FAILED else None
                        ),
                    ),
                    idempotency_key=f"invalid-{from_state.value}-{to_state.value}",
                    ip="127.0.0.1",
                    user_agent="campaign-outreach-domain-test",
                )
            assert invalid.value.code == "INVALID_OUTREACH_TASK_TRANSITION"
            await refresh_context_after_rollback(harness)

        # The legal edges above use only public service calls. Re-seeding the
        # Task state here lets the service guard every forbidden source/target
        # pair without creating unrelated logical steps.
        invalid_targets = {
            OutreachTaskState.REVIEW_REQUIRED: (
                OutreachTaskState.SENT,
                OutreachTaskState.FAILED,
            ),
            OutreachTaskState.READY: (OutreachTaskState.READY,),
            OutreachTaskState.SENT: (
                OutreachTaskState.READY,
                OutreachTaskState.SENT,
                OutreachTaskState.STOPPED,
                OutreachTaskState.FAILED,
            ),
            OutreachTaskState.STOPPED: (
                OutreachTaskState.READY,
                OutreachTaskState.SENT,
                OutreachTaskState.STOPPED,
                OutreachTaskState.FAILED,
            ),
            OutreachTaskState.FAILED: (
                OutreachTaskState.SENT,
                OutreachTaskState.STOPPED,
                OutreachTaskState.FAILED,
            ),
        }
        for from_state, targets in invalid_targets.items():
            for to_state in targets:
                await assert_invalid_transition(from_state, to_state)


@async_test
async def test_cross_campaign_history_warning_is_redacted_in_result_and_event() -> None:
    async with domain_harness() as harness:
        fixture = await seed_influencer(harness.session, harness.context)
        history_context = await seed_context(harness.session)
        await harness.session.commit()
        history_campaigns = CampaignService(
            harness.session,
            channel_registry=harness.campaign_service.channel_registry,
            clock=harness.clock,
        )
        history_outreach = OutreachService(
            harness.session,
            channel_registry=harness.campaign_service.channel_registry,
            clock=harness.clock,
        )
        history_campaign = await history_campaigns.create_campaign(
            history_context,
            CampaignCreateInput(
                name="Historical campaign",
                review_mode=CampaignReviewMode.AUTO,
                review_count=None,
            ),
            idempotency_key="history-campaign",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        await history_campaigns.bulk_add_members(
            history_context,
            history_campaign.result.id,
            CampaignMemberBulkAddInput(
                members=(
                    CampaignMemberAddItem(
                        influencer_id=fixture.influencer_id,
                        preferred_platform_account_id=fixture.account_id,
                    ),
                )
            ),
            idempotency_key="history-member",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        history_member = await history_campaigns.repository.get_member(
            (
                await history_campaigns.repository.list_members_for_influencers(
                    history_campaign.result.id,
                    (fixture.influencer_id,),
                )
            )[0].id,
            campaign_id=history_campaign.result.id,
            department_id=history_context.department.id,
        )
        assert history_member is not None
        history_target = await history_outreach.create_target(
            history_context,
            history_campaign.result.id,
            OutreachTargetCreateInput(
                member_id=history_member.id,
                influencer_id=fixture.influencer_id,
                channel=OutreachChannel.EMAIL,
                contact_id=fixture.contact_id,
            ),
            idempotency_key="history-target",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        await history_campaigns.transition_campaign_status(
            history_context,
            history_campaign.result.id,
            CampaignStatusTransitionInput(
                to_status=CampaignStatus.ACTIVE,
                expected_version=1,
            ),
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        historical_task = await history_outreach.create_task(
            history_context,
            history_target.result.id,
            OutreachTaskCreateInput(due_at=harness.clock.now() + timedelta(days=1)),
            idempotency_key="history-task",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert historical_task.result.task.state is OutreachTaskState.READY
        historical_sent = await history_outreach.transition_task(
            history_context,
            historical_task.result.task.id,
            OutreachTaskTransitionInput(
                to_state=OutreachTaskState.SENT,
                expected_version=historical_task.result.task.version,
            ),
            idempotency_key="history-sent",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )

        campaign_id = await create_campaign(harness, name="New campaign")
        member = await add_member(harness, campaign_id, fixture)
        target = await create_email_target(harness, campaign_id, member.id, fixture)
        created = await harness.outreach_service.create_task(
            harness.context,
            target.id,
            OutreachTaskCreateInput(due_at=harness.clock.now() + timedelta(days=1)),
            idempotency_key="history-warning-task",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        assert created.result.history_warning is not None
        assert created.result.history_warning.channel is OutreachChannel.EMAIL
        assert created.result.history_warning.last_sent_at == harness.clock.now()

        created_event = await harness.session.scalar(
            select(OutreachEvent).where(
                OutreachEvent.task_id == created.result.task.id,
                OutreachEvent.event_type == OutreachEventType.TASK_CREATED,
            )
        )
        assert created_event is not None
        assert isinstance(created_event.event_metadata, dict)
        warning_metadata = created_event.event_metadata["history_warning"]
        assert isinstance(warning_metadata, dict)
        assert set(warning_metadata) == {"channel", "last_sent_at"}
        encoded_warning = json.dumps(warning_metadata)
        assert str(history_campaign.result.id) not in encoded_warning
        assert str(historical_task.result.task.id) not in encoded_warning
        assert str(historical_sent.event_id) not in encoded_warning


@async_test
async def test_task_event_and_audit_rollback_is_atomic() -> None:
    async with domain_harness() as harness:
        fixture = await seed_influencer(harness.session, harness.context)
        await harness.session.commit()
        campaign_id = await create_campaign(harness)
        member = await add_member(harness, campaign_id, fixture)
        target = await create_email_target(harness, campaign_id, member.id, fixture)
        target_id = target.id
        await activate_campaign(harness, campaign_id)
        create_input = OutreachTaskCreateInput(due_at=harness.clock.now() + timedelta(days=1))

        with patch.object(
            harness.outreach_service.audit,
            "add",
            side_effect=RuntimeError("task create audit fail"),
        ):
            with pytest.raises(RuntimeError, match="task create audit fail"):
                await harness.outreach_service.create_task(
                    harness.context,
                    target_id,
                    create_input,
                    idempotency_key="task-create-audit-rollback",
                    ip="127.0.0.1",
                    user_agent="campaign-outreach-domain-test",
                )
        assert await count_rows(harness.session, OutreachTask) == 0
        assert await count_rows(harness.session, OutreachEvent) == 0
        assert await count_audits(harness.session, AuditAction.OUTREACH_TASK_CREATED) == 0
        await refresh_context_after_rollback(harness)

        created = await harness.outreach_service.create_task(
            harness.context,
            target_id,
            create_input,
            idempotency_key="task-create-after-rollback",
            ip="127.0.0.1",
            user_agent="campaign-outreach-domain-test",
        )
        with patch.object(
            harness.outreach_service.audit,
            "add",
            side_effect=RuntimeError("task transition audit fail"),
        ):
            with pytest.raises(RuntimeError, match="task transition audit fail"):
                await harness.outreach_service.transition_task(
                    harness.context,
                    created.result.task.id,
                    OutreachTaskTransitionInput(
                        to_state=OutreachTaskState.READY,
                        expected_version=created.result.task.version,
                    ),
                    idempotency_key="task-transition-audit-rollback",
                    ip="127.0.0.1",
                    user_agent="campaign-outreach-domain-test",
                )
        await refresh_context_after_rollback(harness)
        stored = await harness.session.get(OutreachTask, created.result.task.id)
        assert stored is not None
        assert stored.state is OutreachTaskState.REVIEW_REQUIRED
        assert stored.version == 1
        assert await count_rows(harness.session, OutreachEvent) == 1
        assert await count_audits(harness.session, AuditAction.OUTREACH_TASK_CREATED) == 1
        assert await count_audits(harness.session, AuditAction.OUTREACH_TASK_TRANSITIONED) == 0


@async_test
async def test_member_bulk_add_rejects_preferred_account_owned_by_another_influencer() -> None:
    async with domain_harness() as harness:
        selected = await seed_influencer(harness.session, harness.context)
        other = await seed_influencer(harness.session, harness.context)
        await harness.session.commit()
        campaign_id = await create_campaign(harness)

        with pytest.raises(CampaignOutreachError) as invalid_account:
            await harness.campaign_service.bulk_add_members(
                harness.context,
                campaign_id,
                CampaignMemberBulkAddInput(
                    members=(
                        CampaignMemberAddItem(
                            influencer_id=selected.influencer_id,
                            preferred_platform_account_id=other.account_id,
                        ),
                    )
                ),
                idempotency_key="foreign-preferred-account",
                ip="127.0.0.1",
                user_agent="campaign-outreach-domain-test",
            )
        assert invalid_account.value.code == "PREFERRED_PLATFORM_ACCOUNT_INVALID"
        assert await count_rows(harness.session, CampaignMember) == 0
        assert (
            await harness.session.scalar(
                select(Phase3AIdempotencyRecord).where(
                    Phase3AIdempotencyRecord.idempotency_key == "foreign-preferred-account"
                )
            )
            is None
        )

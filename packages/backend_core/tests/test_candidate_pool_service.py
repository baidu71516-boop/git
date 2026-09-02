"""Portable lifecycle, idempotency, materialization, and RBAC tests for WO-3A-2."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.audit.models import AuditLog
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, DepartmentPermission, Operator
from backend_core.auth.service import AuthContext
from backend_core.content_activity.enums import (
    ContentActivityCoverageStatus,
    ContentActivityObservationStatus,
    ContentActivityProvider,
    ContentActivityPublicationType,
    ContentActivityResult,
    ContentActivityScanTerminalReason,
    ContentActivitySemantics,
    ProviderAccountIdentityNamespace,
    ProviderAccountIdentityVerificationOutcome,
    ProviderAccountIdentityVerificationState,
)
from backend_core.content_activity.models import (
    ContentActivityObservation,
    ProviderAccountIdentity,
    ProviderAccountIdentityVerification,
)
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.growth.buyer_taxonomy_v1 import (
    BUYER_TAXONOMY_V1_HASH,
    resolve_buyer_taxonomy_v1,
)
from backend_core.growth.enums import (
    BuyerLeadTier,
    BuyerProspectOwnerFilter,
    BuyerProspectRecentCollectionWindow,
    CampaignReviewMode,
    CampaignStatus,
    CandidatePoolKind,
    CandidatePoolRunStatus,
    CandidatePoolStatus,
    CandidateResult,
    DuplicateHistoryPolicy,
    Phase3AOperationScope,
)
from backend_core.growth.long_inactivity import LongInactivityProviderRefresh
from backend_core.growth.models import (
    Campaign,
    CampaignMember,
    CandidatePool,
    CandidatePoolMember,
    CandidatePoolRun,
    Phase3AIdempotencyRecord,
)
from backend_core.growth.repository import CandidatePoolRepository
from backend_core.growth.schemas import (
    BuyerProspectRuleCreateInput,
    BuyerProspectRuleLifecycleInput,
    BuyerProspectRuleUpdateInput,
    CandidatePoolCreateInput,
    CandidatePoolRunRequest,
    LongInactivityEnrichmentRequest,
    TargetingPolicyCreateInput,
)
from backend_core.growth.service import CandidatePoolService, TargetingError
from backend_core.growth.targeting import (
    BuyerProspectRuleTargetingPolicy,
    BuyerTargetingPolicy,
    CandidateFactBundle,
    CollectionContextSnapshot,
    ContentActivityFact,
    IntegerRange,
    LongInactivityConstraint,
    SellerTargetingPolicy,
    TargetingEvaluation,
    TaxonomyDefinition,
)
from backend_core.imports.enums import (
    CollectionJobStatus,
    ImportJobFileStatus,
    ImportJobStatus,
    ImportMatchType,
    ImportRowAction,
    ImportSourceType,
    SourceAcquiredAtOrigin,
    StoredFileType,
)
from backend_core.imports.models import (
    CollectionJob,
    ImportJob,
    ImportJobFile,
    ImportRow,
    StoredImportFile,
)
from backend_core.influencers.enums import (
    ContactFilter,
    ContactType,
    ContactValidationStatus,
    CRMStage,
    DataSource,
    InfluencerStatus,
    Platform,
)
from backend_core.influencers.freshness import FreshnessPolicy
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
    InfluencerSourceState,
)
from backend_core.outreach.enums import (
    OutreachActorType,
    OutreachChannel,
    OutreachEventType,
    OutreachPriority,
    OutreachPrioritySource,
    OutreachTaskKind,
    OutreachTaskState,
)
from backend_core.outreach.models import OutreachEvent, OutreachTarget, OutreachTask
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

NOW = datetime(2026, 8, 17, 8, 0, tzinfo=UTC)


async def _actor(session: AsyncSession, *, role: Role) -> AuthContext:
    department = Department(
        name=f"targeting-{uuid4().hex}",
        password_hash="not-used-by-targeting-tests",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    session.add(department)
    await session.flush()
    operator = Operator(
        department_id=department.id,
        name="Targeting operator",
        role=role,
        status=OperatorStatus.ACTIVE,
    )
    permission = DepartmentPermission(department_id=department.id, role=role)
    session.add_all([operator, permission])
    await session.flush()
    auth_session = AuthSession(
        department_id=department.id,
        operator_id=operator.id,
        token_hash=uuid4().hex * 2,
        csrf_token_hash=uuid4().hex * 2,
        ip="127.0.0.1",
        user_agent="candidate-pool-service-test",
        expires_at=NOW + timedelta(days=1),
    )
    session.add(auth_session)
    await session.flush()
    return AuthContext(
        department=department,
        operator=operator,
        role=role,
        auth_session=auth_session,
    )


async def _account(
    session: AsyncSession,
    *,
    owner: Operator,
    source_tags: list[str] | None = None,
    source: DataSource = DataSource.GENERIC,
    platform: Platform = Platform.XIAOHONGSHU,
) -> InfluencerPlatformAccount:
    influencer = Influencer(
        display_name="Targeting Candidate",
        owner_operator_id=owner.id,
        crm_stage=CRMStage.TO_DEVELOP,
        status=InfluencerStatus.ACTIVE,
        deleted_at=None,
    )
    session.add(influencer)
    await session.flush()
    account = InfluencerPlatformAccount(
        influencer_id=influencer.id,
        platform=platform,
        platform_account_id=f"candidate-{uuid4().hex}",
        account_name="Targeting Candidate",
        account_handle="targeting-candidate",
        profile_url=f"https://example.invalid/{uuid4().hex}",
        normalized_profile_url=f"https://example.invalid/{uuid4().hex}",
        source=source,
        source_tags=source_tags if source_tags is not None else ["beauty"],
        is_active=True,
    )
    session.add(account)
    await session.flush()
    return account


async def _content_activity_observation(
    session: AsyncSession,
    account: InfluencerPlatformAccount,
    *,
    last_publication_at: datetime,
) -> ContentActivityObservation:
    """Insert the minimum immutable trusted lineage for as-of hydration tests."""

    observed_at = NOW - timedelta(days=1)
    identity = ProviderAccountIdentity(
        platform_account_id=account.id,
        platform=account.platform,
        namespace=ProviderAccountIdentityNamespace.XIAOHONGSHU_USERID,
        opaque_external_identity=f"candidate-identity-{uuid4().hex}",
        identity_source="TIKHUB_XHS_APP_V2",
        resolver_contract_version="TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1",
        verification_state=ProviderAccountIdentityVerificationState.VERIFIED_CURRENT,
        resolved_at=observed_at,
        verified_at=observed_at,
        provenance_ref="candidate-fixture-identity",
        lock_version=1,
        superseded_at=None,
        revoked_at=None,
    )
    session.add(identity)
    await session.flush()
    verification = ProviderAccountIdentityVerification(
        provider_account_identity_id=identity.id,
        identity_source="TIKHUB_XHS_APP_V2",
        resolver_contract_version="TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1",
        verification_outcome=ProviderAccountIdentityVerificationOutcome.POLICY_VERIFIED,
        verified_at=observed_at,
        provenance_ref="candidate-fixture-verification",
        idempotency_key=f"candidate-fixture-{uuid4().hex}",
    )
    session.add(verification)
    await session.flush()
    observation = ContentActivityObservation(
        platform_account_id=account.id,
        platform=account.platform,
        schema_version=1,
        activity_semantics=ContentActivitySemantics.CURRENT_PUBLIC_VISIBLE,
        provider_account_identity_id=identity.id,
        provider_account_identity_verification_id=verification.id,
        activity_source_provider=ContentActivityProvider.TIKHUB,
        provider_product="XIAOHONGSHU_APP_V2",
        endpoint="/api/v1/xiaohongshu/app_v2/get_user_posted_notes",
        endpoint_version="APP_V2",
        adapter_version="TIKHUB_XHS_CONTENT_ACTIVITY_ADAPTER_V1",
        capability_policy_version="TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1",
        response_schema_version="TIKHUB_XHS_APP_V2_GET_USER_POSTED_NOTES_RESPONSE_SCHEMA_V1",
        visibility_policy_version="TIKHUB_XHS_APP_V2_CURRENT_PUBLIC_VISIBILITY_POLICY_V1",
        attempt_started_at=observed_at - timedelta(seconds=1),
        observed_at=observed_at,
        observation_status=ContentActivityObservationStatus.COMPLETE,
        coverage_status=ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET,
        activity_result=ContentActivityResult.PUBLICATION_FOUND,
        last_publication_at=last_publication_at,
        latest_publication_id_namespace="xiaohongshu.noteid",
        latest_publication_id="candidate-fixture-note",
        latest_publication_type=ContentActivityPublicationType.VIDEO,
        co_latest_publication_count=1,
        coverage_start_at=None,
        coverage_end_at=None,
        timestamp_encoding="UNIX_SECONDS",
        source_timezone=None,
        timezone_basis="UNIX_SECONDS",
        normalized_timezone="UTC",
        request_ref="candidate-fixture-request",
        provenance_ref="candidate-fixture-observation",
        scan_terminal_reason=ContentActivityScanTerminalReason.SINGLE_RESPONSE_COMPLETE,
        scanned_page_count=1,
        scanned_item_count=1,
        provider_error_class=None,
        provider_error_code=None,
    )
    session.add(observation)
    await session.flush()
    return observation


def _service(session: AsyncSession, **kwargs: object) -> CandidatePoolService:
    return CandidatePoolService(session, freshness_policy=FreshnessPolicy(), **kwargs)


async def _collection_job(
    session: AsyncSession,
    *,
    context: AuthContext,
    industry: str,
    subdirection: str | None = "makeup",
) -> CollectionJob:
    assert context.operator is not None
    collection = CollectionJob(
        name=f"Targeting collection {uuid4().hex}",
        industry=industry,
        subdirection=subdirection,
        purpose="Targeting test fixture",
        target_action="discover",
        target_count=1,
        department_id=context.department.id,
        owner_operator_id=context.operator.id,
        source_type=ImportSourceType.GENERIC_CSV,
        status=CollectionJobStatus.COMPLETED,
    )
    session.add(collection)
    await session.flush()
    return collection


async def _committed_import(
    session: AsyncSession,
    *,
    context: AuthContext,
    collection: CollectionJob,
    account: InfluencerPlatformAccount,
    creator_tags: list[str] | None,
    raw_data: dict[str, object] | None = None,
    source_acquired_at: datetime | None = None,
) -> ImportJob:
    """Seed committed canonical import provenance for one account."""

    assert context.operator is not None
    stored_file = StoredImportFile(
        sha256=uuid4().hex * 2,
        storage_key=f"targeting/{uuid4().hex}.csv",
        size=1,
        detected_type=StoredFileType.CSV,
        detected_mime="text/csv",
        expires_at=NOW + timedelta(days=1),
    )
    session.add(stored_file)
    await session.flush()
    job = ImportJob(
        collection_job_id=collection.id,
        department_id=context.department.id,
        operator_id=context.operator.id,
        source_type=ImportSourceType.GENERIC_CSV,
        status=ImportJobStatus.COMPLETED,
        preview_revision=1,
        confirmed_revision=1,
    )
    session.add(job)
    await session.flush()
    file = ImportJobFile(
        import_job_id=job.id,
        stored_file_id=stored_file.id,
        position=1,
        original_filename="targeting.csv",
        status=ImportJobFileStatus.READY,
        source_acquired_at=source_acquired_at,
        source_acquired_at_origin=(
            SourceAcquiredAtOrigin.USER_CONFIRMED
            if source_acquired_at is not None
            else SourceAcquiredAtOrigin.LEGACY_UNKNOWN
        ),
        source_acquired_at_confirmation_required=False,
    )
    session.add(file)
    await session.flush()
    row = ImportRow(
        import_job_id=job.id,
        import_job_file_id=file.id,
        row_number=2,
        raw_data=raw_data or {},
        normalized_data={
            "source": account.source.value,
            "public_profile": ({"creator_tags": creator_tags} if creator_tags is not None else {}),
        },
        matched_influencer_id=account.influencer_id,
        matched_platform_account_id=account.id,
        match_type=ImportMatchType.PLATFORM_ACCOUNT_ID,
        action=ImportRowAction.NO_CHANGE,
        merge_plan=None,
        warnings=[],
        errors=[],
        preview_revision=1,
        plan_hash=uuid4().hex * 2,
        committed_action=ImportRowAction.NO_CHANGE,
        committed_at=NOW,
    )
    session.add(row)
    await session.flush()
    state = await session.scalar(
        select(InfluencerSourceState).where(
            InfluencerSourceState.platform_account_id == account.id,
            InfluencerSourceState.source == account.source,
        )
    )
    if state is None:
        state = InfluencerSourceState(
            influencer_id=account.influencer_id,
            platform_account_id=account.id,
            source=account.source,
            source_updated_at=NOW,
            source_data={"creator_tags": creator_tags or []},
            source_data_hash=uuid4().hex * 2,
            state_version=1,
            last_import_job_id=job.id,
            last_import_row_id=row.id,
        )
        session.add(state)
    else:
        state.source_updated_at = NOW
        if creator_tags is not None:
            state.source_data = {"creator_tags": creator_tags}
        state.source_data_hash = uuid4().hex * 2
        state.state_version += 1
        state.last_import_job_id = job.id
        state.last_import_row_id = row.id
    await session.flush()
    return job


async def _create_schema(connection: AsyncConnection) -> None:
    """SQLite cannot compile the project’s PostgreSQL guarded-metric indexes."""

    metric_table = Base.metadata.tables["influencer_current_metrics"]
    metric_indexes = tuple(metric_table.indexes)
    metric_table.indexes.clear()
    try:
        await connection.run_sync(Base.metadata.create_all)
    finally:
        metric_table.indexes.update(metric_indexes)


def test_content_activity_candidate_facts_are_hydrated_in_fixed_set_based_history_queries() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        statements: list[str] = []

        def track_statement(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", track_statement)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                assert context.operator is not None
                first = await _account(session, owner=context.operator)
                second = await _account(session, owner=context.operator)
                first_observation = await _content_activity_observation(
                    session,
                    first,
                    last_publication_at=NOW - timedelta(days=90),
                )
                second_observation = await _content_activity_observation(
                    session,
                    second,
                    last_publication_at=NOW - timedelta(days=10),
                )
                await session.commit()
                statements.clear()

                facts = await CandidatePoolRepository(session)._hydrate_fact_batch(
                    accounts=(first, second),
                    collection_context=None,
                    source_collection_job_id=None,
                    buyer=False,
                    market_prospect_rule=False,
                    department_id=context.department.id,
                    as_of=NOW,
                    freshness_policy=FreshnessPolicy(),
                    include_content_activity=True,
                )

                assert [fact.content_activity is not None for fact in facts] == [True, True]
                assert facts[0].content_activity is not None
                assert facts[0].content_activity.trusted_observation_id == first_observation.id
                assert facts[0].content_activity.last_publication_at == NOW - timedelta(days=90)
                assert facts[1].content_activity is not None
                assert facts[1].content_activity.trusted_observation_id == second_observation.id
                assert facts[1].content_activity.last_publication_at == NOW - timedelta(days=10)
                activity_history_reads = [
                    statement
                    for statement in statements
                    if "content_activity_observations" in statement.lower()
                    and statement.lstrip().upper().startswith("SELECT")
                ]
                # Latest/trusted XHS and latest/accepted returned-scope Huitun
                # queries are each set-based for the whole account batch;
                # never a per-candidate history read or provider call.
                assert len(activity_history_reads) == 4
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", track_statement)
            await engine.dispose()

    asyncio.run(scenario())


def test_grey_dolphin_activity_fact_requires_one_confirmed_coherent_metric_snapshot() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                assert context.operator is not None
                account = await _account(
                    session,
                    owner=context.operator,
                    source=DataSource.HUITUN,
                )
                collection = await _collection_job(
                    session,
                    context=context,
                    industry="beauty",
                )
                stored_file = StoredImportFile(
                    sha256=uuid4().hex * 2,
                    storage_key=f"targeting/{uuid4().hex}.csv",
                    size=1,
                    detected_type=StoredFileType.CSV,
                    detected_mime="text/csv",
                    expires_at=NOW + timedelta(days=1),
                )
                session.add(stored_file)
                await session.flush()
                job = ImportJob(
                    collection_job_id=collection.id,
                    department_id=context.department.id,
                    operator_id=context.operator.id,
                    source_type=ImportSourceType.MANUAL_HUITUN_EXPORT,
                    status=ImportJobStatus.COMPLETED,
                    preview_revision=1,
                    confirmed_revision=1,
                )
                session.add(job)
                await session.flush()
                import_file = ImportJobFile(
                    import_job_id=job.id,
                    stored_file_id=stored_file.id,
                    position=1,
                    original_filename="huitun.csv",
                    status=ImportJobFileStatus.READY,
                    source_acquired_at=NOW - timedelta(days=1),
                    source_acquired_at_origin=SourceAcquiredAtOrigin.SERVER_DEFAULT,
                    source_acquired_at_confirmation_required=False,
                )
                session.add(import_file)
                await session.flush()
                row = ImportRow(
                    import_job_id=job.id,
                    import_job_file_id=import_file.id,
                    row_number=2,
                    raw_data={},
                    normalized_data={},
                    matched_influencer_id=account.influencer_id,
                    matched_platform_account_id=account.id,
                    match_type=ImportMatchType.PLATFORM_ACCOUNT_ID,
                    action=ImportRowAction.NO_CHANGE,
                    merge_plan=None,
                    warnings=[],
                    errors=[],
                    preview_revision=1,
                    plan_hash=uuid4().hex * 2,
                    committed_action=ImportRowAction.NO_CHANGE,
                    committed_at=NOW,
                )
                session.add(row)
                await session.flush()
                session.add(
                    InfluencerCurrentMetrics(
                        influencer_id=account.influencer_id,
                        platform_account_id=account.id,
                        source=DataSource.HUITUN,
                        source_updated_at=NOW - timedelta(days=1),
                        metrics={"notes_7d": 0, "notes_60d": 0},
                        metrics_hash=uuid4().hex * 2,
                        last_import_job_id=job.id,
                        last_import_row_id=row.id,
                    )
                )
                await session.commit()

                fact = (
                    await CandidatePoolRepository(session)._hydrate_fact_batch(
                        accounts=(account,),
                        collection_context=None,
                        source_collection_job_id=None,
                        buyer=False,
                        market_prospect_rule=False,
                        department_id=context.department.id,
                        as_of=NOW,
                        freshness_policy=FreshnessPolicy(),
                        include_content_activity=True,
                    )
                )[0]

                assert fact.grey_dolphin_activity is not None
                assert fact.grey_dolphin_activity.observed_at == NOW - timedelta(days=1)
                assert fact.grey_dolphin_activity.notes_7d == 0
                assert fact.grey_dolphin_activity.notes_60d == 0
                assert fact.grey_dolphin_activity.import_row_id == row.id
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_pool_create_uses_shared_idempotency_record_and_single_audits() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                assert context.operator is not None
                account = await _account(session, owner=context.operator)
                contact_secret = "pool-create-contact-secret@example.com"
                session.add(
                    InfluencerContact(
                        influencer_id=account.influencer_id,
                        platform_account_id=account.id,
                        type=ContactType.EMAIL,
                        value=contact_secret,
                        normalized_value=contact_secret,
                        source=DataSource.MANUAL,
                        validation_status=ContactValidationStatus.VALID,
                        is_current=True,
                        possible_duplicate_contact=False,
                        first_seen_at=NOW,
                        last_seen_at=NOW,
                    )
                )
                await session.commit()

                service = _service(session)
                idempotency_key = "candidate-pool-create-a1"
                input_payload = CandidatePoolCreateInput(
                    name="A1 seller candidates",
                    kind=CandidatePoolKind.POTENTIAL_SELLER,
                    policy=SellerTargetingPolicy(contact_availability=ContactFilter.HAS_EMAIL),
                )
                first = await service.create_pool(
                    context,
                    input_payload,
                    idempotency_key=idempotency_key,
                    ip="198.51.100.10",
                    user_agent="candidate-pool-a1-service-test",
                )
                record = await session.scalar(
                    select(Phase3AIdempotencyRecord).where(
                        Phase3AIdempotencyRecord.department_id == context.department.id,
                        Phase3AIdempotencyRecord.operation_scope
                        == Phase3AOperationScope.CANDIDATE_POOL_CREATE,
                        Phase3AIdempotencyRecord.idempotency_key == idempotency_key,
                    )
                )
                assert record is not None
                assert record.result_entity_id == first.id
                assert record.result_schema_version == 1
                assert len(record.request_hash) == 64
                assert set(record.result_payload) == set(first.model_dump(mode="json"))
                assert record.result_payload == first.model_dump(mode="json")
                assert (
                    len(
                        json.dumps(
                            record.result_payload, separators=(",", ":"), sort_keys=True
                        ).encode()
                    )
                    <= 16 * 1024
                )

                pool_created = list(
                    await session.scalars(
                        select(AuditLog).where(
                            AuditLog.action == AuditAction.CANDIDATE_POOL_CREATED,
                            AuditLog.entity_id == first.id,
                        )
                    )
                )
                policy_created = list(
                    await session.scalars(
                        select(AuditLog).where(
                            AuditLog.action == AuditAction.TARGETING_POLICY_CREATED,
                            AuditLog.entity_id == first.current_policy_id,
                        )
                    )
                )
                assert len(pool_created) == 1
                assert len(policy_created) == 1
                assert {audit.result for audit in (*pool_created, *policy_created)} == {
                    AuditResult.SUCCESS
                }
                assert {
                    (audit.department_id, audit.operator_id, audit.ip, audit.user_agent)
                    for audit in (*pool_created, *policy_created)
                } == {
                    (
                        context.department.id,
                        context.operator.id,
                        "198.51.100.10",
                        "candidate-pool-a1-service-test",
                    )
                }

                replay = await service.create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="A1 seller candidates",
                        kind=CandidatePoolKind.POTENTIAL_SELLER,
                        policy=SellerTargetingPolicy(contact_availability=ContactFilter.HAS_EMAIL),
                    ),
                    idempotency_key=idempotency_key,
                    ip="198.51.100.10",
                    user_agent="candidate-pool-a1-service-test",
                )
                assert replay == first
                assert len(list(await session.scalars(select(CandidatePool)))) == 1
                assert (
                    len(
                        list(
                            await session.scalars(
                                select(Phase3AIdempotencyRecord).where(
                                    Phase3AIdempotencyRecord.department_id == context.department.id,
                                    Phase3AIdempotencyRecord.operation_scope
                                    == Phase3AOperationScope.CANDIDATE_POOL_CREATE,
                                    Phase3AIdempotencyRecord.idempotency_key == idempotency_key,
                                )
                            )
                        )
                    )
                    == 1
                )
                assert (
                    len(
                        list(
                            await session.scalars(
                                select(AuditLog).where(
                                    AuditLog.action == AuditAction.CANDIDATE_POOL_CREATED,
                                    AuditLog.entity_id == first.id,
                                )
                            )
                        )
                    )
                    == 1
                )
                assert (
                    len(
                        list(
                            await session.scalars(
                                select(AuditLog).where(
                                    AuditLog.action == AuditAction.TARGETING_POLICY_CREATED,
                                    AuditLog.entity_id == first.current_policy_id,
                                )
                            )
                        )
                    )
                    == 1
                )

                with pytest.raises(TargetingError) as conflict:
                    await service.create_pool(
                        context,
                        CandidatePoolCreateInput(
                            name="A1 seller candidates changed",
                            kind=CandidatePoolKind.POTENTIAL_SELLER,
                            policy=SellerTargetingPolicy(
                                contact_availability=ContactFilter.HAS_EMAIL
                            ),
                        ),
                        idempotency_key=idempotency_key,
                    )
                assert conflict.value.status_code == 409
                assert conflict.value.code == "IDEMPOTENCY_KEY_REUSED"
                assert len(list(await session.scalars(select(CandidatePool)))) == 1

                # A key is scoped by Department, not globally across tenants.
                other_context = await _actor(session, role=Role.OPERATOR)
                await session.commit()
                other = await service.create_pool(
                    other_context,
                    input_payload,
                    idempotency_key=idempotency_key,
                )
                records = list(
                    await session.scalars(
                        select(Phase3AIdempotencyRecord).where(
                            Phase3AIdempotencyRecord.operation_scope
                            == Phase3AOperationScope.CANDIDATE_POOL_CREATE,
                            Phase3AIdempotencyRecord.idempotency_key == idempotency_key,
                        )
                    )
                )
                assert other.department_id == other_context.department.id
                assert {record.department_id for record in records} == {
                    first.department_id,
                    other_context.department.id,
                }
                assert len(records) == 2

                # Replay records and Audit metadata must not retain Contact data,
                # request bodies, policy definitions, or Idempotency-Key values.
                serialized_records = json.dumps(
                    [record.result_payload for record in records],
                    default=str,
                    sort_keys=True,
                )
                serialized_audits = json.dumps(
                    [
                        {"before": audit.before, "after": audit.after}
                        for audit in (*pool_created, *policy_created)
                    ],
                    default=str,
                    sort_keys=True,
                )
                for serialized in (serialized_records, serialized_audits):
                    assert contact_secret not in serialized
                    assert "normalized_value" not in serialized
                    assert "definition" not in serialized
                    assert idempotency_key not in serialized
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_policy_create_uses_minimal_replay_dto_and_single_audits() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                assert context.operator is not None
                account = await _account(session, owner=context.operator)
                contact_secret = "policy-create-contact-secret@example.com"
                session.add(
                    InfluencerContact(
                        influencer_id=account.influencer_id,
                        platform_account_id=account.id,
                        type=ContactType.EMAIL,
                        value=contact_secret,
                        normalized_value=contact_secret,
                        source=DataSource.MANUAL,
                        validation_status=ContactValidationStatus.VALID,
                        is_current=True,
                        possible_duplicate_contact=False,
                        first_seen_at=NOW,
                        last_seen_at=NOW,
                    )
                )
                await session.commit()

                service = _service(session)
                # Scope isolation permits the same opaque key for the initial
                # Pool create and this distinct TargetingPolicy create operation.
                shared_key = "candidate-policy-create-a1"
                pool = await service.create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="Policy A1 seller candidates",
                        kind=CandidatePoolKind.POTENTIAL_SELLER,
                        policy=SellerTargetingPolicy(),
                    ),
                    idempotency_key=shared_key,
                )
                policy_input = TargetingPolicyCreateInput(
                    policy=SellerTargetingPolicy(contact_availability=ContactFilter.HAS_EMAIL)
                )
                first = await service.append_policy(
                    context,
                    pool.id,
                    policy_input,
                    idempotency_key=shared_key,
                    ip="198.51.100.11",
                    user_agent="targeting-policy-a1-service-test",
                )
                record = await session.scalar(
                    select(Phase3AIdempotencyRecord).where(
                        Phase3AIdempotencyRecord.department_id == context.department.id,
                        Phase3AIdempotencyRecord.operation_scope
                        == Phase3AOperationScope.TARGETING_POLICY_CREATE,
                        Phase3AIdempotencyRecord.idempotency_key == shared_key,
                    )
                )
                assert record is not None
                assert record.result_entity_id == first.id
                assert record.result_schema_version == 1
                assert len(record.request_hash) == 64
                expected_payload_keys = {
                    "id",
                    "pool_id",
                    "version",
                    "schema_version",
                    "canonical_hash",
                    "created_by_operator_id",
                    "created_at",
                }
                assert set(record.result_payload) == expected_payload_keys
                assert record.result_payload == first.model_dump(mode="json")
                assert (
                    len(
                        json.dumps(
                            record.result_payload, separators=(",", ":"), sort_keys=True
                        ).encode()
                    )
                    <= 16 * 1024
                )

                pool_updates = list(
                    await session.scalars(
                        select(AuditLog).where(
                            AuditLog.action == AuditAction.CANDIDATE_POOL_UPDATED,
                            AuditLog.entity_id == pool.id,
                        )
                    )
                )
                policy_created = list(
                    await session.scalars(
                        select(AuditLog).where(
                            AuditLog.action == AuditAction.TARGETING_POLICY_CREATED,
                            AuditLog.entity_id == first.id,
                        )
                    )
                )
                assert len(pool_updates) == 1
                assert len(policy_created) == 1
                assert {audit.result for audit in (*pool_updates, *policy_created)} == {
                    AuditResult.SUCCESS
                }
                assert {
                    (audit.department_id, audit.operator_id, audit.ip, audit.user_agent)
                    for audit in (*pool_updates, *policy_created)
                } == {
                    (
                        context.department.id,
                        context.operator.id,
                        "198.51.100.11",
                        "targeting-policy-a1-service-test",
                    )
                }

                replay = await service.append_policy(
                    context,
                    pool.id,
                    TargetingPolicyCreateInput(
                        policy=SellerTargetingPolicy(contact_availability=ContactFilter.HAS_EMAIL)
                    ),
                    idempotency_key=shared_key,
                    ip="198.51.100.11",
                    user_agent="targeting-policy-a1-service-test",
                )
                assert replay == first
                assert (
                    len(
                        list(
                            await session.scalars(
                                select(Phase3AIdempotencyRecord).where(
                                    Phase3AIdempotencyRecord.department_id == context.department.id,
                                    Phase3AIdempotencyRecord.operation_scope
                                    == Phase3AOperationScope.TARGETING_POLICY_CREATE,
                                    Phase3AIdempotencyRecord.idempotency_key == shared_key,
                                )
                            )
                        )
                    )
                    == 1
                )
                assert (
                    len(
                        list(
                            await session.scalars(
                                select(AuditLog).where(
                                    AuditLog.action == AuditAction.CANDIDATE_POOL_UPDATED,
                                    AuditLog.entity_id == pool.id,
                                )
                            )
                        )
                    )
                    == 1
                )
                assert (
                    len(
                        list(
                            await session.scalars(
                                select(AuditLog).where(
                                    AuditLog.action == AuditAction.TARGETING_POLICY_CREATED,
                                    AuditLog.entity_id == first.id,
                                )
                            )
                        )
                    )
                    == 1
                )

                # The Pool path is part of the semantic request hash, so this
                # exact policy/key pair cannot replay against another Pool.
                other_pool = await service.create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="Other policy path",
                        kind=CandidatePoolKind.POTENTIAL_SELLER,
                        policy=SellerTargetingPolicy(),
                    ),
                    idempotency_key="candidate-policy-path-a1",
                )
                assert len(await service.list_policies(context, other_pool.id)) == 1
                with pytest.raises(TargetingError) as path_conflict:
                    await service.append_policy(
                        context,
                        other_pool.id,
                        policy_input,
                        idempotency_key=shared_key,
                    )
                assert path_conflict.value.status_code == 409
                assert path_conflict.value.code == "IDEMPOTENCY_KEY_REUSED"

                with pytest.raises(TargetingError) as conflict:
                    await service.append_policy(
                        context,
                        pool.id,
                        TargetingPolicyCreateInput(
                            policy=SellerTargetingPolicy(followers=IntegerRange(minimum=1_000))
                        ),
                        idempotency_key=shared_key,
                    )
                assert conflict.value.status_code == 409
                assert conflict.value.code == "IDEMPOTENCY_KEY_REUSED"

                serialized_record = json.dumps(record.result_payload, default=str, sort_keys=True)
                serialized_audits = json.dumps(
                    [
                        {"before": audit.before, "after": audit.after}
                        for audit in (*pool_updates, *policy_created)
                    ],
                    default=str,
                    sort_keys=True,
                )
                for serialized in (serialized_record, serialized_audits):
                    assert contact_secret not in serialized
                    assert "normalized_value" not in serialized
                    assert "definition" not in serialized
                    assert shared_key not in serialized
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_pool_create_rolls_back_domain_and_idempotency_when_audit_write_fails() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                await session.commit()
                service = _service(session)

                def fail_audit_write(**_kwargs: object) -> AuditLog:
                    raise RuntimeError("synthetic audit write failure")

                service.audit.add = fail_audit_write  # type: ignore[method-assign]
                with pytest.raises(RuntimeError, match="synthetic audit write failure"):
                    await service.create_pool(
                        context,
                        CandidatePoolCreateInput(
                            name="Atomicity regression",
                            kind=CandidatePoolKind.POTENTIAL_SELLER,
                            policy=SellerTargetingPolicy(tags_exact_any=("beauty",)),
                        ),
                        idempotency_key="candidate-pool-audit-rollback",
                    )

                assert list(await session.scalars(select(CandidatePool))) == []
                assert list(await session.scalars(select(database_models.TargetingPolicy))) == []
                assert list(await session.scalars(select(Phase3AIdempotencyRecord))) == []
                assert list(await session.scalars(select(AuditLog))) == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_run_idempotency_materialization_and_member_evidence() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.MANAGER)
                assert context.operator is not None
                account = await _account(session, owner=context.operator)
                await session.commit()

                service = _service(session)
                pool = await service.create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="Seller candidates",
                        kind="POTENTIAL_SELLER",
                        policy=SellerTargetingPolicy(tags_exact_any=("beauty",)),
                    ),
                    idempotency_key="targeting-pool-run-lifecycle",
                )
                first = await service.reserve_run(
                    context,
                    pool_id=pool.id,
                    idempotency_key="targeting-run-1",
                    ip="198.51.100.12",
                    user_agent="candidate-pool-run-service-test",
                )
                replay = await service.reserve_run(
                    context,
                    pool_id=pool.id,
                    idempotency_key="targeting-run-1",
                    ip="198.51.100.12",
                    user_agent="candidate-pool-run-service-test",
                )
                assert replay.id == first.id
                assert replay.idempotent_replay is True
                assert replay.input_watermark is not None
                assert replay.input_watermark["policy_hash"]
                assert "max_current_contact_updated_at" in replay.input_watermark
                requested_audits = list(
                    await session.scalars(
                        select(AuditLog).where(
                            AuditLog.action == AuditAction.CANDIDATE_POOL_RUN_REQUESTED,
                            AuditLog.entity_id == first.id,
                        )
                    )
                )
                assert len(requested_audits) == 1
                assert requested_audits[0].result is AuditResult.SUCCESS
                assert requested_audits[0].ip == "198.51.100.12"
                assert requested_audits[0].user_agent == "candidate-pool-run-service-test"

                materialization_commits: list[object] = []

                def record_materialization_commit(_session: object) -> None:
                    materialization_commits.append(object())

                event.listen(session.sync_session, "after_commit", record_materialization_commit)
                try:
                    await service.materialize_run(first.id)
                finally:
                    event.remove(
                        session.sync_session, "after_commit", record_materialization_commit
                    )
                assert len(materialization_commits) == 1
                completed = await service.get_run(context, pool_id=pool.id, run_id=first.id)
                assert completed.status.value == "COMPLETED"
                assert completed.match_count == 1
                assert completed.unknown_count == 0
                assert completed.not_match_count == 0
                assert completed.input_watermark is not None
                assert completed.input_watermark["policy_version"] == 1
                assert completed.input_watermark["materialized_member_count"] == 1
                assert completed.input_watermark["materialized_at"]
                completed_audits = list(
                    await session.scalars(
                        select(AuditLog).where(
                            AuditLog.action == AuditAction.CANDIDATE_POOL_RUN_COMPLETED,
                            AuditLog.entity_id == first.id,
                        )
                    )
                )
                assert len(completed_audits) == 1
                assert completed_audits[0].result is AuditResult.SUCCESS
                assert completed_audits[0].ip == "worker"
                assert completed_audits[0].user_agent == "celery:materialize_candidate_pool_run"
                first_as_of = completed.as_of
                first_policy_hash = completed.input_watermark["policy_hash"]

                members = await service.list_run_members(
                    context,
                    pool_id=pool.id,
                    run_id=first.id,
                    cursor=None,
                    limit=50,
                )
                assert len(members.items) == 1
                member = members.items[0]
                assert member.platform_account_id == account.id
                assert member.result.value == "MATCH"
                assert member.evidence_hash
                assert "contact@example.com" not in str(member.redacted_evidence)

                delivery_replay = await service.materialize_run(first.id)
                assert delivery_replay is not None
                assert delivery_replay.status is CandidatePoolRunStatus.COMPLETED
                assert (
                    len(
                        list(
                            await session.scalars(
                                select(AuditLog).where(
                                    AuditLog.action == AuditAction.CANDIDATE_POOL_RUN_COMPLETED,
                                    AuditLog.entity_id == first.id,
                                )
                            )
                        )
                    )
                    == 1
                )
                replayed_members = await service.list_run_members(
                    context,
                    pool_id=pool.id,
                    run_id=first.id,
                    cursor=None,
                    limit=50,
                )
                assert len(replayed_members.items) == 1

                adjusted = await service.reserve_run(
                    context,
                    pool_id=pool.id,
                    payload=CandidatePoolRunRequest(
                        base_policy_id=pool.current_policy_id,
                        expected_pool_version=pool.version,
                        policy=SellerTargetingPolicy(followers=IntegerRange(minimum=100)),
                    ),
                    idempotency_key="targeting-adjusted-run-lifecycle",
                )
                policies = await service.list_policies(context, pool.id)
                assert [item.version for item in policies] == [1, 2]
                assert policies[0].id == first.policy_id
                assert policies[1].id == adjusted.policy_id
                assert (
                    policies[1].canonical_hash
                    == SellerTargetingPolicy(followers=IntegerRange(minimum=100)).canonical_hash
                )
                with pytest.raises(TargetingError, match="different request") as raised:
                    await service.reserve_run(
                        context,
                        pool_id=pool.id,
                        idempotency_key="targeting-run-1",
                    )
                assert raised.value.code == "IDEMPOTENCY_CONFLICT"
                assert (
                    len(
                        tuple(
                            (
                                await session.execute(
                                    select(database_models.CandidatePoolRun).where(
                                        database_models.CandidatePoolRun.pool_id == pool.id
                                    )
                                )
                            ).scalars()
                        )
                    )
                    == 2
                )

                second = await service.reserve_run(
                    context,
                    pool_id=pool.id,
                    idempotency_key="targeting-run-2",
                )
                assert second.policy_id == adjusted.policy_id
                assert second.input_watermark is not None
                assert second.input_watermark["policy_version"] == 2
                await service.materialize_run(second.id)

                historical = await service.reserve_run(
                    context,
                    pool_id=pool.id,
                    payload=CandidatePoolRunRequest(policy_id=first.policy_id),
                    idempotency_key="targeting-historical-run-lifecycle",
                )
                assert historical.policy_id == first.policy_id
                assert historical.input_watermark is not None
                assert historical.input_watermark["policy_version"] == 1

                first_after_rerun = await service.get_run(
                    context,
                    pool_id=pool.id,
                    run_id=first.id,
                )
                assert first_after_rerun.policy_id == first.policy_id
                assert first_after_rerun.as_of == first_as_of
                assert first_after_rerun.input_watermark is not None
                assert first_after_rerun.input_watermark["policy_version"] == 1
                assert first_after_rerun.input_watermark["policy_hash"] == first_policy_hash
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_explicit_long_inactivity_runtime_uses_planner_and_counts_full_policy_candidates() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.MANAGER)
                assert context.operator is not None
                account = await _account(session, owner=context.operator)
                await session.commit()

                calls: list[UUID] = []
                observed_at = datetime.now(UTC)

                async def provider_enricher(
                    platform_account_id: UUID,
                ) -> LongInactivityProviderRefresh:
                    calls.append(platform_account_id)
                    return LongInactivityProviderRefresh(
                        content_activity=ContentActivityFact(
                            trusted_observed_at=observed_at,
                            trusted_observation_status=ContentActivityObservationStatus.COMPLETE,
                            trusted_coverage_status=(
                                ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET
                            ),
                            trusted_activity_result=ContentActivityResult.PUBLICATION_FOUND,
                            last_publication_at=observed_at - timedelta(days=120),
                        ),
                        accepted_trusted_observation=True,
                    )

                service = _service(
                    session,
                    long_inactivity_provider_enricher=provider_enricher,
                    long_inactivity_provider_budget=12,
                )
                pool = await service.create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="Explicit long inactivity candidates",
                        kind=CandidatePoolKind.POTENTIAL_SELLER,
                        policy=SellerTargetingPolicy(
                            long_inactivity=LongInactivityConstraint(minimum_inactive_days=90)
                        ),
                    ),
                    idempotency_key="explicit-long-inactivity-pool",
                )
                run = await service.reserve_run(
                    context,
                    pool_id=pool.id,
                    payload=CandidatePoolRunRequest(
                        long_inactivity_enrichment=LongInactivityEnrichmentRequest(
                            planned_assignable_target=1,
                            max_provider_enrichment=12,
                        )
                    ),
                    idempotency_key="explicit-long-inactivity-run",
                )

                completed = await service.materialize_run(run.id)

                assert completed is not None
                assert completed.status is CandidatePoolRunStatus.COMPLETED
                assert completed.match_count == 1
                assert completed.unknown_count == 0
                assert calls == [account.id]
                assert completed.input_watermark is not None
                execution = completed.input_watermark["long_inactivity_enrichment"]
                assert execution["result"]["provider_calls_attempted"] == 1
                assert execution["result"]["target_reached"] is True
                assert execution["result"]["budget_reached"] is False
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_adjusted_run_uses_current_pool_pointer_and_rejects_cross_pool_policy() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.MANAGER)
                service = _service(session)
                first_pool = await service.create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="First Seller Pool",
                        kind=CandidatePoolKind.POTENTIAL_SELLER,
                        policy=SellerTargetingPolicy(tags_exact_any=("beauty",)),
                    ),
                    idempotency_key="c3a-first-pool",
                )
                second_pool = await service.create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="Second Seller Pool",
                        kind=CandidatePoolKind.POTENTIAL_SELLER,
                        policy=SellerTargetingPolicy(tags_exact_any=("fashion",)),
                    ),
                    idempotency_key="c3a-second-pool",
                )
                assert first_pool.current_policy_id is not None
                assert second_pool.current_policy_id is not None

                stale_request = CandidatePoolRunRequest(
                    base_policy_id=first_pool.current_policy_id,
                    expected_pool_version=first_pool.version - 1,
                    policy=SellerTargetingPolicy(followers=IntegerRange(minimum=100)),
                )
                with pytest.raises(TargetingError) as stale:
                    await service.reserve_run(
                        context,
                        pool_id=first_pool.id,
                        payload=stale_request,
                        idempotency_key="c3a-stale-adjustment",
                    )
                assert stale.value.code == "VERSION_CONFLICT"

                with pytest.raises(TargetingError) as cross_pool:
                    await service.reserve_run(
                        context,
                        pool_id=first_pool.id,
                        payload=CandidatePoolRunRequest(policy_id=second_pool.current_policy_id),
                        idempotency_key="c3a-cross-pool-policy",
                    )
                assert cross_pool.value.code == "TARGETING_POLICY_NOT_FOUND"

                adjustment = CandidatePoolRunRequest(
                    base_policy_id=first_pool.current_policy_id,
                    expected_pool_version=first_pool.version,
                    policy=SellerTargetingPolicy(followers=IntegerRange(minimum=100)),
                )
                first = await service.reserve_run(
                    context,
                    pool_id=first_pool.id,
                    payload=adjustment,
                    idempotency_key="c3a-adjustment",
                )
                replay = await service.reserve_run(
                    context,
                    pool_id=first_pool.id,
                    payload=adjustment,
                    idempotency_key="c3a-adjustment",
                )
                assert replay.id == first.id
                assert replay.idempotent_replay is True

                with pytest.raises(TargetingError) as conflict:
                    await service.reserve_run(
                        context,
                        pool_id=first_pool.id,
                        payload=CandidatePoolRunRequest(
                            base_policy_id=first_pool.current_policy_id,
                            expected_pool_version=first_pool.version,
                            policy=SellerTargetingPolicy(followers=IntegerRange(minimum=200)),
                        ),
                        idempotency_key="c3a-adjustment",
                    )
                assert conflict.value.code == "IDEMPOTENCY_CONFLICT"

                with pytest.raises(TargetingError) as stale_after_update:
                    await service.reserve_run(
                        context,
                        pool_id=first_pool.id,
                        payload=adjustment,
                        idempotency_key="c3a-stale-after-update",
                    )
                assert stale_after_update.value.code == "VERSION_CONFLICT"

                persisted = await session.get(CandidatePool, first_pool.id)
                assert persisted is not None
                assert persisted.version == first_pool.version + 1
                assert persisted.current_policy_id == first.policy_id
                policies = await service.list_policies(context, first_pool.id)
                assert [policy.version for policy in policies] == [1, 2]
                runs = await service.list_runs(
                    context,
                    pool_id=first_pool.id,
                    cursor=None,
                    limit=50,
                )
                assert [run.id for run in runs.items] == [first.id]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_long_inactivity_runtime_stops_after_twelve_additional_fully_eligible_candidates() -> None:
    async def scenario() -> None:
        observed_at = datetime.now(UTC)
        calls: list[UUID] = []

        async def provider_enricher(
            platform_account_id: UUID,
        ) -> LongInactivityProviderRefresh:
            calls.append(platform_account_id)
            return LongInactivityProviderRefresh(
                content_activity=ContentActivityFact(
                    trusted_observed_at=observed_at,
                    trusted_observation_status=ContentActivityObservationStatus.COMPLETE,
                    trusted_coverage_status=ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET,
                    trusted_activity_result=ContentActivityResult.PUBLICATION_FOUND,
                    last_publication_at=observed_at - timedelta(days=120),
                ),
                accepted_trusted_observation=True,
            )

        service = CandidatePoolService(
            cast(AsyncSession, MagicMock()),
            freshness_policy=FreshnessPolicy(),
            long_inactivity_provider_enricher=provider_enricher,
            long_inactivity_provider_budget=50,
        )
        policy = SellerTargetingPolicy(
            long_inactivity=LongInactivityConstraint(minimum_inactive_days=90)
        )
        evaluations: list[tuple[CandidateFactBundle, TargetingEvaluation]] = []
        for index in range(30):
            trusted = (
                ContentActivityFact(
                    trusted_observed_at=observed_at,
                    trusted_observation_status=ContentActivityObservationStatus.COMPLETE,
                    trusted_coverage_status=ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET,
                    trusted_activity_result=ContentActivityResult.PUBLICATION_FOUND,
                    last_publication_at=observed_at - timedelta(days=120),
                )
                if index < 18
                else None
            )
            facts = CandidateFactBundle(
                influencer_id=UUID(int=index + 1),
                platform_account_id=UUID(int=index + 101),
                platform=Platform.XIAOHONGSHU,
                content_activity=trusted,
            )
            evaluations.append(
                (facts, service._evaluate_targeting(policy, facts, as_of=observed_at))
            )

        final_evaluations, execution = await service._execute_long_inactivity_enrichment(
            run=cast(CandidatePoolRun, SimpleNamespace(as_of=observed_at)),
            policy=policy,
            request=LongInactivityEnrichmentRequest(
                planned_assignable_target=30,
                max_provider_enrichment=50,
            ),
            evaluations=evaluations,
        )

        assert len(calls) == 12
        assert execution.fully_eligible_count == 30
        assert execution.metrics.provider_calls_attempted == 12
        assert execution.metrics.stopped_by_planned_target is True
        assert (
            sum(evaluation.result.value == "MATCH" for _facts, evaluation in final_evaluations)
            == 30
        )

    asyncio.run(scenario())


def test_stale_trusted_fact_preserved_after_failed_refresh_stays_unknown_and_ineligible() -> None:
    async def scenario() -> None:
        execution_as_of = datetime.now(UTC)
        old_observation_id = uuid4()
        stale_fact = ContentActivityFact(
            trusted_observation_id=old_observation_id,
            trusted_observed_at=execution_as_of - timedelta(days=8),
            trusted_observation_status=ContentActivityObservationStatus.COMPLETE,
            trusted_coverage_status=ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET,
            trusted_activity_result=ContentActivityResult.PUBLICATION_FOUND,
            last_publication_at=execution_as_of - timedelta(days=120),
        )
        calls: list[UUID] = []

        async def provider_failure(platform_account_id: UUID) -> LongInactivityProviderRefresh:
            calls.append(platform_account_id)
            # Content Activity correctly retains the prior trusted projection,
            # but this failed attempt did not accept that old observation.
            return LongInactivityProviderRefresh(
                content_activity=stale_fact,
                accepted_trusted_observation=False,
            )

        service = CandidatePoolService(
            cast(AsyncSession, MagicMock()),
            freshness_policy=FreshnessPolicy(),
            long_inactivity_provider_enricher=provider_failure,
            long_inactivity_provider_budget=1,
        )
        policy = SellerTargetingPolicy(
            long_inactivity=LongInactivityConstraint(minimum_inactive_days=90)
        )
        facts = CandidateFactBundle(
            influencer_id=uuid4(),
            platform_account_id=uuid4(),
            platform=Platform.XIAOHONGSHU,
            content_activity=stale_fact,
        )
        initial = service._evaluate_targeting(policy, facts, as_of=execution_as_of)
        assert initial.result.value == "UNKNOWN"

        final, execution = await service._execute_long_inactivity_enrichment(
            run=cast(CandidatePoolRun, SimpleNamespace(as_of=execution_as_of)),
            policy=policy,
            request=LongInactivityEnrichmentRequest(
                planned_assignable_target=1,
                max_provider_enrichment=1,
            ),
            evaluations=[(facts, initial)],
        )

        assert calls == [facts.platform_account_id]
        assert final[0][1].result.value == "UNKNOWN"
        assert execution.fully_eligible_count == 0
        assert execution.metrics.provider_calls_attempted == 1
        assert execution.metrics.stopped_by_planned_target is False
        assert execution.metrics.stopped_by_provider_budget is True
        assert stale_fact.trusted_observation_id == old_observation_id
        assert stale_fact.trusted_observed_at == execution_as_of - timedelta(days=8)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("publication_age_days", "expected_result", "expected_fully_eligible"),
    [
        pytest.param(120, "MATCH", 1, id="new-inactive-observation-matches"),
        pytest.param(1, "NOT_MATCH", 0, id="new-recent-observation-does-not-match"),
    ],
)
def test_successful_new_trusted_refresh_is_evaluated_at_candidate_run_as_of(
    publication_age_days: int,
    expected_result: str,
    expected_fully_eligible: int,
) -> None:
    async def scenario() -> None:
        execution_as_of = datetime.now(UTC)
        stale_fact = ContentActivityFact(
            trusted_observation_id=uuid4(),
            trusted_observed_at=execution_as_of - timedelta(days=8),
            trusted_observation_status=ContentActivityObservationStatus.COMPLETE,
            trusted_coverage_status=ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET,
            trusted_activity_result=ContentActivityResult.PUBLICATION_FOUND,
            last_publication_at=execution_as_of - timedelta(days=120),
        )
        fresh_fact = ContentActivityFact(
            trusted_observation_id=uuid4(),
            trusted_observed_at=execution_as_of - timedelta(minutes=1),
            trusted_observation_status=ContentActivityObservationStatus.COMPLETE,
            trusted_coverage_status=ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET,
            trusted_activity_result=ContentActivityResult.PUBLICATION_FOUND,
            last_publication_at=execution_as_of - timedelta(days=publication_age_days),
        )

        async def provider_success(_platform_account_id: UUID) -> LongInactivityProviderRefresh:
            return LongInactivityProviderRefresh(
                content_activity=fresh_fact,
                accepted_trusted_observation=True,
            )

        service = CandidatePoolService(
            cast(AsyncSession, MagicMock()),
            freshness_policy=FreshnessPolicy(),
            long_inactivity_provider_enricher=provider_success,
            long_inactivity_provider_budget=1,
        )
        policy = SellerTargetingPolicy(
            long_inactivity=LongInactivityConstraint(minimum_inactive_days=90)
        )
        facts = CandidateFactBundle(
            influencer_id=uuid4(),
            platform_account_id=uuid4(),
            platform=Platform.XIAOHONGSHU,
            content_activity=stale_fact,
        )

        final, execution = await service._execute_long_inactivity_enrichment(
            run=cast(CandidatePoolRun, SimpleNamespace(as_of=execution_as_of)),
            policy=policy,
            request=LongInactivityEnrichmentRequest(
                planned_assignable_target=1,
                max_provider_enrichment=1,
            ),
            evaluations=[
                (facts, service._evaluate_targeting(policy, facts, as_of=execution_as_of))
            ],
        )

        assert final[0][1].result.value == expected_result
        assert execution.fully_eligible_count == expected_fully_eligible
        assert execution.metrics.provider_calls_attempted == 1
        assert execution.metrics.stopped_by_planned_target is (expected_fully_eligible == 1)
        assert execution.metrics.stopped_by_provider_budget is True

    asyncio.run(scenario())


def test_fresh_trusted_cache_short_circuits_failed_refresh_callback() -> None:
    async def scenario() -> None:
        execution_as_of = datetime.now(UTC)
        calls: list[UUID] = []
        fresh_fact = ContentActivityFact(
            trusted_observation_id=uuid4(),
            trusted_observed_at=execution_as_of - timedelta(minutes=1),
            trusted_observation_status=ContentActivityObservationStatus.COMPLETE,
            trusted_coverage_status=ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET,
            trusted_activity_result=ContentActivityResult.PUBLICATION_FOUND,
            last_publication_at=execution_as_of - timedelta(days=120),
        )

        async def provider_failure(platform_account_id: UUID) -> LongInactivityProviderRefresh:
            calls.append(platform_account_id)
            raise AssertionError("fresh trusted cache must not be refreshed")

        service = CandidatePoolService(
            cast(AsyncSession, MagicMock()),
            freshness_policy=FreshnessPolicy(),
            long_inactivity_provider_enricher=provider_failure,
            long_inactivity_provider_budget=1,
        )
        policy = SellerTargetingPolicy(
            long_inactivity=LongInactivityConstraint(minimum_inactive_days=90)
        )
        facts = CandidateFactBundle(
            influencer_id=uuid4(),
            platform_account_id=uuid4(),
            platform=Platform.XIAOHONGSHU,
            content_activity=fresh_fact,
        )
        initial = service._evaluate_targeting(policy, facts, as_of=execution_as_of)

        _final, execution = await service._execute_long_inactivity_enrichment(
            run=cast(CandidatePoolRun, SimpleNamespace(as_of=execution_as_of)),
            policy=policy,
            request=LongInactivityEnrichmentRequest(
                planned_assignable_target=1,
                max_provider_enrichment=1,
            ),
            evaluations=[(facts, initial)],
        )

        assert initial.result.value == "MATCH"
        assert calls == []
        assert execution.fully_eligible_count == 1
        assert execution.metrics.provider_calls_attempted == 0

    asyncio.run(scenario())


def test_missing_seller_evidence_materializes_unknown_member() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                assert context.operator is not None
                account = await _account(session, owner=context.operator)
                await session.commit()

                service = _service(session)
                pool = await service.create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="Followers unknown",
                        kind="POTENTIAL_SELLER",
                        policy=SellerTargetingPolicy(followers=IntegerRange(minimum=100)),
                    ),
                    idempotency_key="targeting-pool-unknown-evidence",
                )
                run = await service.reserve_run(
                    context,
                    pool_id=pool.id,
                    idempotency_key="targeting-run-unknown",
                )
                await service.materialize_run(run.id)

                completed = await service.get_run(context, pool_id=pool.id, run_id=run.id)
                assert completed.match_count == 0
                assert completed.unknown_count == 1
                assert completed.not_match_count == 0
                members = await service.list_run_members(
                    context,
                    pool_id=pool.id,
                    run_id=run.id,
                    cursor=None,
                    limit=50,
                )
                assert len(members.items) == 1
                assert members.items[0].platform_account_id == account.id
                assert members.items[0].result is CandidateResult.UNKNOWN
                assert "FOLLOWERS_MISSING" in members.items[0].reason_codes
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_invalid_seller_tag_evidence_materializes_unknown_member() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                assert context.operator is not None
                account = await _account(session, owner=context.operator)
                account.source_tags = cast(list[str], ["beauty", 1])
                await session.commit()

                service = _service(session)
                pool = await service.create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="Tags unknown",
                        kind="POTENTIAL_SELLER",
                        policy=SellerTargetingPolicy(tags_exact_any=("beauty",)),
                    ),
                    idempotency_key="targeting-pool-invalid-tags",
                )
                run = await service.reserve_run(
                    context,
                    pool_id=pool.id,
                    idempotency_key="targeting-run-invalid-tags",
                )
                completed = await service.materialize_run(run.id)

                assert completed is not None
                assert completed.status is CandidatePoolRunStatus.COMPLETED
                assert completed.match_count == 0
                assert completed.unknown_count == 1
                assert completed.not_match_count == 0
                members = await service.list_run_members(
                    context,
                    pool_id=pool.id,
                    run_id=run.id,
                    cursor=None,
                    limit=50,
                )
                assert members.items[0].platform_account_id == account.id
                assert members.items[0].reason_codes == ("TRACK_MISSING",)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_buyer_materialization_uses_committed_source_provenance_once_per_account() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                assert context.operator is not None
                account = await _account(
                    session,
                    owner=context.operator,
                    source_tags=["科技"],
                )
                source_collection = await _collection_job(
                    session,
                    context=context,
                    industry="美食",
                    subdirection=None,
                )
                unrelated_collection = await _collection_job(
                    session,
                    context=context,
                    industry="科技",
                    subdirection=None,
                )
                source_first = await _committed_import(
                    session,
                    context=context,
                    collection=source_collection,
                    account=account,
                    creator_tags=["科技"],
                )
                source_second = await _committed_import(
                    session,
                    context=context,
                    collection=source_collection,
                    account=account,
                    creator_tags=["科技"],
                )
                classification_import = await _committed_import(
                    session,
                    context=context,
                    collection=unrelated_collection,
                    account=account,
                    creator_tags=["科技"],
                )
                await session.commit()

                service = _service(session)
                pool = await service.create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="Buyer provenance",
                        kind="POTENTIAL_BUYER",
                        source_collection_job_id=source_collection.id,
                        policy=BuyerTargetingPolicy(taxonomy=resolve_buyer_taxonomy_v1()),
                    ),
                    idempotency_key="targeting-pool-buyer-provenance",
                )
                run = await service.reserve_run(
                    context,
                    pool_id=pool.id,
                    idempotency_key="targeting-buyer-provenance",
                )
                completed = await service.materialize_run(run.id)

                assert completed is not None
                assert completed.status is CandidatePoolRunStatus.COMPLETED
                assert completed.match_count == 1
                assert completed.unknown_count == 0
                assert completed.not_match_count == 0
                members = await service.list_run_members(
                    context,
                    pool_id=pool.id,
                    run_id=run.id,
                    cursor=None,
                    limit=50,
                )
                assert len(members.items) == 1
                member = members.items[0]
                assert member.platform_account_id == account.id
                assert member.result is CandidateResult.MATCH
                assert member.redacted_evidence["collection_context"][
                    "provenance_import_job_ids"
                ] == sorted((str(source_first.id), str(source_second.id)))
                assert member.redacted_evidence["creator_classification"][
                    "provenance_import_job_ids"
                ] == [str(classification_import.id)]
                assert "purchased_account" not in str(member.redacted_evidence)
                historical = await service.reserve_run(
                    context,
                    pool_id=pool.id,
                    payload=CandidatePoolRunRequest(policy_id=run.policy_id),
                    idempotency_key="targeting-buyer-provenance-historical",
                )
                assert historical.policy_id == run.policy_id
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_buyer_bootstrap_derives_trusted_policy_and_reuses_one_first_run() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                assert context.operator is not None
                account = await _account(session, owner=context.operator, source_tags=["科技"])
                collection = await _collection_job(
                    session,
                    context=context,
                    industry="美妆",
                    subdirection=None,
                )
                await _committed_import(
                    session,
                    context=context,
                    collection=collection,
                    account=account,
                    creator_tags=["科技"],
                )
                await session.commit()

                service = _service(session)
                first = await service.bootstrap_buyer_screening(
                    context,
                    collection_job_id=collection.id,
                    idempotency_key="buyer-bootstrap-first",
                )
                assert first.reused_existing_pool is False
                assert first.run.status is CandidatePoolRunStatus.PENDING
                assert first.policy.version == 1
                policy = await service.get_policy(
                    context,
                    pool_id=first.pool.id,
                    policy_id=first.policy.id,
                )
                assert isinstance(policy.definition, BuyerTargetingPolicy)
                assert policy.definition.taxonomy.artifact_hash == BUYER_TAXONOMY_V1_HASH

                second = await service.bootstrap_buyer_screening(
                    context,
                    collection_job_id=collection.id,
                    idempotency_key="buyer-bootstrap-second-click",
                )
                assert second.reused_existing_pool is True
                assert second.pool.id == first.pool.id
                assert second.policy.id == first.policy.id
                assert second.run.id == first.run.id

                completed = await service.materialize_run(first.run.id)
                assert completed is not None
                assert completed.status is CandidatePoolRunStatus.COMPLETED
                audit_actions = tuple(
                    (
                        await session.execute(
                            select(AuditLog.action).where(
                                AuditLog.entity_id.in_(
                                    (first.pool.id, first.policy.id, first.run.id)
                                )
                            )
                        )
                    ).scalars()
                )
                assert AuditAction.CANDIDATE_POOL_CREATED in audit_actions
                assert AuditAction.TARGETING_POLICY_CREATED in audit_actions
                assert AuditAction.CANDIDATE_POOL_RUN_REQUESTED in audit_actions
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_buyer_bootstrap_fails_closed_for_source_scope_and_client_category() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                other_context = await _actor(session, role=Role.OPERATOR)
                foreign = await _collection_job(
                    session, context=other_context, industry="美妆", subdirection=None
                )
                incomplete = await _collection_job(
                    session, context=context, industry="美妆", subdirection=None
                )
                unmapped = await _collection_job(
                    session, context=context, industry="未映射行业", subdirection=None
                )
                assert context.operator is not None
                account = await _account(session, owner=context.operator, source_tags=["科技"])
                await _committed_import(
                    session,
                    context=context,
                    collection=unmapped,
                    account=account,
                    creator_tags=["科技"],
                )
                await session.commit()

                service = _service(session)
                with pytest.raises(TargetingError) as cross_department:
                    await service.bootstrap_buyer_screening(
                        context,
                        collection_job_id=foreign.id,
                        idempotency_key="buyer-bootstrap-foreign",
                    )
                assert cross_department.value.status_code == 404
                assert cross_department.value.code == "COLLECTION_JOB_NOT_FOUND"

                with pytest.raises(TargetingError) as missing_provenance:
                    await service.bootstrap_buyer_screening(
                        context,
                        collection_job_id=incomplete.id,
                        idempotency_key="buyer-bootstrap-incomplete",
                    )
                assert missing_provenance.value.status_code == 409
                assert missing_provenance.value.code == "BUYER_SOURCE_PROVENANCE_INCOMPLETE"

                with pytest.raises(TargetingError) as unmapped_category:
                    await service.bootstrap_buyer_screening(
                        context,
                        collection_job_id=unmapped.id,
                        idempotency_key="buyer-bootstrap-unmapped",
                    )
                assert unmapped_category.value.status_code == 409
                assert unmapped_category.value.code == "BUYER_CLIENT_CATEGORY_UNMAPPED"

                viewer = await _actor(session, role=Role.VIEWER)
                with pytest.raises(TargetingError) as viewer_rejected:
                    await service.bootstrap_buyer_screening(
                        viewer,
                        collection_job_id=incomplete.id,
                        idempotency_key="buyer-bootstrap-viewer",
                    )
                assert viewer_rejected.value.status_code == 403
                assert viewer_rejected.value.code == "PERMISSION_DENIED"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_buyer_future_run_materializes_every_lead_tier_without_expanding_seller_rows() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                assert context.operator is not None
                collection = await _collection_job(
                    session, context=context, industry="科技", subdirection=None
                )
                expected = {
                    "美食": BuyerLeadTier.HIGH,
                    "休闲": BuyerLeadTier.CHANGED,
                    "数码": BuyerLeadTier.RELATED,
                    "科技": BuyerLeadTier.SAME_CATEGORY,
                    None: BuyerLeadTier.UNKNOWN,
                }
                for creator_tag in expected:
                    account = await _account(
                        session,
                        owner=context.operator,
                        source_tags=[] if creator_tag is None else [creator_tag],
                    )
                    await _committed_import(
                        session,
                        context=context,
                        collection=collection,
                        account=account,
                        creator_tags=None if creator_tag is None else [creator_tag],
                    )
                await session.commit()

                service = _service(session)
                pool = await service.create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="Buyer lead tiers",
                        kind=CandidatePoolKind.POTENTIAL_BUYER,
                        source_collection_job_id=collection.id,
                        policy=BuyerTargetingPolicy(taxonomy=resolve_buyer_taxonomy_v1()),
                    ),
                    idempotency_key="buyer-lead-tiers-pool",
                )
                run = await service.reserve_run(
                    context,
                    pool_id=pool.id,
                    idempotency_key="buyer-lead-tiers-run",
                )
                completed = await service.materialize_run(run.id)

                assert completed is not None
                assert (
                    completed.match_count,
                    completed.unknown_count,
                    completed.not_match_count,
                ) == (1, 2, 2)
                assert completed.input_watermark is not None
                assert completed.input_watermark["materialized_member_count"] == 5
                page = await service.list_run_members(
                    context, pool_id=pool.id, run_id=run.id, cursor=None, limit=50
                )
                assert {item.buyer_lead_tier for item in page.items} == set(expected.values())
                assert all(item.buyer_relation_summary is not None for item in page.items)
                changed = await service.list_run_members(
                    context,
                    pool_id=pool.id,
                    run_id=run.id,
                    cursor=None,
                    limit=50,
                    buyer_lead_tier=BuyerLeadTier.CHANGED,
                )
                assert [item.buyer_lead_tier for item in changed.items] == [BuyerLeadTier.CHANGED]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_buyer_classification_requires_provenance_for_retained_tags() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                assert context.operator is not None
                account = await _account(
                    session,
                    owner=context.operator,
                    source_tags=["科技"],
                )
                collection = await _collection_job(
                    session,
                    context=context,
                    industry="美食",
                    subdirection=None,
                )
                tagged_import = await _committed_import(
                    session,
                    context=context,
                    collection=collection,
                    account=account,
                    creator_tags=["科技"],
                )
                tagless_import = await _committed_import(
                    session,
                    context=context,
                    collection=collection,
                    account=account,
                    creator_tags=None,
                )
                await session.commit()

                service = _service(session)
                pool = await service.create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="Buyer retained-tag provenance",
                        kind=CandidatePoolKind.POTENTIAL_BUYER,
                        source_collection_job_id=collection.id,
                        policy=BuyerTargetingPolicy(taxonomy=resolve_buyer_taxonomy_v1()),
                    ),
                    idempotency_key="targeting-pool-buyer-retained",
                )
                run = await service.reserve_run(
                    context,
                    pool_id=pool.id,
                    idempotency_key="targeting-buyer-retained-tags",
                )
                completed = await service.materialize_run(run.id)

                assert completed is not None
                assert completed.match_count == 0
                assert completed.unknown_count == 1
                assert completed.not_match_count == 0
                members = await service.list_run_members(
                    context,
                    pool_id=pool.id,
                    run_id=run.id,
                    cursor=None,
                    limit=50,
                )
                assert members.items[0].reason_codes == ("CREATOR_CLASSIFICATION_MISSING",)
                evidence = members.items[0].redacted_evidence
                assert evidence["creator_classification"]["provenance_import_job_ids"] == []
                assert str(tagged_import.id) in str(evidence)
                assert str(tagless_import.id) in str(evidence)
                assert "CATEGORY_MISMATCH" not in str(evidence)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_buyer_pool_rejects_unreviewed_taxonomy_activation() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                collection = await _collection_job(
                    session,
                    context=context,
                    industry="beauty",
                    subdirection=None,
                )
                await session.commit()

                with pytest.raises(TargetingError) as rejected:
                    await _service(session).create_pool(
                        context,
                        CandidatePoolCreateInput(
                            name="Unreviewed buyer taxonomy",
                            kind=CandidatePoolKind.POTENTIAL_BUYER,
                            source_collection_job_id=collection.id,
                            policy=BuyerTargetingPolicy(
                                taxonomy=TaxonomyDefinition(
                                    taxonomy_version="unreviewed-v1",
                                    reviewed=False,
                                    categories=("beauty",),
                                )
                            ),
                        ),
                        idempotency_key="targeting-pool-unreviewed",
                    )

                assert rejected.value.status_code == 422
                assert rejected.value.code == "BUYER_TAXONOMY_UNREVIEWED"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_buyer_pool_rejects_client_reviewed_taxonomy_activation() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                collection = await _collection_job(
                    session, context=context, industry="美食", subdirection=None
                )
                await session.commit()

                with pytest.raises(TargetingError) as rejected:
                    await _service(session).create_pool(
                        context,
                        CandidatePoolCreateInput(
                            name="Client-reviewed buyer taxonomy",
                            kind=CandidatePoolKind.POTENTIAL_BUYER,
                            source_collection_job_id=collection.id,
                            policy=BuyerTargetingPolicy(
                                taxonomy=TaxonomyDefinition(
                                    taxonomy_version="client-v1",
                                    reviewed=True,
                                    categories=("FOOD",),
                                )
                            ),
                        ),
                        idempotency_key="targeting-pool-client-reviewed",
                    )

                assert rejected.value.status_code == 422
                assert rejected.value.code == "BUYER_TAXONOMY_UNTRUSTED"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_buyer_pool_rejects_cross_department_source_collection() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                other_context = await _actor(session, role=Role.OPERATOR)
                foreign_collection = await _collection_job(
                    session,
                    context=other_context,
                    industry="beauty",
                )
                await session.commit()

                with pytest.raises(TargetingError) as raised:
                    await _service(session).create_pool(
                        context,
                        CandidatePoolCreateInput(
                            name="Foreign source",
                            kind="POTENTIAL_BUYER",
                            source_collection_job_id=foreign_collection.id,
                            policy=BuyerTargetingPolicy(taxonomy=resolve_buyer_taxonomy_v1()),
                        ),
                        idempotency_key="targeting-pool-cross-department",
                    )
                assert raised.value.status_code == 404
                assert raised.value.code == "COLLECTION_JOB_NOT_FOUND"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_buyer_materialization_uses_reservation_time_collection_context() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                collection = await _collection_job(session, context=context, industry="beauty")
                await session.commit()

                service = _service(session)
                pool = await service.create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="Buyer candidates",
                        kind="POTENTIAL_BUYER",
                        source_collection_job_id=collection.id,
                        policy=BuyerTargetingPolicy(taxonomy=resolve_buyer_taxonomy_v1()),
                    ),
                    idempotency_key="targeting-pool-buyer-snapshot",
                )
                run = await service.reserve_run(
                    context,
                    pool_id=pool.id,
                    idempotency_key="targeting-buyer-snapshot",
                )
                assert run.input_watermark is not None
                assert run.input_watermark["collection_context"]["industry"] == "beauty"

                collection.industry = "gaming"
                await session.commit()
                observed_contexts: list[CollectionContextSnapshot | None] = []

                async def empty_batches(
                    **kwargs: object,
                ) -> AsyncIterator[tuple[CandidateFactBundle, ...]]:
                    context_snapshot = kwargs["collection_context"]
                    assert context_snapshot is None or isinstance(
                        context_snapshot, CollectionContextSnapshot
                    )
                    observed_contexts.append(context_snapshot)
                    if False:
                        yield ()

                service.repository.iter_candidate_fact_batches = empty_batches  # type: ignore[method-assign]
                completed = await service.materialize_run(run.id)

                assert completed is not None
                assert len(observed_contexts) == 1
                snapshot = observed_contexts[0]
                assert snapshot is not None
                assert snapshot.source_collection_job_id == collection.id
                assert snapshot.industry == "beauty"
                assert snapshot.subdirection == "makeup"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_invalid_persisted_policy_finishes_the_run_as_failed() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                assert context.operator is not None
                await _account(session, owner=context.operator)
                await session.commit()

                service = _service(session)
                pool = await service.create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="Invalid policy",
                        kind="POTENTIAL_SELLER",
                        policy=SellerTargetingPolicy(tags_exact_any=("beauty",)),
                    ),
                    idempotency_key="targeting-pool-invalid-policy",
                )
                run = await service.reserve_run(
                    context,
                    pool_id=pool.id,
                    idempotency_key="targeting-run-invalid-policy",
                )
                policy = await session.get(database_models.TargetingPolicy, run.policy_id)
                assert policy is not None
                policy.definition = {"schema_version": 999, "policy_type": "SELLER_V1"}
                await session.commit()

                failed = await service.materialize_run(run.id)

                assert failed is not None
                assert failed.status is CandidatePoolRunStatus.FAILED
                assert failed.error_code == "TARGETING_POLICY_INVALID"
                assert failed.error_message == "Targeting policy is invalid"
                failure_audits = list(
                    await session.scalars(
                        select(AuditLog).where(
                            AuditLog.action == AuditAction.CANDIDATE_POOL_RUN_FAILED,
                            AuditLog.entity_id == run.id,
                        )
                    )
                )
                assert len(failure_audits) == 1
                assert failure_audits[0].result is AuditResult.FAILED
                assert failure_audits[0].after == {
                    "pool_id": str(pool.id),
                    "policy_id": str(run.policy_id),
                    "status": "FAILED",
                    "match_count": 0,
                    "unknown_count": 0,
                    "not_match_count": 0,
                    "error_code": "TARGETING_POLICY_INVALID",
                }
                assert failure_audits[0].ip == "worker"
                assert failure_audits[0].user_agent == "celery:materialize_candidate_pool_run"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_viewer_contact_evidence_is_masked_and_cross_department_is_hidden() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                manager = await _actor(session, role=Role.MANAGER)
                assert manager.operator is not None
                account = await _account(session, owner=manager.operator)
                session.add(
                    InfluencerContact(
                        influencer_id=account.influencer_id,
                        platform_account_id=account.id,
                        type=ContactType.EMAIL,
                        value="contact@example.com",
                        normalized_value="contact@example.com",
                        source=DataSource.MANUAL,
                        validation_status=ContactValidationStatus.VALID,
                        is_current=True,
                        possible_duplicate_contact=False,
                        first_seen_at=NOW,
                        last_seen_at=NOW,
                    )
                )
                await session.commit()
                service = _service(session)
                pool = await service.create_pool(
                    manager,
                    CandidatePoolCreateInput(
                        name="Email sellers",
                        kind="POTENTIAL_SELLER",
                        policy=SellerTargetingPolicy(contact_availability=ContactFilter.HAS_EMAIL),
                    ),
                    idempotency_key="targeting-pool-viewer",
                )
                run = await service.reserve_run(
                    manager,
                    pool_id=pool.id,
                    idempotency_key="targeting-run-viewer",
                )
                await service.materialize_run(run.id)

                viewer_operator = Operator(
                    department_id=manager.department.id,
                    name="Targeting viewer",
                    role=Role.VIEWER,
                    status=OperatorStatus.ACTIVE,
                )
                session.add(viewer_operator)
                await session.flush()
                viewer = AuthContext(
                    department=manager.department,
                    operator=viewer_operator,
                    role=Role.MANAGER,
                    auth_session=manager.auth_session,
                )
                members = await service.list_run_members(
                    viewer,
                    pool_id=pool.id,
                    run_id=run.id,
                    cursor=None,
                    limit=50,
                )
                rendered = str(members.items[0].redacted_evidence)
                assert "contact@example.com" not in rendered
                assert "email" not in rendered
                assert "***" in rendered
                assert members.items[0].reason_codes == ("CONTACT_EVIDENCE_REDACTED",)

                other = await _actor(session, role=Role.OPERATOR)
                await session.commit()
                with pytest.raises(TargetingError) as hidden:
                    await service.get_pool(other, pool.id)
                assert hidden.value.status_code == 404
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_scoped_pool_create_keeps_actor_owner_and_target_department_distinct() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                super_context = await _actor(session, role=Role.SUPER_ADMIN)
                assert super_context.operator is not None
                same_department_owner = Operator(
                    department_id=super_context.department.id,
                    name="Same Department owner",
                    role=Role.OPERATOR,
                    status=OperatorStatus.ACTIVE,
                )
                inactive_owner = Operator(
                    department_id=super_context.department.id,
                    name="Inactive owner",
                    role=Role.OPERATOR,
                    status=OperatorStatus.DISABLED,
                )
                target_department = Department(
                    name=f"target-{uuid4().hex}",
                    password_hash="not-used-by-targeting-tests",
                    status=DepartmentStatus.ACTIVE,
                    session_days=30,
                )
                session.add_all((same_department_owner, inactive_owner, target_department))
                await session.flush()
                target_owner = Operator(
                    department_id=target_department.id,
                    name="Target Department owner",
                    role=Role.OPERATOR,
                    status=OperatorStatus.ACTIVE,
                )
                session.add(target_owner)
                await session.commit()

                service = _service(session)
                default_owner = await service.create_pool(
                    super_context,
                    CandidatePoolCreateInput(
                        name="Default owner",
                        kind=CandidatePoolKind.POTENTIAL_SELLER,
                        policy=SellerTargetingPolicy(),
                    ),
                    idempotency_key="candidate-owner-default",
                )
                assert default_owner.owner_operator_id == super_context.operator.id

                explicit_owner = await service.create_pool(
                    super_context,
                    CandidatePoolCreateInput(
                        name="Explicit owner",
                        kind=CandidatePoolKind.POTENTIAL_SELLER,
                        owner_operator_id=same_department_owner.id,
                        policy=SellerTargetingPolicy(),
                    ),
                    idempotency_key="candidate-owner-explicit",
                )
                assert explicit_owner.owner_operator_id == same_department_owner.id

                replay = await service.create_pool(
                    super_context,
                    CandidatePoolCreateInput(
                        name="Explicit owner",
                        kind=CandidatePoolKind.POTENTIAL_SELLER,
                        owner_operator_id=same_department_owner.id,
                        policy=SellerTargetingPolicy(),
                    ),
                    idempotency_key="candidate-owner-explicit",
                )
                assert replay == explicit_owner
                with pytest.raises(TargetingError) as changed_owner:
                    await service.create_pool(
                        super_context,
                        CandidatePoolCreateInput(
                            name="Explicit owner",
                            kind=CandidatePoolKind.POTENTIAL_SELLER,
                            owner_operator_id=super_context.operator.id,
                            policy=SellerTargetingPolicy(),
                        ),
                        idempotency_key="candidate-owner-explicit",
                    )
                assert changed_owner.value.status_code == 409
                assert changed_owner.value.code == "IDEMPOTENCY_KEY_REUSED"

                with pytest.raises(TargetingError) as inactive:
                    await service.create_pool(
                        super_context,
                        CandidatePoolCreateInput(
                            name="Inactive owner",
                            kind=CandidatePoolKind.POTENTIAL_SELLER,
                            owner_operator_id=inactive_owner.id,
                            policy=SellerTargetingPolicy(),
                        ),
                        idempotency_key="candidate-owner-inactive",
                    )
                assert inactive.value.status_code == 404
                assert inactive.value.code == "OPERATOR_NOT_FOUND"

                with pytest.raises(TargetingError) as missing_cross_owner:
                    await service.create_pool(
                        super_context,
                        CandidatePoolCreateInput(
                            name="Missing target owner",
                            kind=CandidatePoolKind.POTENTIAL_SELLER,
                            policy=SellerTargetingPolicy(),
                        ),
                        department_id=target_department.id,
                        idempotency_key="candidate-owner-cross-missing",
                    )
                assert missing_cross_owner.value.status_code == 409
                assert missing_cross_owner.value.code == "TARGET_DEPARTMENT_OWNER_REQUIRED"

                with pytest.raises(TargetingError) as wrong_cross_owner:
                    await service.create_pool(
                        super_context,
                        CandidatePoolCreateInput(
                            name="Wrong target owner",
                            kind=CandidatePoolKind.POTENTIAL_SELLER,
                            owner_operator_id=super_context.operator.id,
                            policy=SellerTargetingPolicy(),
                        ),
                        department_id=target_department.id,
                        idempotency_key="candidate-owner-cross-wrong",
                    )
                assert wrong_cross_owner.value.status_code == 404
                assert wrong_cross_owner.value.code == "OPERATOR_NOT_FOUND"

                cross_department = await service.create_pool(
                    super_context,
                    CandidatePoolCreateInput(
                        name="Target Department pool",
                        kind=CandidatePoolKind.POTENTIAL_SELLER,
                        owner_operator_id=target_owner.id,
                        policy=SellerTargetingPolicy(),
                    ),
                    department_id=target_department.id,
                    idempotency_key="candidate-owner-cross-valid",
                )
                assert cross_department.department_id == target_department.id
                assert cross_department.owner_operator_id == target_owner.id

                own_page = await service.list_pools(
                    super_context,
                    cursor=None,
                    limit=50,
                )
                assert {pool.id for pool in own_page.items} == {
                    default_owner.id,
                    explicit_owner.id,
                }
                target_page = await service.list_pools(
                    super_context,
                    cursor=None,
                    limit=50,
                    department_id=target_department.id,
                )
                assert {pool.id for pool in target_page.items} == {cross_department.id}

                audit = await session.scalar(
                    select(AuditLog).where(
                        AuditLog.action == AuditAction.CANDIDATE_POOL_CREATED,
                        AuditLog.entity_id == cross_department.id,
                    )
                )
                assert audit is not None
                assert audit.department_id == target_department.id
                assert audit.operator_id == super_context.operator.id
                assert audit.after == {
                    "owner_operator_id": str(target_owner.id),
                    "status": "ACTIVE",
                    "version": 2,
                    "current_policy_id": str(cross_department.current_policy_id),
                    "cross_department_override": True,
                }

                non_super = await _actor(session, role=Role.OPERATOR)
                await session.commit()
                with pytest.raises(TargetingError) as hidden:
                    await service.get_pool(
                        non_super,
                        cross_department.id,
                        department_id=target_department.id,
                    )
                assert hidden.value.status_code == 404
                assert hidden.value.code == "RESOURCE_NOT_FOUND"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_immutable_policy_runs_and_members_use_closed_keyset_reads() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                assert context.operator is not None
                first_account = await _account(session, owner=context.operator)
                second_account = await _account(session, owner=context.operator)
                await session.commit()

                service = _service(session)
                pool = await service.create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="Immutable reads",
                        kind=CandidatePoolKind.POTENTIAL_SELLER,
                        policy=SellerTargetingPolicy(),
                    ),
                    idempotency_key="candidate-immutable-reads",
                )
                assert pool.current_policy_id is not None
                run_ids = tuple(UUID(int=index) for index in (101, 102, 103))
                runs = tuple(
                    CandidatePoolRun(
                        id=run_id,
                        pool_id=pool.id,
                        policy_id=pool.current_policy_id,
                        as_of=NOW,
                        input_watermark=None,
                        status=CandidatePoolRunStatus.COMPLETED,
                        match_count=1,
                        unknown_count=1,
                        not_match_count=0,
                        error_code=None,
                        error_message=None,
                        idempotency_key=f"immutable-read-run-{index}",
                        request_hash=f"{index:064x}",
                    )
                    for index, run_id in zip((101, 102, 103), run_ids, strict=True)
                )
                session.add_all(runs)
                await session.flush()
                session.add_all(
                    (
                        CandidatePoolMember(
                            id=UUID(int=201),
                            run_id=run_ids[0],
                            influencer_id=first_account.influencer_id,
                            platform_account_id=first_account.id,
                            result=CandidateResult.MATCH,
                            reason_codes=[],
                            redacted_evidence={},
                            evidence_hash="a" * 64,
                        ),
                        CandidatePoolMember(
                            id=UUID(int=202),
                            run_id=run_ids[0],
                            influencer_id=second_account.influencer_id,
                            platform_account_id=second_account.id,
                            result=CandidateResult.UNKNOWN,
                            reason_codes=[],
                            redacted_evidence={},
                            evidence_hash="b" * 64,
                        ),
                    )
                )
                await session.commit()

                policy = await service.get_policy(
                    context,
                    pool_id=pool.id,
                    policy_id=pool.current_policy_id,
                )
                assert policy.id == pool.current_policy_id
                with pytest.raises(TargetingError) as missing_policy:
                    await service.get_policy(
                        context,
                        pool_id=pool.id,
                        policy_id=UUID(int=999),
                    )
                assert missing_policy.value.status_code == 404
                assert missing_policy.value.code == "TARGETING_POLICY_NOT_FOUND"

                first_run_page = await service.list_runs(
                    context,
                    pool_id=pool.id,
                    cursor=None,
                    limit=2,
                )
                assert [run.id for run in first_run_page.items] == list(run_ids[:2])
                assert first_run_page.next_cursor == run_ids[1]
                second_run_page = await service.list_runs(
                    context,
                    pool_id=pool.id,
                    cursor=first_run_page.next_cursor,
                    limit=2,
                )
                assert [run.id for run in second_run_page.items] == [run_ids[2]]
                assert second_run_page.next_cursor is None

                match_members = await service.list_run_members(
                    context,
                    pool_id=pool.id,
                    run_id=run_ids[0],
                    cursor=None,
                    limit=50,
                    result=CandidateResult.MATCH,
                )
                assert [member.id for member in match_members.items] == [UUID(int=201)]
                unknown_members = await service.list_run_members(
                    context,
                    pool_id=pool.id,
                    run_id=run_ids[0],
                    cursor=None,
                    limit=50,
                    result=CandidateResult.UNKNOWN,
                )
                assert [member.id for member in unknown_members.items] == [UUID(int=202)]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_candidate_pool_readiness_projections_are_canonical_historical_and_bounded() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.MANAGER)
                assert context.operator is not None
                account = await _account(session, owner=context.operator)
                influencer = await session.get(Influencer, account.influencer_id)
                assert influencer is not None
                account.platform_account_id = None
                account.account_handle = None
                await session.commit()

                service = _service(session)
                pool = await service.create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="Canonical projection",
                        kind=CandidatePoolKind.POTENTIAL_SELLER,
                        policy=SellerTargetingPolicy(tags_exact_any=("beauty",)),
                    ),
                    idempotency_key="candidate-canonical-projection",
                )
                assert pool.owner.id == pool.owner_operator_id
                assert pool.owner.name == context.operator.name

                run = await service.reserve_run(
                    context,
                    pool_id=pool.id,
                    idempotency_key="candidate-canonical-projection-run",
                )
                await service.materialize_run(run.id)

                influencer.status = InfluencerStatus.DISABLED
                influencer.deleted_at = NOW
                account.is_active = False
                context.operator.status = OperatorStatus.DISABLED
                context.operator.name = "Disabled historical Pool owner"
                await session.commit()

                listed = await service.list_pools(context, cursor=None, limit=50)
                assert listed.items == (await service.get_pool(context, pool.id),)
                assert listed.items[0].owner.id == pool.owner_operator_id
                assert listed.items[0].owner.name == "Disabled historical Pool owner"
                assert listed.items[0].owner.status is OperatorStatus.DISABLED

                members = await service.list_run_members(
                    context,
                    pool_id=pool.id,
                    run_id=run.id,
                    cursor=None,
                    limit=50,
                )
                assert len(members.items) == 1
                member = members.items[0]
                assert member.influencer.id == member.influencer_id == influencer.id
                assert member.influencer.status is InfluencerStatus.DISABLED
                assert member.platform_account.id == member.platform_account_id == account.id
                assert member.platform_account.platform_account_id is None
                assert member.platform_account.account_handle is None
                assert member.platform_account.is_active is False
                assert not ({"email", "phone", "wechat", "contact"} & set(member.model_dump()))

                repository = CandidatePoolRepository(session)
                statements: list[str] = []

                def capture(
                    _connection: object,
                    _cursor: object,
                    statement: str,
                    _parameters: object,
                    _context: object,
                    _executemany: bool,
                ) -> None:
                    if statement.lstrip().upper().startswith("SELECT"):
                        statements.append(statement)

                event.listen(engine.sync_engine, "before_cursor_execute", capture)
                try:
                    projection_page = await repository.list_run_members(
                        pool_id=pool.id,
                        department_id=context.department.id,
                        run_id=run.id,
                        cursor=None,
                        limit=50,
                    )
                    pool_page = await repository.list_pools(
                        department_id=context.department.id,
                        cursor=None,
                        limit=50,
                    )
                finally:
                    event.remove(engine.sync_engine, "before_cursor_execute", capture)
                assert len(projection_page.items) == 1
                assert len(pool_page.items) == 1
                assert len(statements) == 2
                assert "JOIN influencer_platform_accounts" in statements[0]
                assert "JOIN influencers" in statements[0]
                assert "influencer_contacts" not in statements[0]
                assert "JOIN operators" in statements[1]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_candidate_pool_create_replay_enriches_legacy_owner_and_rejects_cross_department() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session, role=Role.OPERATOR)
                assert context.operator is not None
                await session.commit()
                service = _service(session)
                payload = CandidatePoolCreateInput(
                    name="Legacy owner replay",
                    kind=CandidatePoolKind.POTENTIAL_SELLER,
                    policy=SellerTargetingPolicy(),
                )
                created = await service.create_pool(
                    context,
                    payload,
                    idempotency_key="candidate-legacy-owner-replay",
                )
                record = await session.scalar(
                    select(Phase3AIdempotencyRecord).where(
                        Phase3AIdempotencyRecord.operation_scope
                        == Phase3AOperationScope.CANDIDATE_POOL_CREATE
                    )
                )
                assert record is not None
                record.result_payload = {
                    key: value for key, value in record.result_payload.items() if key != "owner"
                }
                context.operator.name = "Current canonical replay owner"
                context.operator.status = OperatorStatus.DISABLED
                await session.commit()

                replay = await service.create_pool(
                    context,
                    payload,
                    idempotency_key="candidate-legacy-owner-replay",
                )
                assert replay.id == created.id
                assert replay.owner.id == replay.owner_operator_id
                assert replay.owner.name == "Current canonical replay owner"
                assert replay.owner.status is OperatorStatus.DISABLED

                other = await _actor(session, role=Role.OPERATOR)
                assert other.operator is not None
                record.result_payload = {
                    **record.result_payload,
                    "owner_operator_id": str(other.operator.id),
                }
                await session.commit()
                with pytest.raises(TargetingError) as invalid:
                    await service.create_pool(
                        context,
                        payload,
                        idempotency_key="candidate-legacy-owner-replay",
                    )
                assert invalid.value.status_code == 409
                assert invalid.value.code == "IDEMPOTENCY_RECORD_INVALID"
        finally:
            await engine.dispose()

    asyncio.run(scenario())

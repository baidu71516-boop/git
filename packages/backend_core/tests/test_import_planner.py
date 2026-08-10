import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.contracts import (
    CanonicalContact,
    CanonicalInfluencerRecord,
    PlatformIdentity,
)
from backend_core.imports.enums import ImportMatchType, ImportRowAction
from backend_core.imports.hashing import hash_document
from backend_core.imports.planner import ImportPlanner, build_preview_context
from backend_core.imports.repository import ImportRepository
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
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
    InfluencerSourceState,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

SOURCE_TIME = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)
MAPPING_HASH = "a" * 64


@asynccontextmanager
async def database_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def make_record(
    *,
    profile_id: str = "profile-a",
    display_name: str | None = "来源达人甲",
    account_handle: str | None = "source-handle-a",
    normalized_profile_url: str | None = None,
    source_updated_at: datetime | None = SOURCE_TIME,
    public_profile: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    emails: tuple[str, ...] = (),
) -> CanonicalInfluencerRecord:
    normalized_url = normalized_profile_url or (
        f"https://www.xiaohongshu.com/user/profile/{profile_id}"
    )
    contacts = tuple(
        CanonicalContact(
            type=ContactType.EMAIL,
            value=email,
            normalized_value=email,
            validation_status=ContactValidationStatus.VALID,
        )
        for email in emails
    )
    return CanonicalInfluencerRecord(
        display_name=display_name,
        platform_identity=PlatformIdentity(
            platform=Platform.XIAOHONGSHU,
            platform_account_id=profile_id,
            account_handle=account_handle,
            profile_url=normalized_url,
            normalized_profile_url=normalized_url,
        ),
        source=DataSource.HUITUN,
        source_updated_at=source_updated_at,
        public_profile=public_profile or {},
        metrics=metrics or {},
        contacts=contacts,
    )


def account_source_data(
    *,
    account_name: str,
    account_handle: str | None,
    profile_url: str,
    normalized_profile_url: str,
    public_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "display_name": account_name,
        "account_handle": account_handle,
        "profile_url": profile_url,
        "normalized_profile_url": normalized_profile_url,
        **(public_profile or {}),
    }
    return {key: value for key, value in values.items() if value is not None}


async def seed_existing_account(
    session: AsyncSession,
    *,
    record: CanonicalInfluencerRecord,
    account_name: str | None = None,
    account_handle: str | None = None,
    profile_id: str | None = None,
    normalized_profile_url: str | None = None,
    source_updated_at: datetime | None = None,
    public_profile: dict[str, Any] | None = None,
) -> tuple[Influencer, InfluencerPlatformAccount, InfluencerSourceState]:
    identity = record.platform_identity
    stored_account_name = (
        account_name if account_name is not None else (record.display_name or "已有")
    )
    stored_handle = account_handle if account_handle is not None else identity.account_handle
    stored_profile_id = profile_id if profile_id is not None else identity.platform_account_id
    stored_url = normalized_profile_url or identity.normalized_profile_url
    assert stored_profile_id is not None
    assert stored_url is not None
    stored_source_time = (
        source_updated_at if source_updated_at is not None else record.source_updated_at
    )
    profile = dict(public_profile or {})
    influencer = Influencer(
        display_name="人工维护的达人主体",
        crm_stage=CRMStage.HIGH_INTENT,
        status=InfluencerStatus.ACTIVE,
    )
    session.add(influencer)
    await session.flush()
    account = InfluencerPlatformAccount(
        influencer_id=influencer.id,
        platform=Platform.XIAOHONGSHU,
        platform_account_id=stored_profile_id,
        account_name=stored_account_name,
        account_handle=stored_handle,
        profile_url=stored_url,
        normalized_profile_url=stored_url,
        source=DataSource.HUITUN,
        is_active=True,
        bio=profile.get("bio"),
        gender=profile.get("gender"),
        region_raw=profile.get("region_raw"),
        verification_info=profile.get("verification_info"),
        mcn_name=profile.get("mcn_name"),
        source_tags=profile.get("creator_tags"),
        creator_level=profile.get("creator_level"),
        is_brand_partner=profile.get("is_brand_partner"),
    )
    session.add(account)
    await session.flush()
    source_data = account_source_data(
        account_name=stored_account_name,
        account_handle=stored_handle,
        profile_url=stored_url,
        normalized_profile_url=stored_url,
        public_profile=profile,
    )
    source_state = InfluencerSourceState(
        influencer_id=influencer.id,
        platform_account_id=account.id,
        source=DataSource.HUITUN,
        source_updated_at=stored_source_time,
        source_data=source_data,
        source_data_hash=hash_document(source_data),
        state_version=1,
        last_import_job_id=uuid4(),
        last_import_row_id=uuid4(),
    )
    session.add(source_state)
    await session.flush()
    return influencer, account, source_state


async def plan_record(
    session: AsyncSession,
    record: CanonicalInfluencerRecord,
    *,
    job_id: UUID | None = None,
    row_number: int = 2,
    duplicate_owner_row: int | None = None,
    possible_duplicate_emails: set[str] | None = None,
):
    return await ImportPlanner(ImportRepository(session)).plan(
        job_id=job_id or uuid4(),
        row_number=row_number,
        record=record,
        normalized_data=record.as_dict(),
        mapping_hash=MAPPING_HASH,
        preview_revision=1,
        duplicate_owner_row=duplicate_owner_row,
        possible_duplicate_emails=possible_duplicate_emails,
    )


def warning_codes(plan: Any) -> set[str]:
    return {warning["code"] for warning in plan.warnings}


def test_new_identity_plans_create_with_source_metrics_and_contact() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            record = make_record(
                public_profile={"bio": "公开简介"},
                metrics={"followers_count": 12345},
                emails=("creator@example.com",),
            )

            plan = await plan_record(session, record)

            assert plan.action is ImportRowAction.CREATE
            assert plan.match_type is ImportMatchType.NONE
            assert plan.matched_influencer_id is None
            assert plan.merge_plan["account_create"]["platform_account_id"] == "profile-a"
            assert plan.merge_plan["source_state"]["operation"] == "create"
            assert plan.merge_plan["metrics"]["current"]["operation"] == "create"
            assert plan.merge_plan["metrics"]["snapshot"] is not None
            assert plan.merge_plan["contacts"]["create"][0]["normalized_value"] == (
                "creator@example.com"
            )

    asyncio.run(scenario())


def test_identical_existing_source_state_is_no_change() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            record = make_record(public_profile={"bio": "同一公开简介"})
            _, account, _ = await seed_existing_account(
                session,
                record=record,
                public_profile={"bio": "同一公开简介"},
            )

            plan = await plan_record(session, record)

            assert plan.action is ImportRowAction.NO_CHANGE
            assert plan.match_type is ImportMatchType.PLATFORM_ACCOUNT_ID
            assert plan.matched_platform_account_id == account.id
            assert plan.merge_plan["account_updates"] == {}
            assert plan.merge_plan["source_state"] is None
            assert plan.warnings == []

    asyncio.run(scenario())


def test_freshness_older_same_newer_and_missing_source_time() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            cases = (
                (
                    "older",
                    SOURCE_TIME - timedelta(hours=1),
                    ImportRowAction.NO_CHANGE,
                    "STALE_SOURCE_VALUE_IGNORED",
                    False,
                ),
                (
                    "same",
                    SOURCE_TIME,
                    ImportRowAction.NO_CHANGE,
                    "SAME_TIMESTAMP_CONFLICT",
                    False,
                ),
                (
                    "newer",
                    SOURCE_TIME + timedelta(hours=1),
                    ImportRowAction.UPDATE,
                    None,
                    True,
                ),
                (
                    "missing",
                    None,
                    ImportRowAction.NO_CHANGE,
                    "SOURCE_TIME_MISSING_FILL_ONLY",
                    False,
                ),
            )
            for index, (label, incoming_time, action, warning, overwrites) in enumerate(cases):
                profile_id = f"freshness-{index}"
                existing = make_record(
                    profile_id=profile_id,
                    display_name="已有名称",
                    account_handle=f"handle-{index}",
                    source_updated_at=SOURCE_TIME,
                )
                await seed_existing_account(session, record=existing)
                incoming = make_record(
                    profile_id=profile_id,
                    display_name=f"新名称-{label}",
                    account_handle=f"handle-{index}",
                    source_updated_at=incoming_time,
                )

                plan = await plan_record(session, incoming)

                assert plan.action is action, label
                assert (plan.merge_plan["account_updates"].get("account_name") is not None) is (
                    overwrites
                ), label
                if warning is not None:
                    assert warning in warning_codes(plan), label

    asyncio.run(scenario())


def test_incoming_null_never_overwrites_existing_nonempty_value() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            existing = make_record(display_name="保留名称", public_profile={"bio": "保留简介"})
            await seed_existing_account(
                session,
                record=existing,
                public_profile={"bio": "保留简介"},
            )
            incoming = make_record(display_name=None, public_profile={"bio": None})

            plan = await plan_record(session, incoming)

            assert plan.action is ImportRowAction.NO_CHANGE
            assert "account_name" not in plan.merge_plan["account_updates"]
            assert "bio" not in plan.merge_plan["account_updates"]

    asyncio.run(scenario())


def test_newer_account_handle_change_updates_same_account_with_warning() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            existing = make_record(account_handle="old-handle")
            influencer, account, _ = await seed_existing_account(session, record=existing)
            incoming = make_record(
                account_handle="new-handle",
                source_updated_at=SOURCE_TIME + timedelta(minutes=1),
            )

            plan = await plan_record(session, incoming)

            assert plan.action is ImportRowAction.UPDATE
            assert plan.matched_influencer_id == influencer.id
            assert plan.matched_platform_account_id == account.id
            assert plan.merge_plan["account_updates"]["account_handle"] == "new-handle"
            assert "ACCOUNT_HANDLE_CHANGED" in warning_codes(plan)

    asyncio.run(scenario())


def test_conflicting_hard_identities_require_manual_review() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            platform_record = make_record(
                profile_id="platform-match",
                normalized_profile_url=("https://www.xiaohongshu.com/user/profile/platform-match"),
            )
            _, platform_account, _ = await seed_existing_account(
                session,
                record=platform_record,
            )
            url_record = make_record(
                profile_id="url-match",
                normalized_profile_url="https://www.xiaohongshu.com/user/profile/url-match",
            )
            _, url_account, _ = await seed_existing_account(session, record=url_record)
            incoming = make_record(
                profile_id="platform-match",
                normalized_profile_url="https://www.xiaohongshu.com/user/profile/url-match",
            )

            plan = await plan_record(session, incoming)

            assert platform_account.id != url_account.id
            assert plan.action is ImportRowAction.MANUAL_REVIEW
            assert plan.match_type is ImportMatchType.NONE
            assert plan.matched_influencer_id is None
            assert "IDENTITY_CONFLICT" in warning_codes(plan)

    asyncio.run(scenario())


def test_duplicate_hard_identity_in_same_file_is_skipped_deterministically() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            first = make_record(display_name="第一行")
            duplicate = make_record(display_name="第二行")
            owners, duplicate_emails = build_preview_context([(2, first), (7, duplicate)])

            plan = await plan_record(
                session,
                duplicate,
                row_number=7,
                duplicate_owner_row=owners[7],
                possible_duplicate_emails=duplicate_emails,
            )

            assert owners == {7: 2}
            assert plan.action is ImportRowAction.SKIP
            assert plan.merge_plan["identity_keys"][0].endswith(":account:profile-a")
            assert "DUPLICATE_IDENTITY_IN_FILE" in warning_codes(plan)

    asyncio.run(scenario())


def test_shared_email_never_matches_influencer_and_marks_contact_as_possible_duplicate() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            existing_record = make_record(profile_id="email-owner", emails=())
            existing_influencer, existing_account, _ = await seed_existing_account(
                session,
                record=existing_record,
            )
            existing_contact = InfluencerContact(
                influencer_id=existing_influencer.id,
                platform_account_id=existing_account.id,
                type=ContactType.EMAIL,
                value="shared@mcn.example",
                normalized_value="shared@mcn.example",
                source=DataSource.HUITUN,
                validation_status=ContactValidationStatus.VALID,
                is_current=True,
                possible_duplicate_contact=False,
                first_seen_at=SOURCE_TIME,
                last_seen_at=SOURCE_TIME,
                source_updated_at=SOURCE_TIME,
                first_import_job_id=uuid4(),
                first_import_row_id=uuid4(),
                last_import_job_id=uuid4(),
                last_import_row_id=uuid4(),
            )
            session.add(existing_contact)
            await session.flush()
            incoming = make_record(
                profile_id="different-identity",
                account_handle="different-handle",
                emails=("shared@mcn.example",),
            )

            plan = await plan_record(session, incoming)

            assert plan.action is ImportRowAction.CREATE
            assert plan.matched_influencer_id is None
            assert plan.match_type is ImportMatchType.NONE
            new_contact = plan.merge_plan["contacts"]["create"][0]
            assert new_contact["possible_duplicate_contact"] is True
            assert str(existing_contact.id) in plan.merge_plan["contacts"]["mark_duplicate_ids"]

    asyncio.run(scenario())


def test_email_shared_inside_file_flags_contacts_without_becoming_identity() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            first = make_record(
                profile_id="file-email-a",
                account_handle="file-handle-a",
                emails=("shared-in-file@mcn.example",),
            )
            second = make_record(
                profile_id="file-email-b",
                account_handle="file-handle-b",
                emails=("shared-in-file@mcn.example",),
            )
            duplicate_owners, duplicate_emails = build_preview_context([(2, first), (3, second)])

            first_plan = await plan_record(
                session,
                first,
                row_number=2,
                possible_duplicate_emails=duplicate_emails,
            )
            second_plan = await plan_record(
                session,
                second,
                row_number=3,
                possible_duplicate_emails=duplicate_emails,
            )

            assert duplicate_owners == {}
            assert duplicate_emails == {"shared-in-file@mcn.example"}
            assert first_plan.action is second_plan.action is ImportRowAction.CREATE
            assert first_plan.matched_influencer_id is second_plan.matched_influencer_id is None
            assert (
                first_plan.merge_plan["contacts"]["create"][0]["possible_duplicate_contact"] is True
            )
            assert (
                second_plan.merge_plan["contacts"]["create"][0]["possible_duplicate_contact"]
                is True
            )

    asyncio.run(scenario())


def test_shared_manual_email_flags_incoming_without_mutating_manual_contact() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            existing_record = make_record(profile_id="manual-email-owner", emails=())
            existing_influencer, _, _ = await seed_existing_account(
                session,
                record=existing_record,
            )
            manual_contact = InfluencerContact(
                influencer_id=existing_influencer.id,
                platform_account_id=None,
                type=ContactType.EMAIL,
                value="shared-manual@mcn.example",
                normalized_value="shared-manual@mcn.example",
                source=DataSource.MANUAL,
                validation_status=ContactValidationStatus.VALID,
                is_current=True,
                possible_duplicate_contact=False,
                first_seen_at=SOURCE_TIME,
                last_seen_at=SOURCE_TIME,
                source_updated_at=None,
                first_import_job_id=None,
                first_import_row_id=None,
                last_import_job_id=None,
                last_import_row_id=None,
            )
            session.add(manual_contact)
            await session.flush()
            incoming = make_record(
                profile_id="new-email-owner",
                account_handle="new-email-owner-handle",
                emails=("shared-manual@mcn.example",),
            )

            plan = await plan_record(session, incoming)

            assert plan.action is ImportRowAction.CREATE
            assert plan.merge_plan["contacts"]["create"][0]["possible_duplicate_contact"] is True
            assert str(manual_contact.id) not in plan.merge_plan["contacts"]["mark_duplicate_ids"]

    asyncio.run(scenario())


def test_huitun_merge_does_not_modify_manual_contact_or_manual_influencer_fields() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            existing = make_record(emails=())
            influencer, _, _ = await seed_existing_account(session, record=existing)
            manual_contact = InfluencerContact(
                influencer_id=influencer.id,
                platform_account_id=None,
                type=ContactType.EMAIL,
                value="manual@example.com",
                normalized_value="manual@example.com",
                source=DataSource.MANUAL,
                validation_status=ContactValidationStatus.VALID,
                is_current=True,
                possible_duplicate_contact=False,
                first_seen_at=SOURCE_TIME,
                last_seen_at=SOURCE_TIME,
                source_updated_at=None,
                first_import_job_id=None,
                first_import_row_id=None,
                last_import_job_id=None,
                last_import_row_id=None,
            )
            session.add(manual_contact)
            await session.flush()
            incoming = make_record(emails=("huitun@example.com",))

            plan = await plan_record(session, incoming)

            contact_plan = plan.merge_plan["contacts"]
            assert str(manual_contact.id) not in contact_plan["deactivate_ids"]
            assert str(manual_contact.id) not in contact_plan["mark_duplicate_ids"]
            assert str(manual_contact.id) not in contact_plan["observe_ids"]
            serialized_merge = str(plan.merge_plan)
            for manual_field in (
                "crm_stage",
                "owner_operator_id",
                "department_assignment",
                "manual_tags",
                "manual_notes",
                "deleted_at",
            ):
                assert manual_field not in serialized_merge

    asyncio.run(scenario())


def test_reobserved_source_contact_plans_provenance_touch_and_tracks_concurrency() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            record = make_record(emails=("reobserved@example.com",))
            influencer, account, _ = await seed_existing_account(session, record=record)
            previous_job_id = uuid4()
            previous_row_id = uuid4()
            contact = InfluencerContact(
                influencer_id=influencer.id,
                platform_account_id=account.id,
                type=ContactType.EMAIL,
                value="reobserved@example.com",
                normalized_value="reobserved@example.com",
                source=DataSource.HUITUN,
                validation_status=ContactValidationStatus.VALID,
                is_current=True,
                possible_duplicate_contact=False,
                first_seen_at=SOURCE_TIME - timedelta(days=1),
                last_seen_at=SOURCE_TIME,
                source_updated_at=SOURCE_TIME,
                first_import_job_id=previous_job_id,
                first_import_row_id=previous_row_id,
                last_import_job_id=previous_job_id,
                last_import_row_id=previous_row_id,
            )
            session.add(contact)
            await session.flush()
            import_job_id = uuid4()

            baseline = await plan_record(session, record, job_id=import_job_id)

            contact_plan = baseline.merge_plan["contacts"]
            assert baseline.action is ImportRowAction.NO_CHANGE
            assert contact_plan["create"] == []
            assert contact_plan["observe_ids"] == [str(contact.id)]

            contact.last_seen_at = SOURCE_TIME + timedelta(minutes=1)
            contact.last_import_job_id = uuid4()
            contact.last_import_row_id = uuid4()
            await session.flush()

            concurrent_change = await plan_record(session, record, job_id=import_job_id)
            assert concurrent_change.plan_hash != baseline.plan_hash

    asyncio.run(scenario())


def test_snapshot_key_is_stable_and_existing_snapshot_makes_replan_idempotent() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            metrics = {"followers_count": 45678, "notes_count": 123}
            record = make_record(metrics=metrics)
            influencer, account, _ = await seed_existing_account(session, record=record)
            metrics_hash = hash_document(metrics)
            session.add(
                InfluencerCurrentMetrics(
                    influencer_id=influencer.id,
                    platform_account_id=account.id,
                    source=DataSource.HUITUN,
                    source_updated_at=SOURCE_TIME,
                    metrics=metrics,
                    metrics_hash=metrics_hash,
                    last_import_job_id=uuid4(),
                    last_import_row_id=uuid4(),
                )
            )
            await session.flush()
            job_id = uuid4()

            first = await plan_record(session, record, job_id=job_id)
            repeated_preview = await plan_record(session, record, job_id=job_id)

            snapshot = first.merge_plan["metrics"]["snapshot"]
            assert snapshot is not None
            assert repeated_preview.merge_plan["metrics"]["snapshot"]["snapshot_key"] == (
                snapshot["snapshot_key"]
            )
            assert repeated_preview.plan_hash == first.plan_hash
            session.add(
                InfluencerMetricSnapshot(
                    influencer_id=influencer.id,
                    platform_account_id=account.id,
                    source=DataSource.HUITUN,
                    source_updated_at=SOURCE_TIME,
                    import_job_id=job_id,
                    import_row_id=uuid4(),
                    captured_at=SOURCE_TIME,
                    metrics=metrics,
                    metrics_hash=metrics_hash,
                    snapshot_key=snapshot["snapshot_key"],
                )
            )
            await session.flush()

            after_snapshot = await plan_record(session, record, job_id=job_id)

            assert after_snapshot.merge_plan["metrics"]["snapshot"] is None
            assert after_snapshot.action is ImportRowAction.NO_CHANGE

    asyncio.run(scenario())


def test_plan_hash_tracks_relevant_state_but_ignores_influencer_updated_at() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            record = make_record()
            influencer, account, source_state = await seed_existing_account(
                session,
                record=record,
            )
            job_id = uuid4()

            baseline = await plan_record(session, record, job_id=job_id)
            stable_repeat = await plan_record(session, record, job_id=job_id)
            assert stable_repeat.plan_hash == baseline.plan_hash

            influencer.display_name = "另一个人工主体名"
            influencer.crm_stage = CRMStage.CLOSED
            influencer.updated_at = SOURCE_TIME + timedelta(days=1)
            await session.flush()
            manual_change = await plan_record(session, record, job_id=job_id)
            assert manual_change.plan_hash == baseline.plan_hash

            source_state.state_version += 1
            await session.flush()
            source_state_change = await plan_record(session, record, job_id=job_id)
            assert source_state_change.plan_hash != baseline.plan_hash

            source_state.state_version -= 1
            account.account_name = "相关公开资料已被并发修改"
            await session.flush()
            account_change = await plan_record(session, record, job_id=job_id)
            assert account_change.plan_hash != baseline.plan_hash

    asyncio.run(scenario())

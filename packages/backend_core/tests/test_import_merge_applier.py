"""The shared merge applier preserves legacy semantics and bulk write shape."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from backend_core.imports.bulk_repository import AccountSourceKey
from backend_core.imports.contracts import CanonicalInfluencerRecord, PlatformIdentity
from backend_core.imports.enums import ImportMatchType, ImportRowAction
from backend_core.imports.merge_applier import (
    ImportMergeApplier,
    MergeApplyCache,
    PreviewStaleError,
)
from backend_core.imports.models import ImportJob, ImportRow
from backend_core.imports.planner import PlannedImportRow
from backend_core.influencers.enums import DataSource, Platform
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
    InfluencerSourceState,
    PlatformAccountSourceIdentity,
)
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 8, 13, 8, 0, tzinfo=UTC)


def _record() -> CanonicalInfluencerRecord:
    return CanonicalInfluencerRecord(
        display_name="脱敏达人",
        platform_identity=PlatformIdentity(
            platform=Platform.XIAOHONGSHU,
            platform_account_id="account-new",
            account_handle="handle-new",
            profile_url="https://example.invalid/account-new",
            normalized_profile_url="https://example.invalid/account-new",
            external_source_id="external-new",
        ),
        source=DataSource.GENERIC,
        source_updated_at=NOW,
    )


def _plan(
    action: ImportRowAction,
    merge_plan: dict[str, object],
    *,
    account_id: UUID | None = None,
) -> PlannedImportRow:
    return PlannedImportRow(
        matched_influencer_id=None,
        matched_platform_account_id=account_id,
        match_type=(
            ImportMatchType.NONE if account_id is None else ImportMatchType.PLATFORM_ACCOUNT_ID
        ),
        action=action,
        merge_plan=merge_plan,
        warnings=[],
        errors=[],
        preconditions={},
        plan_hash="a" * 64,
    )


def _session() -> MagicMock:
    session = MagicMock(spec=AsyncSession)
    session.get = AsyncMock()
    session.flush = AsyncMock()
    return session


def test_strict_bulk_create_assigns_all_ids_with_one_batch_finalize_flush() -> None:
    async def scenario() -> None:
        session = _session()
        cache = MergeApplyCache(
            accounts_by_id={},
            source_states={},
            current_metrics={},
            contacts_by_id={},
            covered_account_sources=set(),
        )
        job = ImportJob(id=UUID(int=1), operator_id=UUID(int=2))
        row = ImportRow(id=UUID(int=3), row_number=2)
        record = _record()
        plan = _plan(
            ImportRowAction.CREATE,
            {
                "account_create": {
                    "display_name": "脱敏达人",
                    "platform_account_id": "account-new",
                    "account_name": "脱敏达人",
                    "account_handle": "handle-new",
                    "profile_url": "https://example.invalid/account-new",
                    "normalized_profile_url": "https://example.invalid/account-new",
                    "public_profile": {"bio": "公开简介"},
                },
                "account_updates": {},
                "source_identity": {"external_account_id": "external-new"},
                "source_state": {
                    "source_updated_at": NOW.isoformat(),
                    "source_data": {"bio": "公开简介"},
                    "source_data_hash": "b" * 64,
                    "state_version": 1,
                },
                "contacts": {
                    "deactivate_ids": [],
                    "mark_duplicate_ids": [],
                    "observe_ids": [],
                    "create": [
                        {
                            "type": "email",
                            "value": "safe@example.invalid",
                            "normalized_value": "safe@example.invalid",
                            "validation_status": "valid",
                            "is_current": True,
                            "possible_duplicate_contact": False,
                            "source_updated_at": NOW.isoformat(),
                        }
                    ],
                },
                "metrics": {
                    "current": {
                        "source_updated_at": NOW.isoformat(),
                        "metrics": {"followers_count": 123, "notes_7d": 0},
                        "metrics_hash": "c" * 64,
                    },
                    "snapshot": {
                        "source_updated_at": NOW.isoformat(),
                        "metrics": {"followers_count": 123, "notes_7d": 0},
                        "metrics_hash": "c" * 64,
                        "snapshot_key": "d" * 64,
                    },
                },
            },
        )

        applier = ImportMergeApplier(session, cache=cache)
        await applier.apply(job, row, record, plan, NOW)
        assert row.matched_platform_account_id is None
        await applier.finalize()

        session.get.assert_not_awaited()
        session.flush.assert_awaited_once_with()
        influencer, account = session.add_all.call_args.args[0]
        added = [call.args[0] for call in session.add.call_args_list]
        assert isinstance(influencer, Influencer) and influencer.id is not None
        assert isinstance(account, InfluencerPlatformAccount) and account.id is not None
        assert account.influencer_id == influencer.id
        assert {type(item) for item in added} == {
            PlatformAccountSourceIdentity,
            InfluencerSourceState,
            InfluencerContact,
            InfluencerCurrentMetrics,
            InfluencerMetricSnapshot,
        }
        assert all(item.id is not None for item in added)
        current_metrics = next(item for item in added if isinstance(item, InfluencerCurrentMetrics))
        metric_snapshot = next(item for item in added if isinstance(item, InfluencerMetricSnapshot))
        assert (
            current_metrics.metrics
            == metric_snapshot.metrics
            == {
                "followers_count": 123,
                "notes_7d": 0,
            }
        )
        assert row.matched_influencer_id == influencer.id
        assert row.matched_platform_account_id == account.id
        assert row.committed_action is ImportRowAction.CREATE
        assert row.committed_at == NOW
        assert cache.accounts_by_id[account.id] is account
        assert cache.source_states[AccountSourceKey(account.id, record.source)].id is not None
        assert cache.current_metrics[AccountSourceKey(account.id, record.source)].id is not None

    asyncio.run(scenario())


def test_strict_bulk_cache_never_falls_back_for_missing_plan_references() -> None:
    async def scenario() -> None:
        session = _session()
        account_id = UUID(int=21)
        influencer_id = UUID(int=22)
        account = InfluencerPlatformAccount(
            id=account_id,
            influencer_id=influencer_id,
            platform=Platform.XIAOHONGSHU,
            source=DataSource.GENERIC,
        )
        cache = MergeApplyCache(
            accounts_by_id={account_id: account},
            source_states={},
            current_metrics={},
            contacts_by_id={},
            covered_account_sources={AccountSourceKey(account_id, DataSource.GENERIC)},
        )
        plan = _plan(
            ImportRowAction.NO_CHANGE,
            {
                "account_create": None,
                "account_updates": {},
                "source_identity": None,
                "source_state": None,
                "contacts": {
                    "deactivate_ids": [],
                    "mark_duplicate_ids": [],
                    "observe_ids": [str(UUID(int=23))],
                    "create": [],
                },
                "metrics": {"current": None, "snapshot": None},
            },
            account_id=account_id,
        )

        with pytest.raises(PreviewStaleError, match="contact_deleted"):
            await ImportMergeApplier(session, cache=cache).apply(
                ImportJob(id=UUID(int=1), operator_id=UUID(int=2)),
                ImportRow(id=UUID(int=3), row_number=2),
                _record(),
                plan,
                NOW,
            )
        session.get.assert_not_awaited()
        session.flush.assert_not_awaited()

    asyncio.run(scenario())

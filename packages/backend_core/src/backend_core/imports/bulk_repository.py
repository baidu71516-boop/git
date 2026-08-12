"""Typed, chunked read models for bulk import planning.

The module owns database access shape only.  It intentionally does not decide
identity priority, potential matches, freshness, merge actions, or graph
ownership; those remain Planner/domain responsibilities.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.imports.contracts import CanonicalInfluencerRecord
from backend_core.imports.hashing import advisory_lock_key
from backend_core.influencers.enums import (
    ContactType,
    ContactValidationStatus,
    DataSource,
    Platform,
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

DEFAULT_BULK_CHUNK_SIZE = 500


def iter_safe_chunks[T: Hashable](
    values: Iterable[T],
    *,
    chunk_size: int = DEFAULT_BULK_CHUNK_SIZE,
    key: Callable[[T], Any] | None = None,
) -> Iterator[tuple[T, ...]]:
    """Yield stable, unique, bounded chunks.

    Incoming query keys are hashable typed values.  Sorting before chunking
    ensures identical inputs acquire locks and issue statements in the same
    order regardless of their source row order.
    """

    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    ordered = sorted(set(values), key=key or repr)
    for start in range(0, len(ordered), chunk_size):
        yield tuple(ordered[start : start + chunk_size])


@dataclass(frozen=True, order=True, slots=True)
class PlatformAccountIdKey:
    platform: Platform
    value: str


@dataclass(frozen=True, order=True, slots=True)
class ProfileUrlKey:
    platform: Platform
    value: str


@dataclass(frozen=True, order=True, slots=True)
class AccountHandleKey:
    platform: Platform
    value: str


@dataclass(frozen=True, order=True, slots=True)
class ExternalIdentityKey:
    source: DataSource
    platform: Platform
    value: str


@dataclass(frozen=True, order=True, slots=True)
class AccountSourceKey:
    platform_account_id: UUID
    source: DataSource


@dataclass(frozen=True, order=True, slots=True)
class SourceIdentityKey:
    platform_account_id: UUID
    source: DataSource
    external_account_id: str


@dataclass(frozen=True, order=True, slots=True)
class ContactValueKey:
    type: ContactType
    normalized_value: str


@dataclass(frozen=True, order=True, slots=True)
class SourceContactKey:
    influencer_id: UUID
    source: DataSource
    type: ContactType


HardIdentityKey = PlatformAccountIdKey | ProfileUrlKey | ExternalIdentityKey


@dataclass(frozen=True, slots=True)
class BulkImportContext:
    """Unique database query keys extracted from canonical incoming rows."""

    platform_account_ids: frozenset[PlatformAccountIdKey]
    normalized_profile_urls: frozenset[ProfileUrlKey]
    external_source_ids: frozenset[ExternalIdentityKey]
    account_handles: frozenset[AccountHandleKey]
    contact_values: frozenset[ContactValueKey]
    sources: frozenset[DataSource]
    snapshot_keys: frozenset[str]

    @classmethod
    def from_records(
        cls,
        records: Iterable[CanonicalInfluencerRecord],
        *,
        snapshot_keys: Iterable[str] = (),
    ) -> BulkImportContext:
        platform_account_ids: set[PlatformAccountIdKey] = set()
        profile_urls: set[ProfileUrlKey] = set()
        external_ids: set[ExternalIdentityKey] = set()
        handles: set[AccountHandleKey] = set()
        contacts: set[ContactValueKey] = set()
        sources: set[DataSource] = set()
        for record in records:
            identity = record.platform_identity
            sources.add(record.source)
            if identity.platform_account_id:
                platform_account_ids.add(
                    PlatformAccountIdKey(identity.platform, identity.platform_account_id)
                )
            if identity.normalized_profile_url:
                profile_urls.add(ProfileUrlKey(identity.platform, identity.normalized_profile_url))
            if identity.external_source_id:
                external_ids.add(
                    ExternalIdentityKey(
                        record.source,
                        identity.platform,
                        identity.external_source_id,
                    )
                )
            if identity.account_handle:
                handles.add(AccountHandleKey(identity.platform, identity.account_handle))
            contacts.update(
                ContactValueKey(contact.type, contact.normalized_value)
                for contact in record.contacts
                if contact.validation_status is ContactValidationStatus.VALID
            )
        return cls(
            platform_account_ids=frozenset(platform_account_ids),
            normalized_profile_urls=frozenset(profile_urls),
            external_source_ids=frozenset(external_ids),
            account_handles=frozenset(handles),
            contact_values=frozenset(contacts),
            sources=frozenset(sources),
            snapshot_keys=frozenset(snapshot_keys),
        )

    @property
    def hard_identity_keys(self) -> frozenset[HardIdentityKey]:
        """Frozen hard identities only; handle and Contact remain excluded."""

        return frozenset(
            (*self.platform_account_ids, *self.normalized_profile_urls, *self.external_source_ids)
        )


@dataclass(frozen=True, slots=True)
class PrefetchedImportState:
    """Deterministic database state consumed by later bulk planning work."""

    accounts_by_id: Mapping[UUID, InfluencerPlatformAccount]
    influencers_by_id: Mapping[UUID, Influencer]
    accounts_by_platform_id: Mapping[PlatformAccountIdKey, tuple[InfluencerPlatformAccount, ...]]
    accounts_by_profile_url: Mapping[ProfileUrlKey, tuple[InfluencerPlatformAccount, ...]]
    accounts_by_external_id: Mapping[ExternalIdentityKey, tuple[InfluencerPlatformAccount, ...]]
    accounts_by_handle: Mapping[AccountHandleKey, tuple[InfluencerPlatformAccount, ...]]
    source_identities: Mapping[SourceIdentityKey, PlatformAccountSourceIdentity]
    covered_external_source_ids: frozenset[ExternalIdentityKey]
    source_states: Mapping[AccountSourceKey, InfluencerSourceState]
    current_metrics: Mapping[AccountSourceKey, InfluencerCurrentMetrics]
    last_confirmed_observations: Mapping[AccountSourceKey, datetime]
    covered_account_sources: frozenset[AccountSourceKey]
    existing_snapshot_keys: frozenset[str]
    requested_snapshot_keys: frozenset[str]
    source_contacts: Mapping[SourceContactKey, tuple[InfluencerContact, ...]]
    contacts_by_normalized_value: Mapping[ContactValueKey, tuple[InfluencerContact, ...]]


class PrefetchCoverageError(RuntimeError):
    """A Planner asked for a key absent from the frozen prefetch context."""


class PrefetchedImportRepository:
    """In-memory implementation of the Planner repository read contract.

    Missing database rows are represented by covered empty mappings.  A key
    outside the prefetch coverage raises instead of silently changing a plan.
    """

    def __init__(self, state: PrefetchedImportState) -> None:
        self.state = state

    async def accounts_by_platform_id(
        self, platform: Platform, platform_account_id: str
    ) -> list[InfluencerPlatformAccount]:
        key = PlatformAccountIdKey(platform, platform_account_id)
        return list(self._covered_group(self.state.accounts_by_platform_id, key))

    async def accounts_by_profile_url(
        self, platform: Platform, normalized_profile_url: str
    ) -> list[InfluencerPlatformAccount]:
        key = ProfileUrlKey(platform, normalized_profile_url)
        return list(self._covered_group(self.state.accounts_by_profile_url, key))

    async def accounts_by_external_id(
        self,
        platform: Platform,
        source: DataSource,
        external_account_id: str,
    ) -> list[InfluencerPlatformAccount]:
        key = ExternalIdentityKey(source, platform, external_account_id)
        return list(self._covered_group(self.state.accounts_by_external_id, key))

    async def accounts_by_handle(
        self, platform: Platform, account_handle: str
    ) -> list[InfluencerPlatformAccount]:
        key = AccountHandleKey(platform, account_handle)
        return list(self._covered_group(self.state.accounts_by_handle, key))

    async def get_source_state(
        self, platform_account_id: UUID, source: DataSource
    ) -> InfluencerSourceState | None:
        key = AccountSourceKey(platform_account_id, source)
        self._require_coverage(key, self.state.covered_account_sources, "source state")
        return self.state.source_states.get(key)

    async def get_source_identity(
        self,
        platform_account_id: UUID,
        source: DataSource,
        external_account_id: str,
    ) -> PlatformAccountSourceIdentity | None:
        key = SourceIdentityKey(platform_account_id, source, external_account_id)
        account = self.state.accounts_by_id.get(platform_account_id)
        if account is None:
            raise PrefetchCoverageError(
                "prefetch does not cover the requested source identity account"
            )
        external_key = ExternalIdentityKey(source, account.platform, external_account_id)
        self._require_coverage(
            external_key,
            self.state.covered_external_source_ids,
            "source identity external key",
        )
        return self.state.source_identities.get(key)

    async def get_current_metrics(
        self, platform_account_id: UUID, source: DataSource
    ) -> InfluencerCurrentMetrics | None:
        key = AccountSourceKey(platform_account_id, source)
        self._require_coverage(key, self.state.covered_account_sources, "current metrics")
        return self.state.current_metrics.get(key)

    async def snapshot_exists(self, snapshot_key: str) -> bool:
        self._require_coverage(
            snapshot_key,
            self.state.requested_snapshot_keys,
            "metric snapshot",
        )
        return snapshot_key in self.state.existing_snapshot_keys

    async def source_contacts(
        self,
        influencer_id: UUID,
        source: DataSource,
        contact_type: ContactType,
    ) -> list[InfluencerContact]:
        key = SourceContactKey(influencer_id, source, contact_type)
        return list(self._covered_group(self.state.source_contacts, key))

    async def contacts_with_normalized_value(
        self, contact_type: ContactType, normalized_value: str
    ) -> list[InfluencerContact]:
        key = ContactValueKey(contact_type, normalized_value)
        return list(self._covered_group(self.state.contacts_by_normalized_value, key))

    @staticmethod
    def _covered_group[K: Hashable, V](mapping: Mapping[K, tuple[V, ...]], key: K) -> tuple[V, ...]:
        if key not in mapping:
            raise PrefetchCoverageError(
                f"prefetch does not cover requested lookup type: {type(key).__name__}"
            )
        return mapping[key]

    @staticmethod
    def _require_coverage[K: Hashable](
        key: K,
        coverage: frozenset[K],
        label: str,
    ) -> None:
        if key not in coverage:
            raise PrefetchCoverageError(f"prefetch does not cover requested {label}")


def advisory_lock_keys(identities: Iterable[str]) -> tuple[int, ...]:
    """Return the globally stable lock order shared by every bulk worker."""

    return tuple(sorted({advisory_lock_key(identity) for identity in identities}))


class BulkImportRepository:
    """Chunked bulk reads with query counts independent of incoming row count."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        chunk_size: int = DEFAULT_BULK_CHUNK_SIZE,
    ) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        self.session = session
        self.chunk_size = chunk_size

    async def prefetch(self, context: BulkImportContext) -> PrefetchedImportState:
        platform_accounts = await self._accounts_by_platform_value(
            context.platform_account_ids,
            InfluencerPlatformAccount.platform_account_id,
            PlatformAccountIdKey,
        )
        profile_accounts = await self._accounts_by_platform_value(
            context.normalized_profile_urls,
            InfluencerPlatformAccount.normalized_profile_url,
            ProfileUrlKey,
        )
        handle_accounts = await self._accounts_by_platform_value(
            context.account_handles,
            InfluencerPlatformAccount.account_handle,
            AccountHandleKey,
        )
        external_accounts, source_identities = await self._accounts_by_external_identity(
            context.external_source_ids
        )

        candidate_accounts = {
            account.id: account
            for groups in (
                platform_accounts.values(),
                profile_accounts.values(),
                external_accounts.values(),
            )
            for accounts in groups
            for account in accounts
        }
        ordered_account_ids = tuple(sorted(candidate_accounts, key=str))
        influencer_ids = tuple(
            sorted({account.influencer_id for account in candidate_accounts.values()}, key=str)
        )
        influencers = await self._influencers_by_id(influencer_ids)

        account_source_keys = frozenset(
            AccountSourceKey(account_id, source)
            for account_id in ordered_account_ids
            for source in context.sources
        )
        source_states = await self._source_states(account_source_keys)
        current_metrics = await self._current_metrics(account_source_keys)
        last_confirmed_observations = await self._last_confirmed_observations(account_source_keys)
        existing_snapshots = await self._existing_snapshot_keys(context.snapshot_keys)
        source_contact_keys = frozenset(
            SourceContactKey(influencer_id, source, ContactType.EMAIL)
            for influencer_id in influencer_ids
            for source in context.sources
        )
        source_contacts = await self._source_contacts(source_contact_keys)
        contacts_by_value = await self._contacts_by_value(context.contact_values)

        return PrefetchedImportState(
            accounts_by_id=MappingProxyType(
                dict(sorted(candidate_accounts.items(), key=lambda item: str(item[0])))
            ),
            influencers_by_id=MappingProxyType(influencers),
            accounts_by_platform_id=MappingProxyType(platform_accounts),
            accounts_by_profile_url=MappingProxyType(profile_accounts),
            accounts_by_external_id=MappingProxyType(external_accounts),
            accounts_by_handle=MappingProxyType(handle_accounts),
            source_identities=MappingProxyType(source_identities),
            covered_external_source_ids=context.external_source_ids,
            source_states=MappingProxyType(source_states),
            current_metrics=MappingProxyType(current_metrics),
            last_confirmed_observations=MappingProxyType(last_confirmed_observations),
            covered_account_sources=account_source_keys,
            existing_snapshot_keys=frozenset(existing_snapshots),
            requested_snapshot_keys=context.snapshot_keys,
            source_contacts=MappingProxyType(source_contacts),
            contacts_by_normalized_value=MappingProxyType(contacts_by_value),
        )

    async def _last_confirmed_observations(
        self,
        keys: Iterable[AccountSourceKey],
    ) -> dict[AccountSourceKey, datetime]:
        """Load reliable observations from successful Confirm lineage.

        Freshness is keyed by PlatformAccount + canonical source.  Preview
        revisions and uncommitted staging rows are deliberately excluded: only
        completed jobs, committed business actions, included occurrences, and
        confirmed acquisition timestamps can advance the baseline.
        """

        from backend_core.imports.enums import (
            ImportJobFileStatus,
            ImportJobStatus,
            ImportRowAction,
            ImportSourceType,
        )
        from backend_core.imports.models import ImportJob, ImportJobFile, ImportRow

        source_types = {
            DataSource.HUITUN: ImportSourceType.MANUAL_HUITUN_EXPORT,
            DataSource.GENERIC: ImportSourceType.GENERIC_CSV,
        }
        account_ids_by_source: dict[DataSource, set[UUID]] = {}
        for key in keys:
            account_ids_by_source.setdefault(key.source, set()).add(key.platform_account_id)

        observations: dict[AccountSourceKey, datetime] = {}
        committed_actions = (
            ImportRowAction.CREATE,
            ImportRowAction.UPDATE,
            ImportRowAction.NO_CHANGE,
        )
        for source in sorted(account_ids_by_source, key=lambda item: item.value):
            source_type = source_types.get(source)
            if source_type is None:
                continue
            for account_ids in iter_safe_chunks(
                account_ids_by_source[source],
                chunk_size=self.chunk_size,
                key=str,
            ):
                statement = (
                    select(
                        ImportRow.matched_platform_account_id,
                        func.max(ImportJobFile.source_acquired_at),
                    )
                    .join(ImportJob, ImportJob.id == ImportRow.import_job_id)
                    .join(
                        ImportJobFile,
                        and_(
                            ImportJobFile.id == ImportRow.import_job_file_id,
                            ImportJobFile.import_job_id == ImportRow.import_job_id,
                        ),
                    )
                    .where(
                        ImportRow.matched_platform_account_id.in_(account_ids),
                        ImportRow.committed_at.is_not(None),
                        ImportRow.committed_action.in_(committed_actions),
                        ImportJob.status == ImportJobStatus.COMPLETED,
                        ImportJob.stored_file_id.is_(None),
                        ImportJob.confirmed_revision.is_not(None),
                        ImportRow.preview_revision == ImportJob.confirmed_revision,
                        ImportJob.source_type == source_type,
                        ImportJobFile.status == ImportJobFileStatus.READY,
                        ImportJobFile.source_acquired_at.is_not(None),
                        ImportJobFile.source_acquired_at_confirmation_required.is_(False),
                    )
                    .group_by(ImportRow.matched_platform_account_id)
                )
                for account_id, observed_at in (await self.session.execute(statement)).all():
                    if account_id is not None and observed_at is not None:
                        observations[AccountSourceKey(account_id, source)] = observed_at
        return dict(sorted(observations.items(), key=lambda item: repr(item[0])))

    async def acquire_identity_locks_bulk(self, identities: Iterable[str]) -> tuple[int, ...]:
        """Acquire sorted transaction-level advisory locks in chunk-sized SQL calls."""

        lock_keys = advisory_lock_keys(identities)
        if self.session.bind is None or self.session.bind.dialect.name != "postgresql":
            return lock_keys
        statement = text(
            "SELECT pg_advisory_xact_lock(lock_key) "
            "FROM unnest(CAST(:lock_keys AS BIGINT[])) AS lock_key "
            "ORDER BY lock_key"
        )
        # ``lock_keys`` is already the canonical numeric order.  Keep that
        # order while chunking so legacy and bulk callers can never acquire the
        # same lock set in opposite orders.
        for start in range(0, len(lock_keys), self.chunk_size):
            chunk = lock_keys[start : start + self.chunk_size]
            await self.session.execute(statement, {"lock_keys": list(chunk)})
        return lock_keys

    async def _accounts_by_platform_value[K: (
        PlatformAccountIdKey,
        ProfileUrlKey,
        AccountHandleKey,
    )](
        self,
        keys: Iterable[K],
        value_column: Any,
        key_factory: Callable[[Platform, str], K],
    ) -> dict[K, tuple[InfluencerPlatformAccount, ...]]:
        frozen_keys = frozenset(keys)
        grouped: dict[K, list[InfluencerPlatformAccount]] = {key: [] for key in frozen_keys}
        for chunk in iter_safe_chunks(keys, chunk_size=self.chunk_size):
            rows = await self.session.scalars(
                select(InfluencerPlatformAccount).where(
                    tuple_(InfluencerPlatformAccount.platform, value_column).in_(
                        [(key.platform, key.value) for key in chunk]
                    )
                )
            )
            for account in rows:
                value = getattr(account, value_column.key)
                if value is not None:
                    grouped[key_factory(account.platform, value)].append(account)
        return {
            key: tuple(sorted(accounts, key=lambda account: str(account.id)))
            for key, accounts in sorted(grouped.items(), key=lambda item: repr(item[0]))
        }

    async def _accounts_by_external_identity(
        self,
        keys: Iterable[ExternalIdentityKey],
    ) -> tuple[
        dict[ExternalIdentityKey, tuple[InfluencerPlatformAccount, ...]],
        dict[SourceIdentityKey, PlatformAccountSourceIdentity],
    ]:
        frozen_keys = frozenset(keys)
        grouped: dict[ExternalIdentityKey, list[InfluencerPlatformAccount]] = {
            key: [] for key in frozen_keys
        }
        identities: dict[SourceIdentityKey, PlatformAccountSourceIdentity] = {}
        for chunk in iter_safe_chunks(keys, chunk_size=self.chunk_size):
            rows = await self.session.execute(
                select(PlatformAccountSourceIdentity, InfluencerPlatformAccount)
                .join(
                    InfluencerPlatformAccount,
                    InfluencerPlatformAccount.id
                    == PlatformAccountSourceIdentity.platform_account_id,
                )
                .where(
                    tuple_(
                        PlatformAccountSourceIdentity.source,
                        PlatformAccountSourceIdentity.platform,
                        PlatformAccountSourceIdentity.external_account_id,
                    ).in_([(key.source, key.platform, key.value) for key in chunk])
                )
            )
            for identity, account in rows:
                query_key = ExternalIdentityKey(
                    identity.source,
                    identity.platform,
                    identity.external_account_id,
                )
                grouped[query_key].append(account)
                identities[
                    SourceIdentityKey(
                        identity.platform_account_id,
                        identity.source,
                        identity.external_account_id,
                    )
                ] = identity
        return (
            {
                key: tuple(sorted(accounts, key=lambda account: str(account.id)))
                for key, accounts in sorted(grouped.items())
            },
            dict(sorted(identities.items())),
        )

    async def _influencers_by_id(self, influencer_ids: Iterable[UUID]) -> dict[UUID, Influencer]:
        result: dict[UUID, Influencer] = {}
        for chunk in iter_safe_chunks(influencer_ids, chunk_size=self.chunk_size, key=str):
            for influencer in await self.session.scalars(
                select(Influencer).where(Influencer.id.in_(chunk))
            ):
                result[influencer.id] = influencer
        return dict(sorted(result.items(), key=lambda item: str(item[0])))

    async def _source_states(
        self, keys: Iterable[AccountSourceKey]
    ) -> dict[AccountSourceKey, InfluencerSourceState]:
        result: dict[AccountSourceKey, InfluencerSourceState] = {}
        for chunk in iter_safe_chunks(keys, chunk_size=self.chunk_size):
            rows = await self.session.scalars(
                select(InfluencerSourceState).where(
                    tuple_(
                        InfluencerSourceState.platform_account_id,
                        InfluencerSourceState.source,
                    ).in_([(key.platform_account_id, key.source) for key in chunk])
                )
            )
            for state in rows:
                result[AccountSourceKey(state.platform_account_id, state.source)] = state
        return dict(sorted(result.items()))

    async def _current_metrics(
        self, keys: Iterable[AccountSourceKey]
    ) -> dict[AccountSourceKey, InfluencerCurrentMetrics]:
        result: dict[AccountSourceKey, InfluencerCurrentMetrics] = {}
        for chunk in iter_safe_chunks(keys, chunk_size=self.chunk_size):
            rows = await self.session.scalars(
                select(InfluencerCurrentMetrics).where(
                    tuple_(
                        InfluencerCurrentMetrics.platform_account_id,
                        InfluencerCurrentMetrics.source,
                    ).in_([(key.platform_account_id, key.source) for key in chunk])
                )
            )
            for metrics in rows:
                result[AccountSourceKey(metrics.platform_account_id, metrics.source)] = metrics
        return dict(sorted(result.items()))

    async def _existing_snapshot_keys(self, keys: Iterable[str]) -> set[str]:
        result: set[str] = set()
        for chunk in iter_safe_chunks(keys, chunk_size=self.chunk_size):
            result.update(
                await self.session.scalars(
                    select(InfluencerMetricSnapshot.snapshot_key).where(
                        InfluencerMetricSnapshot.snapshot_key.in_(chunk)
                    )
                )
            )
        return result

    async def _source_contacts(
        self,
        keys: Iterable[SourceContactKey],
    ) -> dict[SourceContactKey, tuple[InfluencerContact, ...]]:
        frozen_keys = frozenset(keys)
        grouped: dict[SourceContactKey, list[InfluencerContact]] = {key: [] for key in frozen_keys}
        for chunk in iter_safe_chunks(keys, chunk_size=self.chunk_size):
            rows = await self.session.scalars(
                select(InfluencerContact).where(
                    tuple_(
                        InfluencerContact.influencer_id,
                        InfluencerContact.source,
                        InfluencerContact.type,
                    ).in_([(key.influencer_id, key.source, key.type) for key in chunk])
                )
            )
            for contact in rows:
                grouped[
                    SourceContactKey(contact.influencer_id, contact.source, contact.type)
                ].append(contact)
        return {
            key: tuple(sorted(contacts, key=lambda contact: str(contact.id)))
            for key, contacts in sorted(grouped.items())
        }

    async def _contacts_by_value(
        self,
        keys: Iterable[ContactValueKey],
    ) -> dict[ContactValueKey, tuple[InfluencerContact, ...]]:
        frozen_keys = frozenset(keys)
        grouped: dict[ContactValueKey, list[InfluencerContact]] = {key: [] for key in frozen_keys}
        for chunk in iter_safe_chunks(keys, chunk_size=self.chunk_size):
            rows = await self.session.scalars(
                select(InfluencerContact).where(
                    tuple_(
                        InfluencerContact.type,
                        InfluencerContact.normalized_value,
                    ).in_([(key.type, key.normalized_value) for key in chunk])
                )
            )
            for contact in rows:
                grouped[ContactValueKey(contact.type, contact.normalized_value)].append(contact)
        return {
            key: tuple(sorted(contacts, key=lambda contact: str(contact.id)))
            for key, contacts in sorted(grouped.items())
        }


__all__ = [
    "AccountHandleKey",
    "AccountSourceKey",
    "BulkImportContext",
    "BulkImportRepository",
    "ContactValueKey",
    "DEFAULT_BULK_CHUNK_SIZE",
    "ExternalIdentityKey",
    "HardIdentityKey",
    "PlatformAccountIdKey",
    "ProfileUrlKey",
    "PrefetchCoverageError",
    "PrefetchedImportState",
    "PrefetchedImportRepository",
    "SourceContactKey",
    "SourceIdentityKey",
    "advisory_lock_keys",
    "iter_safe_chunks",
]

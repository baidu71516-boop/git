"""Focused persistence queries for the Phase 3A Campaign and Outreach domain.

The repository deliberately stages no commits or rollbacks.  The services own the
transaction so their business mutation, Audit, Event, and idempotency facts stay
atomic.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, case, func, insert, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.schema import Table

from backend_core.auth.models import Operator
from backend_core.growth.enums import CandidateResult
from backend_core.growth.models import (
    Campaign,
    CampaignMember,
    CandidatePool,
    CandidatePoolMember,
    CandidatePoolRun,
)
from backend_core.influencers.enums import Platform
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerPlatformAccount,
)
from backend_core.outreach.enums import OutreachChannel, OutreachEventType
from backend_core.outreach.models import (
    MessageTemplate,
    MessageTemplateVersion,
    OutreachEvent,
    OutreachTarget,
    OutreachTask,
)


@dataclass(frozen=True, slots=True)
class TemplateVersionRecord:
    template: MessageTemplate
    version: MessageTemplateVersion


@dataclass(frozen=True, slots=True)
class CampaignMemberInsertRow:
    """One new Member row prepared after Campaign-domain validation."""

    id: UUID
    department_id: UUID
    campaign_id: UUID
    influencer_id: UUID
    preferred_platform_account_id: UUID
    source_pool_run_id: UUID | None
    added_by_operator_id: UUID


@dataclass(frozen=True, slots=True)
class CampaignMemberRestoreRow:
    """One locked, soft-removed Member row and its expected CAS version."""

    member_id: UUID
    expected_version: int
    preferred_platform_account_id: UUID
    source_pool_run_id: UUID | None
    added_by_operator_id: UUID


@dataclass(frozen=True, slots=True)
class CampaignRowsPage:
    """ORM rows and the final returned Campaign keyset tuple."""

    items: tuple[CampaignOwnerRecord, ...]
    next_cursor: tuple[datetime, UUID] | None


@dataclass(frozen=True, slots=True)
class CampaignOwnerRecord:
    """One Campaign with its required, Department-local owner projection source."""

    campaign: Campaign
    owner: Operator


@dataclass(frozen=True, slots=True)
class CampaignMemberRowsPage:
    """Active Member display-projection rows and the final keyset tuple."""

    items: tuple[CampaignMemberProjectionRecord, ...]
    next_cursor: tuple[datetime, UUID] | None


@dataclass(frozen=True, slots=True)
class CampaignMemberProjectionRecord:
    """One Member with its canonical current, non-Contact display identity."""

    member: CampaignMember
    influencer: Influencer
    preferred_platform_account: InfluencerPlatformAccount


@dataclass(frozen=True, slots=True)
class CandidatePoolMatchMemberRecord:
    """One lightweight persisted MATCH row used by ALL_MATCH chunk processing."""

    id: UUID
    influencer_id: UUID
    platform_account_id: UUID


@dataclass(frozen=True, slots=True)
class CandidatePoolRunAmbiguityAccountRecord:
    """Safe, canonical account identity needed to resolve one account conflict."""

    member_id: UUID
    platform_account_id: UUID
    platform: Platform
    account_name: str
    account_handle: str | None


@dataclass(frozen=True, slots=True)
class CandidatePoolRunAmbiguityRecord:
    """A bounded first same-Influencer conflict in an ALL_MATCH source run."""

    influencer_id: UUID
    influencer_display_name: str
    accounts: tuple[CandidatePoolRunAmbiguityAccountRecord, ...]


@dataclass(frozen=True, slots=True)
class CandidatePoolMatchMemberIdentityRecord:
    """One safe MATCH row used by the streaming ALL_MATCH ambiguity preflight."""

    member_id: UUID
    influencer_id: UUID
    influencer_display_name: str
    platform_account_id: UUID
    platform: Platform
    account_name: str
    account_handle: str | None


class CampaignRepository:
    """Query and lock only the records used by Campaign/Outreach domain services."""

    _SINGLE_COLUMN_QUERY_CHUNK_SIZE = 500
    _MEMBER_WRITE_CHUNK_SIZE = 500
    # A restored row needs eight bind values in the portable CASE/CAS UPDATE.
    # Keep SQLite safely below its conservative 999-parameter ceiling.
    _SQLITE_MEMBER_WRITE_CHUNK_SIZE = 80

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_campaign(
        self,
        campaign_id: UUID,
        *,
        department_id: UUID | None,
        for_update: bool = False,
    ) -> Campaign | None:
        statement = select(Campaign).where(Campaign.id == campaign_id)
        if department_id is not None:
            statement = statement.where(Campaign.department_id == department_id)
        if for_update:
            # Refresh a preflighted identity-map row after acquiring the lock;
            # mutation guards must use the current status/version.
            statement = statement.execution_options(populate_existing=True).with_for_update()
        return cast(Campaign | None, await self.session.scalar(statement))

    async def get_campaign_with_owner(
        self,
        campaign_id: UUID,
        *,
        department_id: UUID,
    ) -> CampaignOwnerRecord | None:
        """Return one scoped Campaign and its owner without filtering disabled owners."""

        row = (
            await self.session.execute(
                select(Campaign, Operator)
                .join(
                    Operator,
                    and_(
                        Operator.id == Campaign.owner_operator_id,
                        Operator.department_id == Campaign.department_id,
                    ),
                )
                .where(
                    Campaign.id == campaign_id,
                    Campaign.department_id == department_id,
                )
            )
        ).first()
        if row is None:
            return None
        campaign, owner = row
        return CampaignOwnerRecord(campaign=campaign, owner=owner)

    async def list_campaigns(self, *, department_id: UUID | None) -> list[Campaign]:
        statement = select(Campaign)
        if department_id is not None:
            statement = statement.where(Campaign.department_id == department_id)
        return list(
            await self.session.scalars(
                statement.order_by(Campaign.updated_at.desc(), Campaign.id.desc())
            )
        )

    async def list_campaigns_page(
        self,
        *,
        department_id: UUID,
        cursor_updated_at: datetime | None,
        cursor_id: UUID | None,
        limit: int,
    ) -> CampaignRowsPage:
        """Return one Department-scoped Campaign page in the frozen DESC order."""

        statement = (
            select(Campaign, Operator)
            .join(
                Operator,
                and_(
                    Operator.id == Campaign.owner_operator_id,
                    Operator.department_id == Campaign.department_id,
                ),
            )
            .where(Campaign.department_id == department_id)
        )
        if cursor_updated_at is not None and cursor_id is not None:
            statement = statement.where(
                or_(
                    Campaign.updated_at < cursor_updated_at,
                    and_(
                        Campaign.updated_at == cursor_updated_at,
                        Campaign.id < cursor_id,
                    ),
                )
            )
        rows = tuple(
            await self.session.execute(
                statement.order_by(Campaign.updated_at.desc(), Campaign.id.desc()).limit(limit + 1)
            )
        )
        items = tuple(
            CampaignOwnerRecord(campaign=campaign, owner=owner) for campaign, owner in rows[:limit]
        )
        next_cursor = (
            (items[-1].campaign.updated_at, items[-1].campaign.id)
            if len(rows) > limit and items
            else None
        )
        return CampaignRowsPage(items=items, next_cursor=next_cursor)

    async def get_member(
        self,
        member_id: UUID,
        *,
        campaign_id: UUID,
        department_id: UUID | None,
        for_update: bool = False,
    ) -> CampaignMember | None:
        """Return the raw Member for non-display domain workflows."""

        statement = (
            select(CampaignMember)
            .where(
                CampaignMember.id == member_id,
                CampaignMember.campaign_id == campaign_id,
            )
            .execution_options(populate_existing=True)
        )
        if department_id is not None:
            statement = statement.where(CampaignMember.department_id == department_id)
        if for_update:
            statement = statement.with_for_update()
        return cast(CampaignMember | None, await self.session.scalar(statement))

    async def get_member_projection(
        self,
        member_id: UUID,
        *,
        campaign_id: UUID,
        department_id: UUID,
        for_update: bool = False,
    ) -> CampaignMemberProjectionRecord | None:
        """Return one scoped Member with its current, non-Contact display identity."""

        statement = (
            select(CampaignMember, Influencer, InfluencerPlatformAccount)
            .select_from(CampaignMember)
            .join(
                Campaign,
                and_(
                    Campaign.id == CampaignMember.campaign_id,
                    Campaign.department_id == CampaignMember.department_id,
                ),
            )
            .join(Influencer, Influencer.id == CampaignMember.influencer_id)
            .join(
                InfluencerPlatformAccount,
                and_(
                    InfluencerPlatformAccount.id == CampaignMember.preferred_platform_account_id,
                    InfluencerPlatformAccount.influencer_id == CampaignMember.influencer_id,
                ),
            )
            .where(
                CampaignMember.id == member_id,
                CampaignMember.campaign_id == campaign_id,
            )
            .execution_options(populate_existing=True)
        )
        statement = statement.where(CampaignMember.department_id == department_id)
        if for_update:
            statement = statement.with_for_update(of=CampaignMember)
        row = (await self.session.execute(statement)).first()
        if row is None:
            return None
        member, influencer, preferred_platform_account = row
        return CampaignMemberProjectionRecord(
            member=member,
            influencer=influencer,
            preferred_platform_account=preferred_platform_account,
        )

    async def list_active_members_page(
        self,
        *,
        campaign_id: UUID,
        department_id: UUID,
        cursor_created_at: datetime | None,
        cursor_id: UUID | None,
        limit: int,
    ) -> CampaignMemberRowsPage:
        """Return active Members in immutable creation order for one scoped Campaign."""

        statement = (
            select(CampaignMember, Influencer, InfluencerPlatformAccount)
            .select_from(CampaignMember)
            .join(
                Campaign,
                and_(
                    Campaign.id == CampaignMember.campaign_id,
                    Campaign.department_id == CampaignMember.department_id,
                ),
            )
            .join(Influencer, Influencer.id == CampaignMember.influencer_id)
            .join(
                InfluencerPlatformAccount,
                and_(
                    InfluencerPlatformAccount.id == CampaignMember.preferred_platform_account_id,
                    InfluencerPlatformAccount.influencer_id == CampaignMember.influencer_id,
                ),
            )
            .where(
                CampaignMember.campaign_id == campaign_id,
                CampaignMember.department_id == department_id,
                CampaignMember.removed_at.is_(None),
            )
        )
        if cursor_created_at is not None and cursor_id is not None:
            statement = statement.where(
                or_(
                    CampaignMember.created_at > cursor_created_at,
                    and_(
                        CampaignMember.created_at == cursor_created_at,
                        CampaignMember.id > cursor_id,
                    ),
                )
            )
        rows = tuple(
            await self.session.execute(
                statement.order_by(CampaignMember.created_at, CampaignMember.id).limit(limit + 1)
            )
        )
        items = tuple(
            CampaignMemberProjectionRecord(
                member=member,
                influencer=influencer,
                preferred_platform_account=preferred_platform_account,
            )
            for member, influencer, preferred_platform_account in rows[:limit]
        )
        next_cursor = (
            (items[-1].member.created_at, items[-1].member.id)
            if len(rows) > limit and items
            else None
        )
        return CampaignMemberRowsPage(items=items, next_cursor=next_cursor)

    async def list_members_for_influencers(
        self,
        campaign_id: UUID,
        influencer_ids: tuple[UUID, ...],
        *,
        for_update: bool = False,
    ) -> list[CampaignMember]:
        if not influencer_ids:
            return []
        members: list[CampaignMember] = []
        for offset in range(0, len(influencer_ids), self._SINGLE_COLUMN_QUERY_CHUNK_SIZE):
            statement = (
                select(CampaignMember)
                .where(
                    CampaignMember.campaign_id == campaign_id,
                    CampaignMember.influencer_id.in_(
                        influencer_ids[offset : offset + self._SINGLE_COLUMN_QUERY_CHUNK_SIZE]
                    ),
                )
                .execution_options(populate_existing=True)
            )
            if for_update:
                statement = statement.with_for_update()
            members.extend(await self.session.scalars(statement))
        return members

    async def list_active_member_ids(self, campaign_id: UUID, *, limit: int) -> set[UUID]:
        if limit <= 0:
            return set()
        result = await self.session.scalars(
            select(CampaignMember.id)
            .where(
                CampaignMember.campaign_id == campaign_id,
                CampaignMember.removed_at.is_(None),
            )
            .order_by(CampaignMember.created_at, CampaignMember.id)
            .limit(limit)
        )
        return set(result)

    async def count_active_members(self, campaign_id: UUID) -> int:
        result = await self.session.scalar(
            select(func.count(CampaignMember.id)).where(
                CampaignMember.campaign_id == campaign_id,
                CampaignMember.removed_at.is_(None),
            )
        )
        return int(result or 0)

    async def insert_campaign_members(self, members: Sequence[CampaignMemberInsertRow]) -> None:
        """Insert new Members in bounded multi-row Core statements.

        IDs are supplied by the service so the shared transaction can retain the
        same UUID convention while this helper avoids ORM row persistence.
        """

        if not members:
            return
        member_table = cast(Table, CampaignMember.__table__)
        chunk_size = self._member_write_chunk_size()
        for offset in range(0, len(members), chunk_size):
            chunk = members[offset : offset + chunk_size]
            await self.session.execute(
                insert(member_table).values(
                    [
                        {
                            "id": member.id,
                            "department_id": member.department_id,
                            "campaign_id": member.campaign_id,
                            "influencer_id": member.influencer_id,
                            "preferred_platform_account_id": member.preferred_platform_account_id,
                            "source_pool_run_id": member.source_pool_run_id,
                            "added_by_operator_id": member.added_by_operator_id,
                            "removed_at": None,
                            "version": 1,
                        }
                        for member in chunk
                    ]
                )
            )

    async def restore_campaign_members(
        self,
        campaign_id: UUID,
        members: Sequence[CampaignMemberRestoreRow],
    ) -> int:
        """Restore locked rows with one CAS-protected Core UPDATE per chunk."""

        if not members:
            return 0
        member_table = cast(Table, CampaignMember.__table__)
        updated_count = 0
        chunk_size = self._member_write_chunk_size()
        for offset in range(0, len(members), chunk_size):
            chunk = members[offset : offset + chunk_size]
            version_predicate = or_(
                *(
                    and_(
                        member_table.c.id == member.member_id,
                        member_table.c.version == member.expected_version,
                    )
                    for member in chunk
                )
            )
            statement = (
                update(member_table)
                .where(
                    member_table.c.campaign_id == campaign_id,
                    member_table.c.removed_at.is_not(None),
                    version_predicate,
                )
                .values(
                    preferred_platform_account_id=case(
                        *(
                            (
                                member_table.c.id == member.member_id,
                                member.preferred_platform_account_id,
                            )
                            for member in chunk
                        ),
                        else_=member_table.c.preferred_platform_account_id,
                    ),
                    source_pool_run_id=case(
                        *(
                            (member_table.c.id == member.member_id, member.source_pool_run_id)
                            for member in chunk
                        ),
                        else_=member_table.c.source_pool_run_id,
                    ),
                    added_by_operator_id=case(
                        *(
                            (member_table.c.id == member.member_id, member.added_by_operator_id)
                            for member in chunk
                        ),
                        else_=member_table.c.added_by_operator_id,
                    ),
                    removed_at=None,
                    version=member_table.c.version + 1,
                    updated_at=func.now(),
                )
                .execution_options(synchronize_session=False)
            )
            result = await self.session.execute(statement)
            updated_count += int(cast(CursorResult[Any], result).rowcount or 0)
        return updated_count

    def _member_write_chunk_size(self) -> int:
        if self.session.get_bind().dialect.name == "sqlite":
            return self._SQLITE_MEMBER_WRITE_CHUNK_SIZE
        return self._MEMBER_WRITE_CHUNK_SIZE

    async def get_platform_accounts(
        self,
        account_ids: tuple[UUID, ...],
    ) -> list[InfluencerPlatformAccount]:
        if not account_ids:
            return []
        accounts: list[InfluencerPlatformAccount] = []
        for offset in range(0, len(account_ids), self._SINGLE_COLUMN_QUERY_CHUNK_SIZE):
            accounts.extend(
                await self.session.scalars(
                    select(InfluencerPlatformAccount).where(
                        InfluencerPlatformAccount.id.in_(
                            account_ids[offset : offset + self._SINGLE_COLUMN_QUERY_CHUNK_SIZE]
                        )
                    )
                )
            )
        return accounts

    async def get_platform_account(
        self,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> InfluencerPlatformAccount | None:
        statement = select(InfluencerPlatformAccount).where(
            InfluencerPlatformAccount.id == account_id
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(InfluencerPlatformAccount | None, await self.session.scalar(statement))

    async def get_contact(
        self,
        contact_id: UUID,
        *,
        for_update: bool = False,
    ) -> InfluencerContact | None:
        statement = select(InfluencerContact).where(InfluencerContact.id == contact_id)
        if for_update:
            statement = statement.with_for_update()
        return cast(InfluencerContact | None, await self.session.scalar(statement))

    async def get_source_pool_run(
        self,
        source_pool_run_id: UUID,
        *,
        department_id: UUID,
        for_update: bool = False,
    ) -> CandidatePoolRun | None:
        """Return one Department-owned run without disclosing another Department's row."""

        statement = (
            select(CandidatePoolRun)
            .join(CandidatePool, CandidatePoolRun.pool_id == CandidatePool.id)
            .where(
                CandidatePoolRun.id == source_pool_run_id,
                CandidatePool.department_id == department_id,
            )
        )
        if for_update:
            statement = statement.execution_options(populate_existing=True).with_for_update()
        return cast(
            CandidatePoolRun | None,
            await self.session.scalar(statement),
        )

    async def list_selected_source_run_members(
        self,
        *,
        source_pool_run_id: UUID,
        member_ids: tuple[UUID, ...],
    ) -> tuple[CandidatePoolMember, ...]:
        """Resolve an explicit selected Member-ID set from one completed source run.

        This is one set query for the bounded 10,000-ID public maximum.  The
        enum predicate is deliberate: only materialized MATCH and UNKNOWN rows
        may enter the Campaign provenance path.
        """

        if not member_ids:
            return ()
        statement = (
            select(CandidatePoolMember)
            .where(
                CandidatePoolMember.run_id == source_pool_run_id,
                CandidatePoolMember.id.in_(member_ids),
                CandidatePoolMember.result.in_((CandidateResult.MATCH, CandidateResult.UNKNOWN)),
            )
            .order_by(CandidatePoolMember.id)
        )
        return tuple(await self.session.scalars(statement))

    async def count_matching_source_run_members(
        self,
        *,
        source_pool_run_id: UUID,
        member_ids: tuple[UUID, ...],
    ) -> int:
        """Count bounded exclusion IDs that are persisted MATCH rows of one run.

        The request contract caps exclusions at 10,000 IDs.  Still split the
        set queries so SQLite and other conservative bind-parameter limits do
        not turn a valid request into a backend-specific failure.
        """

        valid_count = 0
        for offset in range(0, len(member_ids), self._SINGLE_COLUMN_QUERY_CHUNK_SIZE):
            chunk = member_ids[offset : offset + self._SINGLE_COLUMN_QUERY_CHUNK_SIZE]
            count = await self.session.scalar(
                select(func.count(CandidatePoolMember.id)).where(
                    CandidatePoolMember.run_id == source_pool_run_id,
                    CandidatePoolMember.id.in_(chunk),
                    CandidatePoolMember.result == CandidateResult.MATCH,
                )
            )
            valid_count += int(count or 0)
        return valid_count

    async def list_matching_source_run_members_page(
        self,
        *,
        source_pool_run_id: UUID,
        after_member_id: UUID | None,
        limit: int,
    ) -> tuple[CandidatePoolMatchMemberRecord, ...]:
        """Read one bounded UUID-keyset page of lightweight MATCH source rows."""

        statement = select(
            CandidatePoolMember.id,
            CandidatePoolMember.influencer_id,
            CandidatePoolMember.platform_account_id,
        ).where(
            CandidatePoolMember.run_id == source_pool_run_id,
            CandidatePoolMember.result == CandidateResult.MATCH,
        )
        if after_member_id is not None:
            statement = statement.where(CandidatePoolMember.id > after_member_id)
        rows = tuple(
            await self.session.execute(statement.order_by(CandidatePoolMember.id).limit(limit))
        )
        return tuple(
            CandidatePoolMatchMemberRecord(
                id=member_id,
                influencer_id=influencer_id,
                platform_account_id=platform_account_id,
            )
            for member_id, influencer_id, platform_account_id in rows
        )

    async def first_matching_source_run_ambiguity(
        self,
        *,
        source_pool_run_id: UUID,
        excluded_member_ids: set[UUID],
        chunk_size: int,
    ) -> CandidatePoolRunAmbiguityRecord | None:
        """Find one unresolved same-Influencer MATCH conflict in one result stream.

        Exclusions are intentionally filtered in the service's bounded set,
        rather than expanded into one unportable 10,000-parameter ``NOT IN``
        clause.  There is deliberately one server-side ordered stream rather
        than keyset pages ordered by ``influencer_id``: the persisted index is
        ``(run_id, result, id)``, not ``(run_id, result, influencer_id, id)``.
        Reissuing an influencer sort for every keyset page would repeatedly
        sort the same run and can become quadratic.  Streaming one ordered
        result limits application memory to ``chunk_size`` rows while retaining
        only one selected account for the current Influencer.
        """

        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")

        statement = (
            select(
                CandidatePoolMember.id,
                CandidatePoolMember.influencer_id,
                Influencer.display_name,
                CandidatePoolMember.platform_account_id,
                InfluencerPlatformAccount.platform,
                InfluencerPlatformAccount.account_name,
                InfluencerPlatformAccount.account_handle,
            )
            .join(Influencer, Influencer.id == CandidatePoolMember.influencer_id)
            .join(
                InfluencerPlatformAccount,
                and_(
                    InfluencerPlatformAccount.id == CandidatePoolMember.platform_account_id,
                    InfluencerPlatformAccount.influencer_id == CandidatePoolMember.influencer_id,
                ),
            )
            .where(
                CandidatePoolMember.run_id == source_pool_run_id,
                CandidatePoolMember.result == CandidateResult.MATCH,
            )
        )
        result = await self.session.stream(
            statement.order_by(CandidatePoolMember.influencer_id, CandidatePoolMember.id),
            execution_options={"yield_per": chunk_size},
        )
        first_selected_account: CandidatePoolMatchMemberIdentityRecord | None = None
        try:
            async for rows in result.partitions(chunk_size):
                for (
                    member_id,
                    influencer_id,
                    display_name,
                    platform_account_id,
                    platform,
                    account_name,
                    account_handle,
                ) in rows:
                    source_member = CandidatePoolMatchMemberIdentityRecord(
                        member_id=member_id,
                        influencer_id=influencer_id,
                        influencer_display_name=display_name,
                        platform_account_id=platform_account_id,
                        platform=platform,
                        account_name=account_name,
                        account_handle=account_handle,
                    )
                    if source_member.member_id in excluded_member_ids:
                        continue
                    if (
                        first_selected_account is None
                        or source_member.influencer_id != first_selected_account.influencer_id
                    ):
                        first_selected_account = source_member
                        continue
                    if (
                        source_member.platform_account_id
                        == first_selected_account.platform_account_id
                    ):
                        # The persisted run is unique by account, but retain the
                        # exact explicit-selection contract defensively.
                        continue
                    return self._ambiguity_record(first_selected_account, source_member)
        finally:
            await result.close()
        return None

    async def source_run_member_pair_ambiguity(
        self,
        *,
        source_pool_run_id: UUID,
        member_ids: tuple[UUID, UUID],
    ) -> CandidatePoolRunAmbiguityRecord | None:
        """Return safe identities for one already-detected explicit selection conflict.

        The caller provides exactly two selected Member IDs, so this remains a
        fixed-size joined query even when the explicit selection itself has
        the public 10,000-ID maximum.
        """

        statement = (
            select(
                CandidatePoolMember.id,
                CandidatePoolMember.influencer_id,
                Influencer.display_name,
                CandidatePoolMember.platform_account_id,
                InfluencerPlatformAccount.platform,
                InfluencerPlatformAccount.account_name,
                InfluencerPlatformAccount.account_handle,
            )
            .join(Influencer, Influencer.id == CandidatePoolMember.influencer_id)
            .join(
                InfluencerPlatformAccount,
                and_(
                    InfluencerPlatformAccount.id == CandidatePoolMember.platform_account_id,
                    InfluencerPlatformAccount.influencer_id == CandidatePoolMember.influencer_id,
                ),
            )
            .where(
                CandidatePoolMember.run_id == source_pool_run_id,
                CandidatePoolMember.id.in_(member_ids),
                CandidatePoolMember.result.in_((CandidateResult.MATCH, CandidateResult.UNKNOWN)),
            )
            .order_by(CandidatePoolMember.id)
        )
        rows = tuple(await self.session.execute(statement))
        if len(rows) != 2:
            return None
        identities = tuple(
            CandidatePoolMatchMemberIdentityRecord(
                member_id=member_id,
                influencer_id=influencer_id,
                influencer_display_name=display_name,
                platform_account_id=platform_account_id,
                platform=platform,
                account_name=account_name,
                account_handle=account_handle,
            )
            for (
                member_id,
                influencer_id,
                display_name,
                platform_account_id,
                platform,
                account_name,
                account_handle,
            ) in rows
        )
        first, second = identities
        if (
            first.influencer_id != second.influencer_id
            or first.platform_account_id == second.platform_account_id
        ):
            return None
        return self._ambiguity_record(first, second)

    @staticmethod
    def _ambiguity_record(
        first: CandidatePoolMatchMemberIdentityRecord,
        second: CandidatePoolMatchMemberIdentityRecord,
    ) -> CandidatePoolRunAmbiguityRecord:
        return CandidatePoolRunAmbiguityRecord(
            influencer_id=first.influencer_id,
            influencer_display_name=first.influencer_display_name,
            accounts=(
                CandidatePoolRunAmbiguityAccountRecord(
                    member_id=first.member_id,
                    platform_account_id=first.platform_account_id,
                    platform=first.platform,
                    account_name=first.account_name,
                    account_handle=first.account_handle,
                ),
                CandidatePoolRunAmbiguityAccountRecord(
                    member_id=second.member_id,
                    platform_account_id=second.platform_account_id,
                    platform=second.platform,
                    account_name=second.account_name,
                    account_handle=second.account_handle,
                ),
            ),
        )

    async def list_targets_for_campaign(
        self,
        campaign_id: UUID,
        *,
        active_members_only: bool = False,
    ) -> list[OutreachTarget]:
        statement = select(OutreachTarget).where(OutreachTarget.campaign_id == campaign_id)
        if active_members_only:
            statement = statement.join(
                CampaignMember, CampaignMember.id == OutreachTarget.member_id
            ).where(CampaignMember.removed_at.is_(None))
        return list(await self.session.scalars(statement))

    async def get_target(
        self,
        target_id: UUID,
        *,
        campaign_id: UUID | None = None,
        department_id: UUID | None,
        for_update: bool = False,
    ) -> OutreachTarget | None:
        statement = select(OutreachTarget).where(OutreachTarget.id == target_id)
        if campaign_id is not None:
            statement = statement.where(OutreachTarget.campaign_id == campaign_id)
        if department_id is not None:
            statement = statement.where(OutreachTarget.department_id == department_id)
        if for_update:
            statement = statement.with_for_update()
        return cast(OutreachTarget | None, await self.session.scalar(statement))

    async def get_target_by_campaign_member_channel(
        self,
        *,
        campaign_id: UUID,
        member_id: UUID,
        channel: OutreachChannel,
    ) -> OutreachTarget | None:
        return cast(
            OutreachTarget | None,
            await self.session.scalar(
                select(OutreachTarget).where(
                    OutreachTarget.campaign_id == campaign_id,
                    OutreachTarget.member_id == member_id,
                    OutreachTarget.channel == channel,
                )
            ),
        )

    async def get_task(
        self,
        task_id: UUID,
        *,
        department_id: UUID | None,
        for_update: bool = False,
    ) -> OutreachTask | None:
        statement = select(OutreachTask).where(OutreachTask.id == task_id)
        if department_id is not None:
            statement = statement.where(OutreachTask.department_id == department_id)
        if for_update:
            statement = statement.with_for_update()
        return cast(OutreachTask | None, await self.session.scalar(statement))

    async def get_task_by_create_idempotency_key(
        self,
        *,
        department_id: UUID,
        idempotency_key: str,
    ) -> OutreachTask | None:
        return cast(
            OutreachTask | None,
            await self.session.scalar(
                select(OutreachTask).where(
                    OutreachTask.department_id == department_id,
                    OutreachTask.create_idempotency_key == idempotency_key,
                )
            ),
        )

    async def get_task_by_target_step(
        self,
        *,
        target_id: UUID,
        step_key: str,
    ) -> OutreachTask | None:
        return cast(
            OutreachTask | None,
            await self.session.scalar(
                select(OutreachTask).where(
                    OutreachTask.outreach_target_id == target_id,
                    OutreachTask.step_key == step_key,
                )
            ),
        )

    async def get_event_by_idempotency_key(
        self,
        *,
        task_id: UUID,
        idempotency_key: str,
    ) -> OutreachEvent | None:
        return cast(
            OutreachEvent | None,
            await self.session.scalar(
                select(OutreachEvent)
                .where(
                    OutreachEvent.task_id == task_id,
                    OutreachEvent.idempotency_key == idempotency_key,
                )
                .execution_options(populate_existing=True)
            ),
        )

    async def get_task_created_event(self, task_id: UUID) -> OutreachEvent | None:
        return cast(
            OutreachEvent | None,
            await self.session.scalar(
                select(OutreachEvent)
                .where(
                    OutreachEvent.task_id == task_id,
                    OutreachEvent.event_type == OutreachEventType.TASK_CREATED,
                )
                .order_by(OutreachEvent.occurred_at, OutreachEvent.id)
                .limit(1)
            ),
        )

    async def list_task_events(self, task_id: UUID) -> list[OutreachEvent]:
        return list(
            await self.session.scalars(
                select(OutreachEvent)
                .where(OutreachEvent.task_id == task_id)
                .order_by(OutreachEvent.occurred_at, OutreachEvent.id)
            )
        )

    async def latest_sent_event(
        self,
        *,
        influencer_id: UUID,
        channel: OutreachChannel,
        not_before: datetime | None = None,
    ) -> OutreachEvent | None:
        statement = select(OutreachEvent).where(
            OutreachEvent.influencer_id == influencer_id,
            OutreachEvent.channel == channel,
            OutreachEvent.event_type == OutreachEventType.OUTREACH_SENT,
        )
        if not_before is not None:
            statement = statement.where(OutreachEvent.occurred_at >= not_before)
        return cast(
            OutreachEvent | None,
            await self.session.scalar(
                statement.order_by(OutreachEvent.occurred_at.desc(), OutreachEvent.id.desc())
            ),
        )

    async def get_template_version(
        self,
        template_version_id: UUID,
    ) -> TemplateVersionRecord | None:
        row = (
            await self.session.execute(
                select(MessageTemplate, MessageTemplateVersion)
                .join(
                    MessageTemplateVersion, MessageTemplateVersion.template_id == MessageTemplate.id
                )
                .where(MessageTemplateVersion.id == template_version_id)
            )
        ).one_or_none()
        if row is None:
            return None
        template, version = row
        return TemplateVersionRecord(template=template, version=version)

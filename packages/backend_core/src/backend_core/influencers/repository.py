"""Read-only persistence queries for the company-level influencer library."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Numeric,
    and_,
    bindparam,
    case,
    cast,
    exists,
    func,
    literal,
    or_,
    select,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import CTE, Subquery

from backend_core.auth.models import Operator
from backend_core.imports.enums import (
    ImportJobFileStatus,
    ImportJobStatus,
    ImportRowAction,
    ImportSourceType,
)
from backend_core.imports.models import ImportJob, ImportJobFile, ImportRow
from backend_core.influencers.enums import (
    ContactFilter,
    ContactType,
    DataSource,
    InfluencerStatus,
    Notes60dFilter,
)
from backend_core.influencers.freshness import FreshnessPolicy, FreshnessStatus
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
    InfluencerSourceState,
    PlatformAccountSourceIdentity,
)
from backend_core.influencers.schemas import InfluencerListQuery


@dataclass(frozen=True, slots=True)
class InfluencerListRecord:
    """ORM records needed by the service to build one list item."""

    influencer: Influencer
    owner: Operator | None
    platform_accounts: tuple[InfluencerPlatformAccount, ...]
    current_metrics: tuple[InfluencerCurrentMetrics, ...]
    current_contacts: tuple[InfluencerContact, ...]
    huitun_freshness: tuple[AccountSourceFreshnessRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class InfluencerDetailRecord:
    """ORM records needed by the service to build the read-only detail."""

    influencer: Influencer
    owner: Operator | None
    platform_accounts: tuple[InfluencerPlatformAccount, ...]
    contacts: tuple[InfluencerContact, ...]
    source_states: tuple[InfluencerSourceState, ...]
    source_identities: tuple[PlatformAccountSourceIdentity, ...]
    current_metrics: tuple[InfluencerCurrentMetrics, ...]
    huitun_freshness: tuple[AccountSourceFreshnessRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class AccountSourceFreshnessRecord:
    """Confirmed import lineage for one eligible account/source pair."""

    platform_account_id: UUID
    source: DataSource
    last_observed_at: datetime | None
    last_imported_at: datetime | None


def visible_influencer_criteria() -> tuple[ColumnElement[bool], ...]:
    """Return the company-level visibility predicate shared by read domains."""

    return (
        Influencer.status == InfluencerStatus.ACTIVE,
        Influencer.deleted_at.is_(None),
    )


def huitun_confirmed_lineage() -> Subquery:
    """Aggregate successful Huitun Confirm evidence once per account.

    Imported time deliberately includes the Legacy compatibility path.
    Observed time additionally requires a Bulk occurrence carrying a
    confirmed acquisition timestamp; committed time never fills it.
    """

    committed_actions = (
        ImportRowAction.CREATE,
        ImportRowAction.UPDATE,
        ImportRowAction.NO_CHANGE,
    )
    reliable_observation = and_(
        ImportJob.stored_file_id.is_(None),
        ImportJobFile.source_acquired_at.is_not(None),
        ImportJobFile.source_acquired_at_confirmation_required.is_(False),
    )
    return (
        select(
            ImportRow.matched_platform_account_id.label("platform_account_id"),
            func.max(
                case(
                    (reliable_observation, ImportJobFile.source_acquired_at),
                    else_=None,
                )
            ).label("last_observed_at"),
            func.max(ImportRow.committed_at).label("last_imported_at"),
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
            ImportRow.matched_platform_account_id.is_not(None),
            ImportRow.committed_at.is_not(None),
            ImportRow.committed_action.in_(committed_actions),
            ImportJob.status == ImportJobStatus.COMPLETED,
            ImportJob.confirmed_revision.is_not(None),
            ImportRow.preview_revision == ImportJob.confirmed_revision,
            ImportJob.source_type == ImportSourceType.MANUAL_HUITUN_EXPORT,
            ImportJobFile.status == ImportJobFileStatus.READY,
        )
        .group_by(ImportRow.matched_platform_account_id)
        .subquery("huitun_confirmed_lineage")
    )


def eligible_huitun_freshness() -> CTE:
    """Return active accounts with real Huitun source evidence.

    The account's creation source is not sufficient: an account is eligible
    only through Huitun SourceState, SourceIdentity, or successful Huitun
    Confirm lineage. Queue and library reads deliberately share this truth.
    """

    lineage = huitun_confirmed_lineage()
    has_source_state = exists(
        select(1)
        .select_from(InfluencerSourceState)
        .where(
            InfluencerSourceState.platform_account_id == InfluencerPlatformAccount.id,
            InfluencerSourceState.influencer_id == InfluencerPlatformAccount.influencer_id,
            InfluencerSourceState.source == DataSource.HUITUN,
        )
    )
    has_source_identity = exists(
        select(1)
        .select_from(PlatformAccountSourceIdentity)
        .where(
            PlatformAccountSourceIdentity.platform_account_id == InfluencerPlatformAccount.id,
            PlatformAccountSourceIdentity.source == DataSource.HUITUN,
        )
    )
    return (
        select(
            InfluencerPlatformAccount.id.label("platform_account_id"),
            InfluencerPlatformAccount.influencer_id.label("influencer_id"),
            lineage.c.last_observed_at,
            lineage.c.last_imported_at,
        )
        .outerjoin(
            lineage,
            lineage.c.platform_account_id == InfluencerPlatformAccount.id,
        )
        .where(
            InfluencerPlatformAccount.is_active.is_(True),
            or_(
                lineage.c.platform_account_id.is_not(None),
                has_source_state,
                has_source_identity,
            ),
        )
        .cte("eligible_huitun_freshness")
    )


def freshness_status_expression(
    eligible: CTE,
    *,
    as_of: datetime,
    policy: FreshnessPolicy,
) -> ColumnElement[str]:
    """Build the SQL status expression from the same validated policy object."""

    observed_at = eligible.c.last_observed_at
    return case(
        (observed_at.is_(None), literal(FreshnessStatus.UNKNOWN.value)),
        (
            observed_at >= as_of - policy.fresh_duration,
            literal(FreshnessStatus.FRESH.value),
        ),
        (
            observed_at >= as_of - policy.aging_duration,
            literal(FreshnessStatus.AGING.value),
        ),
        (
            observed_at >= as_of - policy.stale_duration,
            literal(FreshnessStatus.STALE.value),
        ),
        else_=literal(FreshnessStatus.VERY_STALE.value),
    )


def followers_count_expression(dialect_name: str) -> ColumnElement[Any]:
    """Return the shared strict non-coercing current follower projection."""

    if dialect_name == "postgresql":
        json_value = cast(InfluencerCurrentMetrics.metrics, JSONB)["followers_count"]
        text_value = json_value.as_string()
        valid_integer = and_(
            func.jsonb_typeof(json_value) == "number",
            text_value.op("~")(r"^(0|[1-9][0-9]*)$"),
        )
        return case(
            (valid_integer, cast(text_value, Numeric())),
            else_=None,
        )
    return case(
        (
            func.json_type(
                InfluencerCurrentMetrics.metrics,
                "$.followers_count",
            )
            == "integer",
            func.json_extract(
                InfluencerCurrentMetrics.metrics,
                "$.followers_count",
            ),
        ),
        else_=None,
    )


def notes_60d_expression(dialect_name: str) -> ColumnElement[Any]:
    """Return the shared strict non-coercing current notes_60d projection."""

    if dialect_name == "postgresql":
        json_value = cast(InfluencerCurrentMetrics.metrics, JSONB)["notes_60d"]
        text_value = json_value.as_string()
        valid_integer = and_(
            func.jsonb_typeof(json_value) == "number",
            text_value.op("~")(r"^(0|[1-9][0-9]*)$"),
        )
        return case((valid_integer, cast(text_value, Numeric())), else_=None)
    return case(
        (
            func.json_type(InfluencerCurrentMetrics.metrics, "$.notes_60d") == "integer",
            func.json_extract(InfluencerCurrentMetrics.metrics, "$.notes_60d"),
        ),
        else_=None,
    )


def _visible_influencer_criteria() -> tuple[ColumnElement[bool], ...]:
    """Backward-compatible private alias for the original Task 7 helper."""

    return visible_influencer_criteria()


def _literal_contains_pattern(value: str) -> str:
    """Escape LIKE metacharacters so user text is always matched literally."""

    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _utc_database_timestamp(value: datetime | None) -> datetime | None:
    """Normalize TIMESTAMPTZ values; SQLite drops offsets in portable tests."""

    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_as_of(value: datetime | None) -> datetime:
    resolved = value or datetime.now(UTC)
    if resolved.tzinfo is None or resolved.utcoffset() is None:
        raise ValueError("freshness as_of must be timezone-aware")
    return resolved.astimezone(UTC)


class InfluencerRepository:
    """Explicit, fixed-query-count reads without ORM lazy relationships."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def _dialect_name(self) -> str:
        return self.session.get_bind().dialect.name

    @staticmethod
    def _huitun_confirmed_lineage() -> Subquery:
        return huitun_confirmed_lineage()

    @classmethod
    def _eligible_huitun_freshness(cls) -> CTE:
        """Return active accounts with real Huitun source evidence.

        The account's creation source is not sufficient: an account is
        eligible only through Huitun SourceState, SourceIdentity, or successful
        Huitun Confirm lineage.
        """

        return eligible_huitun_freshness()

    @staticmethod
    def _freshness_status_expression(
        eligible: CTE,
        *,
        as_of: datetime,
        policy: FreshnessPolicy,
    ) -> ColumnElement[str]:
        return freshness_status_expression(eligible, as_of=as_of, policy=policy)

    def _search_criterion(self, value: str) -> ColumnElement[bool]:
        pattern = _literal_contains_pattern(value)
        account_match = exists(
            select(1)
            .select_from(InfluencerPlatformAccount)
            .where(
                InfluencerPlatformAccount.influencer_id == Influencer.id,
                InfluencerPlatformAccount.is_active.is_(True),
                InfluencerPlatformAccount.account_name.ilike(pattern, escape="\\"),
            )
        )
        return or_(
            Influencer.display_name.ilike(pattern, escape="\\"),
            account_match,
        )

    def _tag_criterion(self, value: str) -> ColumnElement[bool]:
        account_criteria: list[ColumnElement[bool]] = [
            InfluencerPlatformAccount.influencer_id == Influencer.id,
            InfluencerPlatformAccount.is_active.is_(True),
        ]
        if self._dialect_name == "postgresql":
            account_criteria.append(
                cast(InfluencerPlatformAccount.source_tags, JSONB).contains([value])
            )
            return exists(select(1).select_from(InfluencerPlatformAccount).where(*account_criteria))

        tag_values = (
            func.json_each(InfluencerPlatformAccount.source_tags)
            .table_valued("value", joins_implicitly=True)
            .alias("source_tag_values")
        )
        return exists(
            select(1)
            .select_from(InfluencerPlatformAccount)
            .join(tag_values, true())
            .where(*account_criteria, tag_values.c.value == value)
        )

    def _followers_criterion(
        self,
        followers_min: int | None,
        followers_max: int | None,
    ) -> ColumnElement[bool]:
        comparable_value = followers_count_expression(self._dialect_name)
        if self._dialect_name == "postgresql":
            lower_bound: Decimal | int | None = (
                Decimal(followers_min) if followers_min is not None else None
            )
            upper_bound: Decimal | int | None = (
                Decimal(followers_max) if followers_max is not None else None
            )
            numeric_type: Numeric[Decimal] | None = Numeric()
        else:
            comparable_value = case(
                (
                    func.json_type(
                        InfluencerCurrentMetrics.metrics,
                        "$.followers_count",
                    )
                    == "integer",
                    func.json_extract(
                        InfluencerCurrentMetrics.metrics,
                        "$.followers_count",
                    ),
                ),
                else_=None,
            )
            lower_bound = followers_min
            upper_bound = followers_max
            numeric_type = None

        conditions: list[ColumnElement[bool]] = [
            InfluencerCurrentMetrics.influencer_id == Influencer.id,
            InfluencerPlatformAccount.is_active.is_(True),
        ]
        if lower_bound is not None:
            conditions.append(
                comparable_value
                >= bindparam(
                    "followers_min",
                    lower_bound,
                    type_=numeric_type,
                )
            )
        if upper_bound is not None:
            conditions.append(
                comparable_value
                <= bindparam(
                    "followers_max",
                    upper_bound,
                    type_=numeric_type,
                )
            )

        return exists(
            select(1)
            .select_from(InfluencerCurrentMetrics)
            .join(
                InfluencerPlatformAccount,
                and_(
                    InfluencerPlatformAccount.id == InfluencerCurrentMetrics.platform_account_id,
                    InfluencerPlatformAccount.influencer_id
                    == InfluencerCurrentMetrics.influencer_id,
                ),
            )
            .where(*conditions)
        )

    def _contact_criterion(self, contact_filter: ContactFilter) -> ColumnElement[bool]:
        conditions: list[ColumnElement[bool]] = [
            InfluencerContact.influencer_id == Influencer.id,
            InfluencerContact.is_current.is_(True),
        ]
        if contact_filter is ContactFilter.HAS_EMAIL:
            conditions.append(InfluencerContact.type == ContactType.EMAIL)
        has_contact = exists(select(1).select_from(InfluencerContact).where(*conditions))
        return ~has_contact if contact_filter is ContactFilter.NO_CONTACT else has_contact

    def _notes_60d_criterion(self, notes_filter: Notes60dFilter) -> ColumnElement[bool]:
        value = notes_60d_expression(self._dialect_name)
        conditions: list[ColumnElement[bool]] = [
            InfluencerCurrentMetrics.influencer_id == Influencer.id,
            InfluencerPlatformAccount.is_active.is_(True),
        ]
        valid_value = value.is_not(None)
        if notes_filter is Notes60dFilter.ZERO:
            conditions.append(value == 0)
        elif notes_filter is Notes60dFilter.ONE_TO_TWO:
            conditions.extend((value >= 1, value <= 2))
        elif notes_filter is Notes60dFilter.THREE_TO_NINE:
            conditions.extend((value >= 3, value <= 9))
        elif notes_filter is Notes60dFilter.TEN_OR_MORE:
            conditions.append(value >= 10)
        else:
            conditions.append(valid_value)

        has_matching_metric = exists(
            select(1)
            .select_from(InfluencerCurrentMetrics)
            .join(
                InfluencerPlatformAccount,
                and_(
                    InfluencerPlatformAccount.id == InfluencerCurrentMetrics.platform_account_id,
                    InfluencerPlatformAccount.influencer_id
                    == InfluencerCurrentMetrics.influencer_id,
                ),
            )
            .where(*conditions)
        )
        return (
            ~has_matching_metric if notes_filter is Notes60dFilter.MISSING else has_matching_metric
        )

    def _list_criteria(
        self,
        query: InfluencerListQuery,
        *,
        as_of: datetime,
        policy: FreshnessPolicy,
    ) -> list[ColumnElement[bool]]:
        criteria = list(_visible_influencer_criteria())
        if query.q is not None:
            criteria.append(self._search_criterion(query.q))
        if query.tag is not None:
            criteria.append(self._tag_criterion(query.tag))
        if query.followers_min is not None or query.followers_max is not None:
            criteria.append(self._followers_criterion(query.followers_min, query.followers_max))
        if query.owner_operator_id is not None:
            criteria.append(Influencer.owner_operator_id == query.owner_operator_id)
        if query.crm_stage is not None:
            criteria.append(Influencer.crm_stage == query.crm_stage)
        if query.contact_filter is not None:
            criteria.append(self._contact_criterion(query.contact_filter))
        if query.notes_60d_filter is not None:
            criteria.append(self._notes_60d_criterion(query.notes_60d_filter))
        if (
            query.freshness_status is not None
            or query.requires_refresh is not None
            or query.last_huitun_observed_before is not None
            or query.last_huitun_observed_after is not None
        ):
            eligible = self._eligible_huitun_freshness()
            correlated = [eligible.c.influencer_id == Influencer.id]
            if query.freshness_status is not None:
                status_expression = self._freshness_status_expression(
                    eligible,
                    as_of=as_of,
                    policy=policy,
                )
                criteria.append(
                    exists(
                        select(1)
                        .select_from(eligible)
                        .where(
                            *correlated,
                            status_expression == query.freshness_status.value,
                        )
                    )
                )
            if query.requires_refresh is not None:
                needs_refresh = or_(
                    eligible.c.last_observed_at.is_(None),
                    eligible.c.last_observed_at < as_of - policy.aging_duration,
                )
                has_refresh_required_account = exists(
                    select(1).select_from(eligible).where(*correlated, needs_refresh)
                )
                criteria.append(
                    has_refresh_required_account
                    if query.requires_refresh
                    else ~has_refresh_required_account
                )
            if (
                query.last_huitun_observed_before is not None
                or query.last_huitun_observed_after is not None
            ):
                observed_criteria = [
                    *correlated,
                    eligible.c.last_observed_at.is_not(None),
                ]
                if query.last_huitun_observed_before is not None:
                    observed_criteria.append(
                        eligible.c.last_observed_at <= query.last_huitun_observed_before
                    )
                if query.last_huitun_observed_after is not None:
                    observed_criteria.append(
                        eligible.c.last_observed_at >= query.last_huitun_observed_after
                    )
                criteria.append(exists(select(1).select_from(eligible).where(*observed_criteria)))
        return criteria

    async def list_influencers(
        self,
        query: InfluencerListQuery,
        *,
        as_of: datetime | None = None,
        policy: FreshnessPolicy | None = None,
    ) -> tuple[list[InfluencerListRecord], int]:
        """Return one aggregate per visible influencer and the exact subject total."""

        resolved_as_of = _utc_as_of(as_of)
        resolved_policy = policy or FreshnessPolicy()
        criteria = self._list_criteria(
            query,
            as_of=resolved_as_of,
            policy=resolved_policy,
        )
        total_value = await self.session.scalar(select(func.count(Influencer.id)).where(*criteria))
        total = int(total_value or 0)
        page_rows = (
            await self.session.execute(
                select(Influencer, Operator)
                .outerjoin(Operator, Operator.id == Influencer.owner_operator_id)
                .where(*criteria)
                .order_by(Influencer.created_at.desc(), Influencer.id.desc())
                .offset((query.page - 1) * query.page_size)
                .limit(query.page_size)
            )
        ).all()
        if not page_rows:
            return [], total

        influencer_ids = [row[0].id for row in page_rows]
        accounts = list(
            await self.session.scalars(
                select(InfluencerPlatformAccount)
                .where(
                    InfluencerPlatformAccount.influencer_id.in_(influencer_ids),
                    InfluencerPlatformAccount.is_active.is_(True),
                )
                .order_by(
                    InfluencerPlatformAccount.influencer_id,
                    InfluencerPlatformAccount.platform,
                    InfluencerPlatformAccount.id,
                )
            )
        )
        account_ids = [account.id for account in accounts]
        metrics = (
            list(
                await self.session.scalars(
                    select(InfluencerCurrentMetrics)
                    .where(
                        InfluencerCurrentMetrics.influencer_id.in_(influencer_ids),
                        InfluencerCurrentMetrics.platform_account_id.in_(account_ids),
                    )
                    .order_by(
                        InfluencerCurrentMetrics.influencer_id,
                        InfluencerCurrentMetrics.platform_account_id,
                        InfluencerCurrentMetrics.source,
                        InfluencerCurrentMetrics.id,
                    )
                )
            )
            if account_ids
            else []
        )
        contacts = list(
            await self.session.scalars(
                select(InfluencerContact)
                .where(
                    InfluencerContact.influencer_id.in_(influencer_ids),
                    InfluencerContact.is_current.is_(True),
                )
                .order_by(
                    InfluencerContact.influencer_id,
                    InfluencerContact.type,
                    InfluencerContact.id,
                )
            )
        )
        freshness_records = await self._list_huitun_freshness(influencer_ids)

        accounts_by_influencer: defaultdict[UUID, list[InfluencerPlatformAccount]] = defaultdict(
            list
        )
        metrics_by_influencer: defaultdict[UUID, list[InfluencerCurrentMetrics]] = defaultdict(list)
        contacts_by_influencer: defaultdict[UUID, list[InfluencerContact]] = defaultdict(list)
        freshness_by_influencer: defaultdict[UUID, list[AccountSourceFreshnessRecord]] = (
            defaultdict(list)
        )
        for account in accounts:
            accounts_by_influencer[account.influencer_id].append(account)
        for metric in metrics:
            metrics_by_influencer[metric.influencer_id].append(metric)
        for contact in contacts:
            contacts_by_influencer[contact.influencer_id].append(contact)
        for influencer_id, freshness in freshness_records:
            freshness_by_influencer[influencer_id].append(freshness)

        return [
            InfluencerListRecord(
                influencer=influencer,
                owner=owner,
                platform_accounts=tuple(accounts_by_influencer[influencer.id]),
                current_metrics=tuple(metrics_by_influencer[influencer.id]),
                current_contacts=tuple(contacts_by_influencer[influencer.id]),
                huitun_freshness=tuple(freshness_by_influencer[influencer.id]),
            )
            for influencer, owner in page_rows
        ], total

    async def get_influencer_detail(
        self,
        influencer_id: UUID,
    ) -> InfluencerDetailRecord | None:
        """Return the visible subject and its active-account read graph."""

        row = (
            await self.session.execute(
                select(Influencer, Operator)
                .outerjoin(Operator, Operator.id == Influencer.owner_operator_id)
                .where(Influencer.id == influencer_id, *_visible_influencer_criteria())
            )
        ).first()
        if row is None:
            return None
        influencer, owner = row

        accounts = tuple(
            await self.session.scalars(
                select(InfluencerPlatformAccount)
                .where(
                    InfluencerPlatformAccount.influencer_id == influencer_id,
                    InfluencerPlatformAccount.is_active.is_(True),
                )
                .order_by(InfluencerPlatformAccount.platform, InfluencerPlatformAccount.id)
            )
        )
        account_ids = [account.id for account in accounts]
        contacts = tuple(
            await self.session.scalars(
                select(InfluencerContact)
                .where(
                    InfluencerContact.influencer_id == influencer_id,
                    InfluencerContact.is_current.is_(True),
                )
                .order_by(InfluencerContact.type, InfluencerContact.id)
            )
        )
        if account_ids:
            source_states = tuple(
                await self.session.scalars(
                    select(InfluencerSourceState)
                    .where(
                        InfluencerSourceState.influencer_id == influencer_id,
                        InfluencerSourceState.platform_account_id.in_(account_ids),
                    )
                    .order_by(
                        InfluencerSourceState.platform_account_id,
                        InfluencerSourceState.source,
                        InfluencerSourceState.id,
                    )
                )
            )
            source_identities = tuple(
                await self.session.scalars(
                    select(PlatformAccountSourceIdentity)
                    .where(PlatformAccountSourceIdentity.platform_account_id.in_(account_ids))
                    .order_by(
                        PlatformAccountSourceIdentity.platform_account_id,
                        PlatformAccountSourceIdentity.source,
                        PlatformAccountSourceIdentity.id,
                    )
                )
            )
            current_metrics = tuple(
                await self.session.scalars(
                    select(InfluencerCurrentMetrics)
                    .where(
                        InfluencerCurrentMetrics.influencer_id == influencer_id,
                        InfluencerCurrentMetrics.platform_account_id.in_(account_ids),
                    )
                    .order_by(
                        InfluencerCurrentMetrics.platform_account_id,
                        InfluencerCurrentMetrics.source,
                        InfluencerCurrentMetrics.id,
                    )
                )
            )
        else:
            source_states = ()
            source_identities = ()
            current_metrics = ()
        freshness_records = await self._list_huitun_freshness([influencer_id])

        return InfluencerDetailRecord(
            influencer=influencer,
            owner=owner,
            platform_accounts=accounts,
            contacts=contacts,
            source_states=source_states,
            source_identities=source_identities,
            current_metrics=current_metrics,
            huitun_freshness=tuple(record for _, record in freshness_records),
        )

    async def _list_huitun_freshness(
        self,
        influencer_ids: list[UUID],
    ) -> list[tuple[UUID, AccountSourceFreshnessRecord]]:
        """Batch-load eligible Huitun freshness without per-influencer queries."""

        if not influencer_ids:
            return []
        eligible = self._eligible_huitun_freshness()
        rows = (
            await self.session.execute(
                select(
                    eligible.c.influencer_id,
                    eligible.c.platform_account_id,
                    eligible.c.last_observed_at,
                    eligible.c.last_imported_at,
                )
                .select_from(eligible)
                .where(eligible.c.influencer_id.in_(influencer_ids))
                .order_by(eligible.c.influencer_id, eligible.c.platform_account_id)
            )
        ).all()
        return [
            (
                influencer_id,
                AccountSourceFreshnessRecord(
                    platform_account_id=platform_account_id,
                    source=DataSource.HUITUN,
                    last_observed_at=_utc_database_timestamp(last_observed_at),
                    last_imported_at=_utc_database_timestamp(last_imported_at),
                ),
            )
            for (
                influencer_id,
                platform_account_id,
                last_observed_at,
                last_imported_at,
            ) in rows
        ]

    async def list_metric_snapshots(
        self,
        influencer_id: UUID,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[InfluencerMetricSnapshot], int] | None:
        """Page immutable snapshots, distinguishing hidden subjects from empty pages."""

        visible_id = await self.session.scalar(
            select(Influencer.id).where(
                Influencer.id == influencer_id,
                *_visible_influencer_criteria(),
            )
        )
        if visible_id is None:
            return None
        total_value = await self.session.scalar(
            select(func.count(InfluencerMetricSnapshot.id)).where(
                InfluencerMetricSnapshot.influencer_id == influencer_id
            )
        )
        snapshots = list(
            await self.session.scalars(
                select(InfluencerMetricSnapshot)
                .where(InfluencerMetricSnapshot.influencer_id == influencer_id)
                .order_by(
                    InfluencerMetricSnapshot.captured_at.desc(),
                    InfluencerMetricSnapshot.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return snapshots, int(total_value or 0)

    async def list_filter_option_owners(self) -> list[Operator]:
        """Return only owners referenced by currently visible subjects."""

        referenced_owner_ids = (
            select(Influencer.owner_operator_id)
            .where(
                *_visible_influencer_criteria(),
                Influencer.owner_operator_id.is_not(None),
            )
            .distinct()
            .subquery()
        )
        owners = await self.session.scalars(
            select(Operator)
            .join(
                referenced_owner_ids,
                referenced_owner_ids.c.owner_operator_id == Operator.id,
            )
            .order_by(Operator.name, Operator.id)
        )
        return list(owners)

    async def list_filter_option_tags(self) -> list[str]:
        """Return raw, distinct tags from active accounts of visible subjects."""

        raw_tag_sets = await self.session.scalars(
            select(InfluencerPlatformAccount.source_tags)
            .join(Influencer, Influencer.id == InfluencerPlatformAccount.influencer_id)
            .where(
                *_visible_influencer_criteria(),
                InfluencerPlatformAccount.is_active.is_(True),
            )
        )
        tags: set[str] = set()
        for raw_tags in raw_tag_sets:
            if not isinstance(raw_tags, list):
                continue
            tags.update(tag for tag in raw_tags if isinstance(tag, str))
        return sorted(tags)


__all__ = [
    "AccountSourceFreshnessRecord",
    "InfluencerDetailRecord",
    "InfluencerListRecord",
    "InfluencerRepository",
    "eligible_huitun_freshness",
    "freshness_status_expression",
    "followers_count_expression",
    "notes_60d_expression",
    "huitun_confirmed_lineage",
    "visible_influencer_criteria",
]

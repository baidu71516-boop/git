"""Read-only persistence queries for the company-level influencer library."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Numeric,
    and_,
    bindparam,
    case,
    cast,
    exists,
    func,
    or_,
    select,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from backend_core.auth.models import Operator
from backend_core.influencers.enums import InfluencerStatus
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


def _visible_influencer_criteria() -> tuple[ColumnElement[bool], ...]:
    return (
        Influencer.status == InfluencerStatus.ACTIVE,
        Influencer.deleted_at.is_(None),
    )


def _literal_contains_pattern(value: str) -> str:
    """Escape LIKE metacharacters so user text is always matched literally."""

    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class InfluencerRepository:
    """Explicit, fixed-query-count reads without ORM lazy relationships."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def _dialect_name(self) -> str:
        return self.session.get_bind().dialect.name

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
        if self._dialect_name == "postgresql":
            json_value = cast(InfluencerCurrentMetrics.metrics, JSONB)["followers_count"]
            text_value = json_value.as_string()
            valid_integer = and_(
                func.jsonb_typeof(json_value) == "number",
                text_value.op("~")(r"^(0|[1-9][0-9]*)$"),
            )
            comparable_value = case(
                (valid_integer, cast(text_value, Numeric())),
                else_=None,
            )
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

    def _list_criteria(self, query: InfluencerListQuery) -> list[ColumnElement[bool]]:
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
        return criteria

    async def list_influencers(
        self,
        query: InfluencerListQuery,
    ) -> tuple[list[InfluencerListRecord], int]:
        """Return one aggregate per visible influencer and the exact subject total."""

        criteria = self._list_criteria(query)
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

        accounts_by_influencer: defaultdict[UUID, list[InfluencerPlatformAccount]] = defaultdict(
            list
        )
        metrics_by_influencer: defaultdict[UUID, list[InfluencerCurrentMetrics]] = defaultdict(list)
        contacts_by_influencer: defaultdict[UUID, list[InfluencerContact]] = defaultdict(list)
        for account in accounts:
            accounts_by_influencer[account.influencer_id].append(account)
        for metric in metrics:
            metrics_by_influencer[metric.influencer_id].append(metric)
        for contact in contacts:
            contacts_by_influencer[contact.influencer_id].append(contact)

        return [
            InfluencerListRecord(
                influencer=influencer,
                owner=owner,
                platform_accounts=tuple(accounts_by_influencer[influencer.id]),
                current_metrics=tuple(metrics_by_influencer[influencer.id]),
                current_contacts=tuple(contacts_by_influencer[influencer.id]),
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

        return InfluencerDetailRecord(
            influencer=influencer,
            owner=owner,
            platform_accounts=accounts,
            contacts=contacts,
            source_states=source_states,
            source_identities=source_identities,
            current_metrics=current_metrics,
        )

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
    "InfluencerDetailRecord",
    "InfluencerListRecord",
    "InfluencerRepository",
]

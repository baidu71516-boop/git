"""Read-only Phase 3A Today projection, cursor, and weekday boundary helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import and_, case, cast, exists, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from backend_core.auth.repository import AuthRepository
from backend_core.auth.service import AuthContext
from backend_core.campaigns.access import CampaignOutreachAccess, DepartmentScope
from backend_core.campaigns.errors import CampaignOutreachError
from backend_core.config.settings import Settings, get_settings
from backend_core.growth.models import Campaign, CampaignMember
from backend_core.imports.hashing import canonical_json, hash_document
from backend_core.influencers.enums import (
    ContactFilter,
    ContactType,
    CRMStage,
    DataSource,
    Platform,
)
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerCurrentMetrics,
    InfluencerPlatformAccount,
)
from backend_core.influencers.repository import followers_count_expression
from backend_core.outreach.enums import (
    OutreachChannel,
    OutreachEventType,
    OutreachPriority,
    OutreachTaskKind,
    OutreachTaskState,
)
from backend_core.outreach.models import OutreachEvent, OutreachTarget, OutreachTask

SHANGHAI = ZoneInfo("Asia/Shanghai")
TODAY_CURSOR_VERSION = 1


class TodayWorkKind(StrEnum):
    """Closed Today work-kind filter, including the intentional ALL value."""

    FIRST_TOUCH = "FIRST_TOUCH"
    FOLLOW_UP = "FOLLOW_UP"
    ALL = "ALL"


class TodayQuery(BaseModel):
    """Normalized public Today query excluding route/header scope."""

    # `track` intentionally remains byte-for-byte as supplied: the contract is
    # exact source-tag equality, not a normalized or fuzzy tag search.
    model_config = ConfigDict(extra="forbid")

    work_kind: TodayWorkKind = Field(
        default=TodayWorkKind.ALL,
        description="Task work kind, or ALL for both current Phase 3A kinds.",
    )
    channel: OutreachChannel | None = Field(default=None, description="Outreach target channel.")
    campaign_id: UUID | None = Field(default=None, description="Scoped Campaign identifier.")
    owner_operator_id: UUID | None = Field(
        default=None,
        description=(
            "Filters OutreachTask.assigned_operator_id: the Operator currently assigned to "
            "execute the task, not the Campaign or Influencer owner."
        ),
    )
    track: Annotated[
        str | None,
        Field(
            default=None,
            max_length=160,
            description=(
                "Exact equality against one source_tags value on the Campaign Member preferred "
                "PlatformAccount only."
            ),
        ),
    ] = None
    followers_min: Annotated[
        int | None,
        Field(
            default=None,
            ge=0,
            description=(
                "Inclusive minimum for the preferred account's current HUITUN followers_count."
            ),
        ),
    ] = None
    followers_max: Annotated[
        int | None,
        Field(
            default=None,
            ge=0,
            description=(
                "Inclusive maximum for the preferred account's current HUITUN followers_count."
            ),
        ),
    ] = None
    contact_filter: ContactFilter | None = None
    priority: OutreachPriority | None = None
    cursor: Annotated[str | None, Field(default=None, min_length=1, max_length=4096)] = None
    limit: Annotated[int, Field(default=50, ge=1, le=100)] = 50

    @model_validator(mode="after")
    def validate_follower_bounds(self) -> TodayQuery:
        if (
            self.followers_min is not None
            and self.followers_max is not None
            and self.followers_min > self.followers_max
        ):
            raise ValueError("followers_min must not exceed followers_max")
        return self


class TodayPreferredAccount(BaseModel):
    """Preferred Campaign Member account context used for tags and followers."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    platform: Platform
    account_name: str
    account_handle: str | None
    source_tags: tuple[str, ...] = ()
    followers_count: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Current HUITUN followers_count for this preferred account; null when absent or "
            "invalid, with no fallback source."
        ),
    )


class TodayHistoryWarning(BaseModel):
    """Safe current warning from company Influencer/channel OutreachEvent history.

    ``TASK_CREATED`` metadata may preserve an historical snapshot, but it is
    intentionally not the canonical source for this projection.
    """

    model_config = ConfigDict(extra="forbid")

    channel: OutreachChannel
    last_sent_at: datetime = Field(
        description="Latest OUTREACH_SENT occurred_at from company Influencer/channel history."
    )


class TodayItem(BaseModel):
    """Closed Today row assembled only from the bounded read projection."""

    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    target_id: UUID
    campaign_id: UUID
    campaign_name: str
    member_id: UUID
    influencer_id: UUID
    crm_stage: CRMStage
    preferred_platform_account: TodayPreferredAccount
    assigned_operator_id: UUID | None = Field(
        description="Operator currently assigned to execute this OutreachTask."
    )
    kind: OutreachTaskKind
    state: OutreachTaskState
    channel: OutreachChannel
    priority: OutreachPriority
    due_at: datetime
    version: int = Field(ge=1)
    has_contact: bool
    has_email: bool
    masked_target_display: str = "***"
    history_warning: TodayHistoryWarning | None = None


class TodayPage(BaseModel):
    """Typed Today page. business_date is always local to Asia/Shanghai."""

    model_config = ConfigDict(extra="forbid")

    business_date: date = Field(
        description=(
            "Asia/Shanghai local calendar date of as_of. It is independent of due_at and the "
            "next-business-day eligibility cutoff."
        )
    )
    timezone: Literal["Asia/Shanghai"] = Field(
        default="Asia/Shanghai",
        description="Fixed Phase 3A business timezone.",
    )
    as_of: datetime = Field(description="Timezone-aware instant used for this read.")
    items: tuple[TodayItem, ...]
    next_cursor: str | None


class _CursorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    priority_rank: int
    due_at: datetime
    task_id: UUID
    query_hash: str
    department_id: UUID

    @model_validator(mode="after")
    def require_aware_due_at(self) -> _CursorPayload:
        if self.due_at.tzinfo is None or self.due_at.utcoffset() is None:
            raise ValueError("cursor due_at must be timezone-aware")
        return self


@dataclass(frozen=True, slots=True)
class TodayCursor:
    priority_rank: int
    due_at: datetime
    task_id: UUID
    query_hash: str
    department_id: UUID


@dataclass(frozen=True, slots=True)
class _ProjectedTodayItem:
    item: TodayItem
    priority_rank: int
    due_at: datetime
    task_id: UUID


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CampaignOutreachError(500, "CLOCK_INVALID", "Clock must return a timezone-aware time")
    return value.astimezone(UTC)


def _database_timestamp(value: datetime) -> datetime:
    """Normalize SQLite's offset-less timestamp round-trips without server-local time."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def next_business_day(as_of: datetime) -> datetime:
    """Return the next Asia/Shanghai Monday-Friday boundary at local midnight."""

    local = _as_utc(as_of).astimezone(SHANGHAI)
    days_until_boundary = 1 if local.weekday() < 4 else 7 - local.weekday()
    boundary_date = local.date() + timedelta(days=days_until_boundary)
    return datetime.combine(boundary_date, time.min, tzinfo=SHANGHAI)


class TodayCursorCodec:
    """HMAC cursor codec bound to semantic query values and resolved scope."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._key = self._signing_key()

    def _signing_key(self) -> bytes:
        if self.settings.app_env == "production":
            secret = self.settings.app_master_key
            value = secret.get_secret_value() if secret is not None else ""
            if not value:
                raise ValueError("APP_MASTER_KEY must be non-empty for Today cursor signing")
            return value.encode("utf-8")
        seed = f"{self.settings.app_name}:{self.settings.app_env}:today-cursor:v1"
        return hashlib.sha256(seed.encode("utf-8")).digest()

    @staticmethod
    def query_hash(query: TodayQuery, department_id: UUID) -> str:
        return hash_document(
            {
                "today_query": query.model_dump(mode="json", exclude={"cursor"}),
                "department_id": department_id,
            }
        )

    def encode(
        self,
        *,
        priority_rank: int,
        due_at: datetime,
        task_id: UUID,
        query_hash: str,
        department_id: UUID,
    ) -> str:
        payload = {
            "version": TODAY_CURSOR_VERSION,
            "priority_rank": priority_rank,
            "due_at": _as_utc(due_at),
            "task_id": task_id,
            "query_hash": query_hash,
            "department_id": department_id,
        }
        encoded_payload = canonical_json(payload).encode("utf-8")
        signature = hmac.new(self._key, encoded_payload, hashlib.sha256).digest()
        return f"{self._encode_base64(encoded_payload)}.{self._encode_base64(signature)}"

    def decode(self, token: str) -> TodayCursor:
        try:
            encoded_payload, encoded_signature = token.split(".")
            raw_payload = self._decode_base64(encoded_payload)
            signature = self._decode_base64(encoded_signature)
            expected_signature = hmac.new(self._key, raw_payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected_signature):
                raise ValueError("invalid signature")
            payload = _CursorPayload.model_validate_json(raw_payload)
        except (TypeError, ValueError, ValidationError) as error:
            raise CampaignOutreachError(422, "CURSOR_INVALID", "Cursor is invalid") from error
        return TodayCursor(
            priority_rank=payload.priority_rank,
            due_at=_as_utc(payload.due_at),
            task_id=payload.task_id,
            query_hash=payload.query_hash,
            department_id=payload.department_id,
        )

    @staticmethod
    def _encode_base64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode_base64(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)


class TodayRepository:
    """One bounded statement for the current Today read projection."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_today(
        self,
        *,
        department_id: UUID,
        query: TodayQuery,
        next_boundary: datetime,
        cursor: TodayCursor | None,
    ) -> tuple[_ProjectedTodayItem, ...]:
        dialect_name = self.session.get_bind().dialect.name
        priority_rank = case(
            (OutreachTask.priority == OutreachPriority.HIGH, literal(1)),
            else_=literal(0),
        )
        followers_count = followers_count_expression(dialect_name)
        has_contact = self._has_contact_criterion()
        has_email = self._has_email_criterion()

        statement = (
            select(
                OutreachTask.id.label("task_id"),
                OutreachTarget.id.label("target_id"),
                Campaign.id.label("campaign_id"),
                Campaign.name.label("campaign_name"),
                CampaignMember.id.label("member_id"),
                CampaignMember.influencer_id.label("influencer_id"),
                Influencer.crm_stage.label("crm_stage"),
                InfluencerPlatformAccount.id.label("account_id"),
                InfluencerPlatformAccount.platform.label("account_platform"),
                InfluencerPlatformAccount.account_name.label("account_name"),
                InfluencerPlatformAccount.account_handle.label("account_handle"),
                InfluencerPlatformAccount.source_tags.label("source_tags"),
                followers_count.label("followers_count"),
                OutreachTask.assigned_operator_id.label("assigned_operator_id"),
                OutreachTask.kind.label("kind"),
                OutreachTask.state.label("state"),
                OutreachTarget.channel.label("channel"),
                OutreachTask.priority.label("priority"),
                OutreachTask.due_at.label("due_at"),
                OutreachTask.version.label("version"),
                has_contact.label("has_contact"),
                has_email.label("has_email"),
                priority_rank.label("priority_rank"),
            )
            .select_from(OutreachTask)
            .join(
                OutreachTarget,
                and_(
                    OutreachTarget.id == OutreachTask.outreach_target_id,
                    OutreachTarget.campaign_id == OutreachTask.campaign_id,
                    OutreachTarget.department_id == OutreachTask.department_id,
                ),
            )
            .join(
                CampaignMember,
                and_(
                    CampaignMember.id == OutreachTarget.member_id,
                    CampaignMember.campaign_id == OutreachTarget.campaign_id,
                    CampaignMember.influencer_id == OutreachTarget.influencer_id,
                    CampaignMember.department_id == OutreachTask.department_id,
                ),
            )
            .join(
                Campaign,
                and_(
                    Campaign.id == OutreachTask.campaign_id,
                    Campaign.department_id == OutreachTask.department_id,
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
            .outerjoin(
                InfluencerCurrentMetrics,
                and_(
                    InfluencerCurrentMetrics.platform_account_id == InfluencerPlatformAccount.id,
                    InfluencerCurrentMetrics.influencer_id == CampaignMember.influencer_id,
                    InfluencerCurrentMetrics.source == DataSource.HUITUN,
                ),
            )
            .where(
                OutreachTask.department_id == department_id,
                CampaignMember.removed_at.is_(None),
                OutreachTask.state == OutreachTaskState.READY,
                # Normalize the literal to UTC before binding it. PostgreSQL
                # compares timestamptz instants directly; this additionally
                # keeps SQLite's offset-less test storage independent of the
                # host process timezone.
                OutreachTask.due_at < _as_utc(next_boundary),
            )
        )
        statement = self._apply_filters(
            statement,
            query=query,
            priority_rank=priority_rank,
            followers_count=followers_count,
            has_contact=has_contact,
            has_email=has_email,
            dialect_name=dialect_name,
            cursor=cursor,
        )
        page = (
            statement.order_by(
                priority_rank.desc(),
                OutreachTask.due_at.asc(),
                OutreachTask.id.asc(),
            )
            .limit(query.limit + 1)
            .cte("today_page")
        )
        page_pairs = (
            select(page.c.influencer_id, page.c.channel).distinct().cte("today_history_pairs")
        )
        ranked_history = (
            select(
                OutreachEvent.influencer_id.label("influencer_id"),
                OutreachEvent.channel.label("channel"),
                OutreachEvent.occurred_at.label("last_sent_at"),
                func.row_number()
                .over(
                    partition_by=(OutreachEvent.influencer_id, OutreachEvent.channel),
                    order_by=(OutreachEvent.occurred_at.desc(), OutreachEvent.id.desc()),
                )
                .label("position"),
            )
            .join(
                page_pairs,
                and_(
                    page_pairs.c.influencer_id == OutreachEvent.influencer_id,
                    page_pairs.c.channel == OutreachEvent.channel,
                ),
            )
            .where(OutreachEvent.event_type == OutreachEventType.OUTREACH_SENT)
            .cte("today_ranked_history")
        )
        latest_history = (
            select(
                ranked_history.c.influencer_id,
                ranked_history.c.channel,
                ranked_history.c.last_sent_at,
            )
            .where(ranked_history.c.position == 1)
            .cte("today_latest_history")
        )
        result = await self.session.execute(
            select(page, latest_history.c.last_sent_at)
            .outerjoin(
                latest_history,
                and_(
                    latest_history.c.influencer_id == page.c.influencer_id,
                    latest_history.c.channel == page.c.channel,
                ),
            )
            .order_by(page.c.priority_rank.desc(), page.c.due_at.asc(), page.c.task_id.asc())
        )
        return tuple(self._project_item(row._mapping) for row in result)

    @staticmethod
    def _has_contact_criterion() -> ColumnElement[bool]:
        return exists(
            select(1).where(
                InfluencerContact.influencer_id == CampaignMember.influencer_id,
                InfluencerContact.is_current.is_(True),
            )
        )

    @staticmethod
    def _has_email_criterion() -> ColumnElement[bool]:
        return exists(
            select(1).where(
                InfluencerContact.influencer_id == CampaignMember.influencer_id,
                InfluencerContact.is_current.is_(True),
                InfluencerContact.type == ContactType.EMAIL,
            )
        )

    @staticmethod
    def _track_criterion(value: str, *, dialect_name: str) -> ColumnElement[bool]:
        if dialect_name == "postgresql":
            return func.coalesce(
                cast(InfluencerPlatformAccount.source_tags, JSONB).contains([value]),
                False,
            )
        tag_values = (
            func.json_each(InfluencerPlatformAccount.source_tags)
            .table_valued("value", "type", joins_implicitly=True)
            .alias("today_source_tag")
        )
        # `source_tags` is a JSON document at the storage boundary.  Only a
        # persisted array represents tag elements; objects/scalars must not be
        # treated as an implicit tag collection by SQLite's json_each().
        return and_(
            func.json_type(InfluencerPlatformAccount.source_tags) == "array",
            exists(
                select(1)
                .select_from(tag_values)
                .where(
                    tag_values.c.type == "text",
                    tag_values.c.value == value,
                )
            ),
        )

    def _apply_filters(
        self,
        statement: Any,
        *,
        query: TodayQuery,
        priority_rank: ColumnElement[int],
        followers_count: ColumnElement[Any],
        has_contact: ColumnElement[bool],
        has_email: ColumnElement[bool],
        dialect_name: str,
        cursor: TodayCursor | None,
    ) -> Any:
        if query.work_kind is not TodayWorkKind.ALL:
            statement = statement.where(
                OutreachTask.kind == OutreachTaskKind(query.work_kind.value)
            )
        if query.channel is not None:
            statement = statement.where(OutreachTarget.channel == query.channel)
        if query.campaign_id is not None:
            statement = statement.where(OutreachTask.campaign_id == query.campaign_id)
        if query.owner_operator_id is not None:
            statement = statement.where(
                OutreachTask.assigned_operator_id == query.owner_operator_id
            )
        if query.track is not None:
            statement = statement.where(
                self._track_criterion(query.track, dialect_name=dialect_name)
            )
        if query.followers_min is not None:
            statement = statement.where(followers_count >= query.followers_min)
        if query.followers_max is not None:
            statement = statement.where(followers_count <= query.followers_max)
        if query.contact_filter is ContactFilter.HAS_CONTACT:
            statement = statement.where(has_contact)
        elif query.contact_filter is ContactFilter.HAS_EMAIL:
            statement = statement.where(has_email)
        elif query.contact_filter is ContactFilter.NO_CONTACT:
            statement = statement.where(~has_contact)
        if query.priority is not None:
            statement = statement.where(OutreachTask.priority == query.priority)
        if cursor is not None:
            statement = statement.where(
                or_(
                    priority_rank < cursor.priority_rank,
                    and_(
                        priority_rank == cursor.priority_rank,
                        OutreachTask.due_at > cursor.due_at,
                    ),
                    and_(
                        priority_rank == cursor.priority_rank,
                        OutreachTask.due_at == cursor.due_at,
                        OutreachTask.id > cursor.task_id,
                    ),
                )
            )
        return statement

    @staticmethod
    def _project_item(row: Any) -> _ProjectedTodayItem:
        due_at = _database_timestamp(row["due_at"])
        last_sent_at = row["last_sent_at"]
        source_tags = row["source_tags"]
        tags = (
            tuple(value for value in source_tags if isinstance(value, str))
            if isinstance(source_tags, list)
            else ()
        )
        raw_followers = row["followers_count"]
        try:
            followers_count = (
                int(raw_followers)
                if raw_followers is not None and int(raw_followers) >= 0
                else None
            )
        except (TypeError, ValueError, OverflowError):
            # A malformed historical metric must not make the whole Today
            # projection fail; the frozen contract represents it as null.
            followers_count = None
        warning = (
            TodayHistoryWarning(
                channel=row["channel"],
                last_sent_at=_database_timestamp(last_sent_at),
            )
            if isinstance(last_sent_at, datetime)
            else None
        )
        item = TodayItem(
            task_id=row["task_id"],
            target_id=row["target_id"],
            campaign_id=row["campaign_id"],
            campaign_name=row["campaign_name"],
            member_id=row["member_id"],
            influencer_id=row["influencer_id"],
            crm_stage=row["crm_stage"],
            preferred_platform_account=TodayPreferredAccount(
                id=row["account_id"],
                platform=row["account_platform"],
                account_name=row["account_name"],
                account_handle=row["account_handle"],
                source_tags=tags,
                followers_count=followers_count,
            ),
            assigned_operator_id=row["assigned_operator_id"],
            kind=row["kind"],
            state=row["state"],
            channel=row["channel"],
            priority=row["priority"],
            due_at=due_at,
            version=row["version"],
            has_contact=bool(row["has_contact"]),
            has_email=bool(row["has_email"]),
            history_warning=warning,
        )
        return _ProjectedTodayItem(
            item=item,
            priority_rank=int(row["priority_rank"]),
            due_at=due_at,
            task_id=row["task_id"],
        )


class TodayService:
    """Read-only facade that owns scope, cursor validation, and response assembly."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Any | None = None,
        repository: TodayRepository | None = None,
    ) -> None:
        self.session = session
        self.access = CampaignOutreachAccess(AuthRepository(session))
        self.repository = repository or TodayRepository(session)
        self.cursor_codec = TodayCursorCodec(settings)
        self.clock = clock

    async def list_today(
        self,
        context: AuthContext,
        query: TodayQuery,
        *,
        department_id: UUID | None = None,
    ) -> TodayPage:
        scope = await self.access.resolve_read_scope(context, department_id)
        as_of = self._now()
        query_hash = self.cursor_codec.query_hash(query, scope.department_id)
        cursor = self._validated_cursor(query.cursor, query_hash, scope)
        projected = await self.repository.list_today(
            department_id=scope.department_id,
            query=query,
            next_boundary=next_business_day(as_of),
            cursor=cursor,
        )
        visible = projected[: query.limit]
        next_cursor = (
            self.cursor_codec.encode(
                priority_rank=visible[-1].priority_rank,
                due_at=visible[-1].due_at,
                task_id=visible[-1].task_id,
                query_hash=query_hash,
                department_id=scope.department_id,
            )
            if len(projected) > query.limit and visible
            else None
        )
        return TodayPage(
            business_date=as_of.astimezone(SHANGHAI).date(),
            as_of=as_of,
            items=tuple(item.item for item in visible),
            next_cursor=next_cursor,
        )

    def _now(self) -> datetime:
        now = self.clock.now() if self.clock is not None else datetime.now(UTC)
        return _as_utc(now)

    def _validated_cursor(
        self,
        value: str | None,
        query_hash: str,
        scope: DepartmentScope,
    ) -> TodayCursor | None:
        if value is None:
            return None
        cursor = self.cursor_codec.decode(value)
        if cursor.query_hash != query_hash or cursor.department_id != scope.department_id:
            raise CampaignOutreachError(409, "CURSOR_MISMATCH", "Cursor does not match this query")
        return cursor


__all__ = [
    "SHANGHAI",
    "TODAY_CURSOR_VERSION",
    "TodayCursor",
    "TodayCursorCodec",
    "TodayHistoryWarning",
    "TodayItem",
    "TodayPage",
    "TodayPreferredAccount",
    "TodayQuery",
    "TodayRepository",
    "TodayService",
    "TodayWorkKind",
    "next_business_day",
]

"""Company-level read authorization and DTO assembly for the influencer library."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from backend_core.auth import BusinessAuthorizationContext as AuthContext
from backend_core.auth.enums import Role
from backend_core.auth.models import Operator
from backend_core.content_activity.enums import (
    ContentActivityCoverageStatus,
    ContentActivityObservationStatus,
    ContentActivityResult,
)
from backend_core.content_activity.models import ContentActivityProjection
from backend_core.influencers.enums import CRMStage, DataSource
from backend_core.influencers.freshness import (
    ContentActivityFreshnessPolicy,
    FreshnessEvaluation,
    FreshnessPolicy,
    InfluencerFreshnessSummary,
)
from backend_core.influencers.metrics import followers_count_from_metrics
from backend_core.influencers.models import (
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
from backend_core.influencers.schemas import (
    ContentActivityReadStatus,
    CurrentContactSummary,
    CurrentMetricsDetail,
    CurrentMetricsSummary,
    InfluencerContactDetail,
    InfluencerDetail,
    InfluencerFilterOptions,
    InfluencerListItem,
    InfluencerListPage,
    InfluencerListQuery,
    MetricSnapshotItem,
    MetricSnapshotPage,
    OwnerSummary,
    PlatformAccountDetail,
    PlatformAccountSummary,
    SourceIdentityDetail,
    SourceStateDetail,
)

READ_ROLES = frozenset(
    {
        Role.SUPER_ADMIN,
        Role.MANAGER,
        Role.OPERATOR,
        Role.VIEWER,
    }
)
VIEWER_CONTACT_MASK = "***"


class InfluencerServiceError(Exception):
    """HTTP-independent influencer domain error with a stable public code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class InfluencerNotFoundError(InfluencerServiceError):
    def __init__(self) -> None:
        super().__init__("INFLUENCER_NOT_FOUND", "Influencer not found")


class InfluencerPermissionError(InfluencerServiceError):
    def __init__(self) -> None:
        super().__init__("PERMISSION_DENIED", "Influencer library read permission denied")


class InfluencerReadRepository(Protocol):
    async def list_influencers(
        self,
        query: InfluencerListQuery,
        *,
        as_of: datetime | None = None,
        policy: FreshnessPolicy | None = None,
        content_activity_freshness_policy: ContentActivityFreshnessPolicy | None = None,
    ) -> tuple[list[InfluencerListRecord], int]: ...

    async def get_influencer_detail(self, influencer_id: UUID) -> InfluencerDetailRecord | None: ...

    async def list_metric_snapshots(
        self,
        influencer_id: UUID,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[InfluencerMetricSnapshot], int] | None: ...

    async def list_filter_option_owners(self) -> list[Operator]: ...

    async def list_filter_option_tags(self) -> list[str]: ...


def _owner_summary(owner: Operator | None) -> OwnerSummary | None:
    if owner is None:
        return None
    return OwnerSummary(id=owner.id, name=owner.name, status=owner.status)


def _source_tags(account: InfluencerPlatformAccount) -> list[str]:
    return list(account.source_tags) if account.source_tags is not None else []


def _utc_timestamp(value: datetime | None) -> datetime | None:
    """Normalize database timestamps without treating a local clock as UTC."""

    if value is None:
        return None
    # SQLite test fixtures do not round-trip timezone offsets. Production
    # TIMESTAMPTZ remains aware; the portable test representation is UTC.
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _content_activity_read_state(
    projection: ContentActivityProjection | None,
    *,
    as_of: datetime,
    freshness_policy: ContentActivityFreshnessPolicy,
) -> tuple[ContentActivityReadStatus, int | None]:
    """Return safe display state and transient inactivity days for one account."""

    if projection is None:
        return ContentActivityReadStatus.NOT_CHECKED, None
    trusted_observed_at = _utc_timestamp(projection.trusted_observed_at)
    if (
        projection.trusted_observation_status != ContentActivityObservationStatus.COMPLETE
        or projection.trusted_coverage_status
        != ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET
        or projection.trusted_activity_result
        not in {
            ContentActivityResult.PUBLICATION_FOUND,
            ContentActivityResult.NO_PUBLIC_CONTENT,
        }
        or trusted_observed_at is None
    ):
        return ContentActivityReadStatus.UNKNOWN, None
    if trusted_observed_at > as_of:
        # A list/detail read must not surface a projection that was only
        # observed after the read's captured instant.
        return ContentActivityReadStatus.UNKNOWN, None
    latest_attempt_observed_at = _utc_timestamp(projection.latest_attempt_observed_at)
    if latest_attempt_observed_at is None or latest_attempt_observed_at > as_of:
        # The present projection cannot reconstruct an older point in time, so
        # a caller-supplied earlier as_of must not selectively use one pointer.
        return ContentActivityReadStatus.UNKNOWN, None
    if projection.latest_attempt_observation_id != projection.trusted_observation_id:
        # A later failure/incomplete attempt does not erase the prior trusted
        # fact, but it makes it last-known rather than a present-tense activity
        # claim. Candidate Runs reconstruct immutable as-of history separately.
        return ContentActivityReadStatus.LAST_KNOWN, None
    if not freshness_policy.is_current(trusted_observed_at, as_of):
        return ContentActivityReadStatus.STALE, None
    if projection.trusted_activity_result == ContentActivityResult.NO_PUBLIC_CONTENT:
        return ContentActivityReadStatus.CURRENT, None
    last_publication_at = _utc_timestamp(projection.last_publication_at)
    if last_publication_at is None or last_publication_at > as_of:
        return ContentActivityReadStatus.UNKNOWN, None
    return (
        ContentActivityReadStatus.CURRENT,
        int((as_of - last_publication_at).total_seconds() // 86_400),
    )


def _platform_account_summary(
    account: InfluencerPlatformAccount,
    freshness: AccountSourceFreshnessRecord | None = None,
    evaluation: FreshnessEvaluation | None = None,
    content_activity: ContentActivityProjection | None = None,
    content_activity_freshness_policy: ContentActivityFreshnessPolicy | None = None,
    as_of: datetime | None = None,
) -> PlatformAccountSummary:
    activity_as_of = _utc_timestamp(as_of)
    activity_state, inactive_days = (
        _content_activity_read_state(
            content_activity,
            as_of=activity_as_of,
            freshness_policy=(
                content_activity_freshness_policy or ContentActivityFreshnessPolicy()
            ),
        )
        if activity_as_of is not None
        else (
            (
                ContentActivityReadStatus.NOT_CHECKED
                if content_activity is None
                else ContentActivityReadStatus.UNKNOWN
            ),
            None,
        )
    )
    # A timestamp only supports a sales-facing "last public work" claim when
    # the projection is currently trusted and complete.  Do not leak a raw
    # provider candidate from an incomplete, untrusted, or stale projection.
    safe_last_publication_at = (
        _utc_timestamp(content_activity.last_publication_at)
        if content_activity is not None
        and activity_state is ContentActivityReadStatus.CURRENT
        and content_activity.trusted_activity_result is ContentActivityResult.PUBLICATION_FOUND
        else None
    )
    return PlatformAccountSummary(
        id=account.id,
        platform=account.platform,
        platform_account_id=account.platform_account_id,
        account_name=account.account_name,
        account_handle=account.account_handle,
        profile_url=account.profile_url,
        source=account.source,
        is_active=account.is_active,
        source_tags=_source_tags(account),
        last_huitun_observed_at=(freshness.last_observed_at if freshness is not None else None),
        last_huitun_imported_at=(freshness.last_imported_at if freshness is not None else None),
        freshness_status=(evaluation.status if evaluation is not None else None),
        freshness_age_days=(evaluation.age_days if evaluation is not None else None),
        requires_refresh=(evaluation.requires_refresh if evaluation is not None else False),
        content_activity_state=activity_state,
        content_activity_trusted_observed_at=(
            _utc_timestamp(content_activity.trusted_observed_at)
            if content_activity is not None
            else None
        ),
        content_activity_trusted_observation_status=(
            content_activity.trusted_observation_status if content_activity is not None else None
        ),
        content_activity_trusted_coverage_status=(
            content_activity.trusted_coverage_status if content_activity is not None else None
        ),
        content_activity_trusted_result=(
            content_activity.trusted_activity_result if content_activity is not None else None
        ),
        content_activity_last_publication_at=safe_last_publication_at,
        content_activity_inactive_days=inactive_days,
        content_activity_latest_attempt_observed_at=(
            _utc_timestamp(content_activity.latest_attempt_observed_at)
            if content_activity is not None
            else None
        ),
        content_activity_latest_attempt_observation_status=(
            content_activity.latest_attempt_observation_status
            if content_activity is not None
            else None
        ),
        content_activity_latest_attempt_coverage_status=(
            content_activity.latest_attempt_coverage_status
            if content_activity is not None
            else None
        ),
        content_activity_latest_attempt_result=(
            content_activity.latest_attempt_activity_result
            if content_activity is not None
            else None
        ),
    )


def _platform_account_detail(
    account: InfluencerPlatformAccount,
    freshness: AccountSourceFreshnessRecord | None = None,
    evaluation: FreshnessEvaluation | None = None,
    content_activity: ContentActivityProjection | None = None,
    content_activity_freshness_policy: ContentActivityFreshnessPolicy | None = None,
    as_of: datetime | None = None,
) -> PlatformAccountDetail:
    return PlatformAccountDetail(
        **_platform_account_summary(
            account,
            freshness,
            evaluation,
            content_activity,
            content_activity_freshness_policy,
            as_of,
        ).model_dump(),
        bio=account.bio,
        gender=account.gender,
        region_raw=account.region_raw,
        verification_info=account.verification_info,
        mcn_name=account.mcn_name,
        creator_level=account.creator_level,
        is_brand_partner=account.is_brand_partner,
    )


def _display_contact_value(contact: InfluencerContact, role: Role) -> str:
    if role == Role.VIEWER and contact.value:
        return VIEWER_CONTACT_MASK
    return contact.value


def _current_contact_summary(
    contact: InfluencerContact,
    role: Role,
) -> CurrentContactSummary:
    return CurrentContactSummary(
        id=contact.id,
        type=contact.type,
        display_value=_display_contact_value(contact, role),
        source=contact.source,
        validation_status=contact.validation_status,
        possible_duplicate_contact=contact.possible_duplicate_contact,
    )


def _contact_detail(contact: InfluencerContact, role: Role) -> InfluencerContactDetail:
    return InfluencerContactDetail(
        id=contact.id,
        platform_account_id=contact.platform_account_id,
        type=contact.type,
        display_value=_display_contact_value(contact, role),
        source=contact.source,
        validation_status=contact.validation_status,
        is_current=contact.is_current,
        possible_duplicate_contact=contact.possible_duplicate_contact,
        first_seen_at=contact.first_seen_at,
        last_seen_at=contact.last_seen_at,
        source_updated_at=contact.source_updated_at,
        first_import_job_id=contact.first_import_job_id,
        first_import_row_id=contact.first_import_row_id,
        last_import_job_id=contact.last_import_job_id,
        last_import_row_id=contact.last_import_row_id,
    )


def _followers_count(metric: InfluencerCurrentMetrics) -> int | None:
    return followers_count_from_metrics(metric.metrics)


def _current_metrics_summary(metric: InfluencerCurrentMetrics) -> CurrentMetricsSummary:
    return CurrentMetricsSummary(
        platform_account_id=metric.platform_account_id,
        source=metric.source,
        source_updated_at=metric.source_updated_at,
        followers_count=_followers_count(metric),
    )


def _creator_tags(source_state: InfluencerSourceState) -> list[str]:
    source_data = source_state.source_data
    if not isinstance(source_data, dict):
        return []
    raw_tags = source_data.get("creator_tags")
    if not isinstance(raw_tags, list):
        return []
    return [tag for tag in raw_tags if isinstance(tag, str)]


def _creator_classification_tags(source_state: InfluencerSourceState) -> list[str]:
    source_data = source_state.source_data
    if not isinstance(source_data, dict):
        return []
    raw_tags = source_data.get("creator_classification_tags")
    if not isinstance(raw_tags, list):
        return []
    return [tag for tag in raw_tags if isinstance(tag, str)]


def _source_state_detail(source_state: InfluencerSourceState) -> SourceStateDetail:
    return SourceStateDetail(
        platform_account_id=source_state.platform_account_id,
        source=source_state.source,
        source_updated_at=source_state.source_updated_at,
        state_version=source_state.state_version,
        creator_tags=_creator_tags(source_state),
        creator_classification_tags=_creator_classification_tags(source_state),
        last_import_job_id=source_state.last_import_job_id,
        last_import_row_id=source_state.last_import_row_id,
    )


def _source_identity_detail(
    source_identity: PlatformAccountSourceIdentity,
) -> SourceIdentityDetail:
    return SourceIdentityDetail(
        id=source_identity.id,
        platform_account_id=source_identity.platform_account_id,
        platform=source_identity.platform,
        source=source_identity.source,
        external_account_id=source_identity.external_account_id,
        first_import_job_id=source_identity.first_import_job_id,
        first_import_row_id=source_identity.first_import_row_id,
        last_import_job_id=source_identity.last_import_job_id,
        last_import_row_id=source_identity.last_import_row_id,
    )


def _current_metrics_detail(metric: InfluencerCurrentMetrics) -> CurrentMetricsDetail:
    return CurrentMetricsDetail(
        platform_account_id=metric.platform_account_id,
        source=metric.source,
        source_updated_at=metric.source_updated_at,
        metrics=metric.metrics,
        last_import_job_id=metric.last_import_job_id,
        last_import_row_id=metric.last_import_row_id,
    )


def _snapshot_item(snapshot: InfluencerMetricSnapshot) -> MetricSnapshotItem:
    return MetricSnapshotItem(
        id=snapshot.id,
        platform_account_id=snapshot.platform_account_id,
        source=snapshot.source,
        source_updated_at=snapshot.source_updated_at,
        captured_at=snapshot.captured_at,
        metrics=snapshot.metrics,
        import_job_id=snapshot.import_job_id,
        import_row_id=snapshot.import_row_id,
    )


class InfluencerService:
    """Authorize company-level reads and build the frozen public contracts."""

    def __init__(
        self,
        repository: InfluencerReadRepository,
        *,
        freshness_policy: FreshnessPolicy | None = None,
        content_activity_freshness_policy: ContentActivityFreshnessPolicy | None = None,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.freshness_policy = freshness_policy or FreshnessPolicy()
        self.content_activity_freshness_policy = (
            content_activity_freshness_policy or ContentActivityFreshnessPolicy()
        )
        self._now_factory = now_factory or (lambda: datetime.now(UTC))

    @staticmethod
    def _require_read(context: AuthContext) -> Role:
        role = context.effective_role
        if role not in READ_ROLES:
            raise InfluencerPermissionError
        return role

    async def list_influencers(
        self,
        context: AuthContext,
        query: InfluencerListQuery,
    ) -> InfluencerListPage:
        role = self._require_read(context)
        as_of = self._now_factory()
        records, total = await self.repository.list_influencers(
            query,
            as_of=as_of,
            policy=self.freshness_policy,
            content_activity_freshness_policy=self.content_activity_freshness_policy,
        )
        items = [self._list_item(record, role, as_of) for record in records]
        return InfluencerListPage(
            items=items,
            page=query.page,
            page_size=query.page_size,
            total=total,
        )

    async def get_influencer_detail(
        self,
        context: AuthContext,
        influencer_id: UUID,
    ) -> InfluencerDetail:
        role = self._require_read(context)
        record = await self.repository.get_influencer_detail(influencer_id)
        if record is None:
            raise InfluencerNotFoundError
        influencer = record.influencer
        as_of = self._now_factory()
        freshness, summary = self._evaluate_freshness(record.huitun_freshness, as_of)
        activity_by_account = {
            projection.platform_account_id: projection
            for projection in record.content_activity_projections
        }
        return InfluencerDetail(
            id=influencer.id,
            display_name=influencer.display_name,
            status=influencer.status,
            crm_stage=influencer.crm_stage,
            owner=_owner_summary(record.owner),
            created_at=influencer.created_at,
            updated_at=influencer.updated_at,
            platform_accounts=[
                _platform_account_detail(
                    account,
                    freshness.get(account.id, (None, None))[0],
                    freshness.get(account.id, (None, None))[1],
                    activity_by_account.get(account.id),
                    self.content_activity_freshness_policy,
                    as_of,
                )
                for account in record.platform_accounts
            ],
            contacts=[_contact_detail(contact, role) for contact in record.contacts],
            source_states=[_source_state_detail(state) for state in record.source_states],
            source_identities=[
                _source_identity_detail(identity) for identity in record.source_identities
            ],
            current_metrics=[_current_metrics_detail(metric) for metric in record.current_metrics],
            freshness_status=summary.status,
            requires_refresh=summary.requires_refresh,
        )

    async def list_metric_snapshots(
        self,
        context: AuthContext,
        influencer_id: UUID,
        *,
        page: int,
        page_size: int,
    ) -> MetricSnapshotPage:
        self._require_read(context)
        result = await self.repository.list_metric_snapshots(
            influencer_id,
            page=page,
            page_size=page_size,
        )
        if result is None:
            raise InfluencerNotFoundError
        snapshots, total = result
        return MetricSnapshotPage(
            items=[_snapshot_item(snapshot) for snapshot in snapshots],
            page=page,
            page_size=page_size,
            total=total,
        )

    async def get_filter_options(self, context: AuthContext) -> InfluencerFilterOptions:
        self._require_read(context)
        owners = await self.repository.list_filter_option_owners()
        tags = await self.repository.list_filter_option_tags()
        return InfluencerFilterOptions(
            owners=[_owner_summary(owner) for owner in owners if owner is not None],
            tags=tags,
            crm_stages=list(CRMStage),
        )

    def _evaluate_freshness(
        self,
        records: tuple[AccountSourceFreshnessRecord, ...],
        as_of: datetime,
    ) -> tuple[
        dict[
            UUID,
            tuple[AccountSourceFreshnessRecord | None, FreshnessEvaluation | None],
        ],
        InfluencerFreshnessSummary,
    ]:
        by_account: dict[
            UUID,
            tuple[AccountSourceFreshnessRecord | None, FreshnessEvaluation | None],
        ] = {}
        evaluations: list[FreshnessEvaluation] = []
        for record in records:
            if record.source is not DataSource.HUITUN:
                continue
            evaluation = self.freshness_policy.evaluate(record.last_observed_at, as_of)
            by_account[record.platform_account_id] = (record, evaluation)
            evaluations.append(evaluation)
        return by_account, self.freshness_policy.aggregate(evaluations)

    def _list_item(
        self,
        record: InfluencerListRecord,
        role: Role,
        as_of: datetime,
    ) -> InfluencerListItem:
        influencer = record.influencer
        freshness, summary = self._evaluate_freshness(record.huitun_freshness, as_of)
        activity_by_account = {
            projection.platform_account_id: projection
            for projection in record.content_activity_projections
        }
        return InfluencerListItem(
            id=influencer.id,
            display_name=influencer.display_name,
            status=influencer.status,
            crm_stage=influencer.crm_stage,
            owner=_owner_summary(record.owner),
            platform_accounts=[
                _platform_account_summary(
                    account,
                    freshness.get(account.id, (None, None))[0],
                    freshness.get(account.id, (None, None))[1],
                    activity_by_account.get(account.id),
                    self.content_activity_freshness_policy,
                    as_of,
                )
                for account in record.platform_accounts
            ],
            current_metrics=[_current_metrics_summary(metric) for metric in record.current_metrics],
            current_contacts=[
                _current_contact_summary(contact, role) for contact in record.current_contacts
            ],
            possible_duplicate_contact=any(
                contact.possible_duplicate_contact for contact in record.current_contacts
            ),
            freshness_status=summary.status,
            requires_refresh=summary.requires_refresh,
            created_at=influencer.created_at,
            updated_at=influencer.updated_at,
        )


__all__ = [
    "InfluencerNotFoundError",
    "InfluencerPermissionError",
    "InfluencerService",
    "InfluencerServiceError",
]

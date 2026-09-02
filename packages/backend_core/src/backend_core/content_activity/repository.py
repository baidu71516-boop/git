"""Small persistence helpers for Content Activity domain services."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.content_activity.enums import (
    ContentActivityRefreshRequestState,
    ProviderAccountIdentityNamespace,
    ProviderAccountIdentityVerificationState,
)
from backend_core.content_activity.models import (
    ContentActivityProjection,
    ContentActivityRefreshRequest,
    ProviderAccountIdentity,
)
from backend_core.influencers.models import InfluencerPlatformAccount


class ContentActivityRepository:
    """Locked reads used by identity, observation, and refresh services."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_platform_account(
        self,
        platform_account_id: UUID,
        *,
        for_update: bool = False,
    ) -> InfluencerPlatformAccount | None:
        statement = select(InfluencerPlatformAccount).where(
            InfluencerPlatformAccount.id == platform_account_id
        )
        if for_update:
            statement = statement.execution_options(populate_existing=True).with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_current_identity(
        self,
        *,
        platform_account_id: UUID,
        namespace: ProviderAccountIdentityNamespace,
        for_update: bool = False,
    ) -> ProviderAccountIdentity | None:
        statement = select(ProviderAccountIdentity).where(
            ProviderAccountIdentity.platform_account_id == platform_account_id,
            ProviderAccountIdentity.namespace == namespace,
            ProviderAccountIdentity.verification_state
            == ProviderAccountIdentityVerificationState.VERIFIED_CURRENT,
        )
        if for_update:
            statement = statement.execution_options(populate_existing=True).with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_projection(
        self,
        platform_account_id: UUID,
        *,
        for_update: bool = False,
    ) -> ContentActivityProjection | None:
        statement = select(ContentActivityProjection).where(
            ContentActivityProjection.platform_account_id == platform_account_id
        )
        if for_update:
            statement = statement.execution_options(populate_existing=True).with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_refresh_request(
        self,
        refresh_request_id: UUID,
        *,
        for_update: bool = False,
    ) -> ContentActivityRefreshRequest | None:
        statement = select(ContentActivityRefreshRequest).where(
            ContentActivityRefreshRequest.id == refresh_request_id
        )
        if for_update:
            statement = statement.execution_options(populate_existing=True).with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list_due_refresh_requests(
        self,
        *,
        as_of: datetime,
        limit: int,
        for_update_skip_locked: bool = False,
    ) -> list[ContentActivityRefreshRequest]:
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        # ``RUNNING`` is normally ineligible because its worker owns a lease.
        # Once that lease expires, however, the only durable recovery path is
        # to republish its token so ``claim_refresh_request`` can move it to a
        # bounded retry or terminal failure.  Keep both predicates in this one
        # locked selection: PostgreSQL can use the separate due/expired partial
        # indexes and SKIP LOCKED prevents concurrent reconcilers from emitting
        # the same candidate while one is being inspected.
        statement = (
            select(ContentActivityRefreshRequest)
            .where(
                # A local runtime capture is held by its short-lived capability
                # token, never by the XHS analytics worker/reconciler.
                ContentActivityRefreshRequest.capture_token_digest.is_(None),
                or_(
                    and_(
                        ContentActivityRefreshRequest.state.in_(
                            (
                                ContentActivityRefreshRequestState.PENDING,
                                ContentActivityRefreshRequestState.RETRY_WAIT,
                            )
                        ),
                        ContentActivityRefreshRequest.next_attempt_at <= as_of,
                    ),
                    and_(
                        ContentActivityRefreshRequest.state
                        == ContentActivityRefreshRequestState.RUNNING,
                        ContentActivityRefreshRequest.lease_expires_at <= as_of,
                    ),
                ),
            )
            .order_by(
                ContentActivityRefreshRequest.next_attempt_at.nulls_last(),
                ContentActivityRefreshRequest.lease_expires_at.nulls_last(),
                ContentActivityRefreshRequest.created_at,
                ContentActivityRefreshRequest.id,
            )
            .limit(limit)
        )
        if for_update_skip_locked:
            statement = statement.execution_options(populate_existing=True).with_for_update(
                skip_locked=True
            )
        return list((await self.session.execute(statement)).scalars())

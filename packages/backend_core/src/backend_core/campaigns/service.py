"""Campaign lifecycle and Member operations for the Phase 3A domain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.audit.repository import AuditRepository
from backend_core.auth.enums import OperatorStatus
from backend_core.auth.repository import AuthRepository
from backend_core.auth.service import AuthContext
from backend_core.campaigns.access import CampaignOutreachAccess, DepartmentScope
from backend_core.campaigns.channels import ChannelEnablementRegistry
from backend_core.campaigns.errors import CampaignOutreachError
from backend_core.campaigns.repository import (
    CampaignMemberInsertRow,
    CampaignMemberRestoreRow,
    CampaignOwnerRecord,
    CampaignRepository,
)
from backend_core.campaigns.schemas import (
    CampaignCreateInput,
    CampaignCursor,
    CampaignMemberAddItem,
    CampaignMemberBulkAddInput,
    CampaignMemberBulkAddResult,
    CampaignMemberCursor,
    CampaignMemberFromCandidateRunBulkAddInput,
    CampaignMemberPage,
    CampaignMemberRemoveInput,
    CampaignMemberResult,
    CampaignOwnerSummary,
    CampaignPage,
    CampaignResult,
    CampaignStatusTransitionInput,
    CampaignUpdateInput,
)
from backend_core.growth.enums import (
    CampaignStatus,
    CandidatePoolRunStatus,
    Phase3AOperationScope,
)
from backend_core.growth.idempotency import (
    Phase3AIdempotencyRepository,
    canonical_request_hash,
    validate_idempotency_key,
)
from backend_core.growth.models import Campaign, Phase3AIdempotencyRecord
from backend_core.influencers.enums import ContactType, ContactValidationStatus, Platform
from backend_core.outreach.enums import OutreachChannel


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class MutationResult[ResultT]:
    """An HTTP-independent mutation outcome with its frozen status semantics."""

    status_code: int
    result: ResultT
    replayed: bool = False


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CampaignOutreachError(500, "CLOCK_INVALID", "Clock must return a timezone-aware time")
    return value.astimezone(UTC)


def _safe_campaign_state(campaign: Campaign) -> dict[str, object]:
    return {
        "name": campaign.name,
        "owner_operator_id": str(campaign.owner_operator_id),
        "status": campaign.status.value,
        "review_mode": campaign.review_mode.value,
        "review_count": campaign.review_count,
        "duplicate_history_policy": campaign.duplicate_history_policy.value,
        "duplicate_window_days": campaign.duplicate_window_days,
        "version": campaign.version,
    }


class CampaignService:
    """Own Campaign lifecycle, Member history, Audit, CAS, and A1 replay behavior."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        channel_registry: ChannelEnablementRegistry | None = None,
        clock: Clock | None = None,
        repository: CampaignRepository | None = None,
    ) -> None:
        self.session = session
        self.repository = repository or CampaignRepository(session)
        self.auth_repository = AuthRepository(session)
        self.access = CampaignOutreachAccess(self.auth_repository)
        self.audit = AuditRepository(session)
        self.idempotency = Phase3AIdempotencyRepository(session)
        self.channel_registry = channel_registry or ChannelEnablementRegistry()
        self.clock = clock or SystemClock()

    async def get_campaign(
        self,
        context: AuthContext,
        campaign_id: UUID,
        *,
        department_id: UUID | None = None,
    ) -> CampaignResult:
        scope = await self.access.resolve_read_scope(context, department_id)
        record = await self.repository.get_campaign_with_owner(
            campaign_id,
            department_id=scope.department_id,
        )
        if record is None:
            raise CampaignOutreachError(404, "CAMPAIGN_NOT_FOUND", "Campaign not found")
        return self._campaign_result_from_record(record)

    async def list_campaigns(
        self,
        context: AuthContext,
        *,
        cursor: CampaignCursor | None,
        limit: int,
        department_id: UUID | None = None,
    ) -> CampaignPage:
        """Read a Department-scoped Campaign page in the frozen DESC keyset order."""

        scope = await self.access.resolve_read_scope(context, department_id)
        page = await self.repository.list_campaigns_page(
            department_id=scope.department_id,
            cursor_updated_at=cursor.updated_at if cursor is not None else None,
            cursor_id=cursor.id if cursor is not None else None,
            limit=limit,
        )
        next_cursor: CampaignCursor | None = None
        if page.next_cursor is not None:
            updated_at, next_campaign_id = page.next_cursor
            next_cursor = CampaignCursor(updated_at=updated_at, id=next_campaign_id)
        return CampaignPage(
            items=tuple(self._campaign_result_from_record(item) for item in page.items),
            next_cursor=next_cursor,
        )

    async def get_member(
        self,
        context: AuthContext,
        campaign_id: UUID,
        member_id: UUID,
        *,
        department_id: UUID | None = None,
    ) -> CampaignMemberResult:
        scope = await self.access.resolve_read_scope(context, department_id)
        member = await self.repository.get_member(
            member_id,
            campaign_id=campaign_id,
            department_id=scope.department_id,
        )
        if member is None:
            raise CampaignOutreachError(
                404, "CAMPAIGN_MEMBER_NOT_FOUND", "Campaign member not found"
            )
        return CampaignMemberResult.from_model(member)

    async def list_members(
        self,
        context: AuthContext,
        campaign_id: UUID,
        *,
        cursor: CampaignMemberCursor | None,
        limit: int,
        department_id: UUID | None = None,
    ) -> CampaignMemberPage:
        """Read only active Members, preserving original creation order across restore."""

        scope = await self.access.resolve_read_scope(context, department_id)
        if cursor is not None and (
            cursor.department_id != scope.department_id or cursor.campaign_id != campaign_id
        ):
            raise CampaignOutreachError(
                409,
                "CURSOR_MISMATCH",
                "Campaign member cursor does not match the resolved scope",
            )
        campaign = await self.repository.get_campaign(
            campaign_id,
            department_id=scope.department_id,
        )
        if campaign is None:
            raise CampaignOutreachError(404, "CAMPAIGN_NOT_FOUND", "Campaign not found")
        page = await self.repository.list_active_members_page(
            campaign_id=campaign.id,
            department_id=scope.department_id,
            cursor_created_at=cursor.created_at if cursor is not None else None,
            cursor_id=cursor.id if cursor is not None else None,
            limit=limit,
        )
        next_cursor: CampaignMemberCursor | None = None
        if page.next_cursor is not None:
            created_at, next_member_id = page.next_cursor
            next_cursor = CampaignMemberCursor(
                created_at=created_at,
                id=next_member_id,
                department_id=scope.department_id,
                campaign_id=campaign.id,
            )
        return CampaignMemberPage(
            items=tuple(CampaignMemberResult.from_model(item) for item in page.items),
            next_cursor=next_cursor,
        )

    async def create_campaign(
        self,
        context: AuthContext,
        create_input: CampaignCreateInput,
        *,
        idempotency_key: str,
        ip: str,
        user_agent: str,
    ) -> MutationResult[CampaignResult]:
        scope, actor_id = await self.access.resolve_mutation_scope(
            context,
            create_input.department_id,
        )
        key = self._validated_idempotency_key(idempotency_key)
        canonical_owner_id = self._canonical_create_owner_id(
            scope=scope,
            actor_id=actor_id,
            requested_owner_id=create_input.owner_operator_id,
        )
        request_hash = canonical_request_hash(
            {
                "campaign": {
                    "department_id": scope.department_id,
                    "owner_operator_id": canonical_owner_id,
                    "name": create_input.name,
                    "review_mode": create_input.review_mode,
                    "review_count": create_input.review_count,
                    "duplicate_history_policy": create_input.duplicate_history_policy,
                    "duplicate_window_days": create_input.duplicate_window_days,
                }
            }
        )
        existing = await self.idempotency.get(
            scope.department_id,
            Phase3AOperationScope.CAMPAIGN_CREATE,
            key,
        )
        if existing is not None:
            return await self._replay_campaign(existing, request_hash)

        try:
            owner_id = await self._owner_operator_id(
                scope=scope,
                context=context,
                actor_id=actor_id,
                requested_owner_id=create_input.owner_operator_id,
            )
            created_by_operator_id = await self._target_side_actor_id(
                context=context,
                scope=scope,
                actor_id=actor_id,
                fallback_operator_id=owner_id,
            )
            campaign = Campaign(
                department_id=scope.department_id,
                owner_operator_id=owner_id,
                created_by_operator_id=created_by_operator_id,
                name=create_input.name,
                status=CampaignStatus.DRAFT,
                review_mode=create_input.review_mode,
                review_count=create_input.review_count,
                duplicate_history_policy=create_input.duplicate_history_policy,
                duplicate_window_days=create_input.duplicate_window_days,
                version=1,
            )
            self.session.add(campaign)
            await self.session.flush()
            result = await self._campaign_result(campaign)
            self.idempotency.add(
                department_id=scope.department_id,
                operation_scope=Phase3AOperationScope.CAMPAIGN_CREATE,
                idempotency_key=key,
                request_hash=request_hash,
                result_entity_id=campaign.id,
                result_payload=result.to_replay_payload(),
            )
            self.audit.add(
                action=AuditAction.CAMPAIGN_CREATED,
                result=AuditResult.SUCCESS,
                department_id=scope.department_id,
                operator_id=actor_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="campaign",
                entity_id=campaign.id,
                after={
                    **_safe_campaign_state(campaign),
                    "cross_department_override": scope.cross_department_override,
                },
            )
            await self.session.commit()
            return MutationResult(status_code=201, result=result)
        except IntegrityError as error:
            await self.session.rollback()
            raced = await self.idempotency.get(
                scope.department_id,
                Phase3AOperationScope.CAMPAIGN_CREATE,
                key,
            )
            if raced is not None:
                return await self._replay_campaign(raced, request_hash)
            raise CampaignOutreachError(
                409,
                "CAMPAIGN_CREATE_CONFLICT",
                "Campaign could not be created",
            ) from error
        except BaseException:
            await self.session.rollback()
            raise

    async def update_campaign(
        self,
        context: AuthContext,
        campaign_id: UUID,
        update_input: CampaignUpdateInput,
        *,
        ip: str,
        user_agent: str,
    ) -> CampaignResult:
        scope, actor_id = await self.access.resolve_mutation_scope(
            context,
            update_input.department_id,
        )
        try:
            campaign = await self._locked_campaign(campaign_id, scope)
            self._require_expected_version(
                campaign.version,
                update_input.expected_version,
                entity_id=campaign.id,
            )
            self._require_campaign_open_for_mutation(campaign)
            owner_id = campaign.owner_operator_id
            if update_input.owner_operator_id != campaign.owner_operator_id:
                owner_id = await self._owner_operator_id(
                    scope=scope,
                    context=context,
                    actor_id=actor_id,
                    requested_owner_id=update_input.owner_operator_id,
                )
            before = _safe_campaign_state(campaign)
            changed = (
                campaign.name != update_input.name
                or campaign.owner_operator_id != owner_id
                or campaign.review_mode != update_input.review_mode
                or campaign.review_count != update_input.review_count
                or campaign.duplicate_history_policy != update_input.duplicate_history_policy
                or campaign.duplicate_window_days != update_input.duplicate_window_days
            )
            if not changed:
                result = await self._campaign_result(campaign)
                await self.session.commit()
                return result
            campaign.name = update_input.name
            campaign.owner_operator_id = owner_id
            campaign.review_mode = update_input.review_mode
            campaign.review_count = update_input.review_count
            campaign.duplicate_history_policy = update_input.duplicate_history_policy
            campaign.duplicate_window_days = update_input.duplicate_window_days
            campaign.version += 1
            await self.session.flush()
            await self.session.refresh(campaign)
            result = await self._campaign_result(campaign)
            self.audit.add(
                action=AuditAction.CAMPAIGN_UPDATED,
                result=AuditResult.SUCCESS,
                department_id=scope.department_id,
                operator_id=actor_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="campaign",
                entity_id=campaign.id,
                before=before,
                after={
                    **_safe_campaign_state(campaign),
                    "cross_department_override": scope.cross_department_override,
                },
            )
            await self.session.commit()
            return result
        except BaseException:
            await self.session.rollback()
            raise

    async def transition_campaign_status(
        self,
        context: AuthContext,
        campaign_id: UUID,
        transition_input: CampaignStatusTransitionInput,
        *,
        ip: str,
        user_agent: str,
    ) -> CampaignResult:
        scope, actor_id = await self.access.resolve_mutation_scope(
            context,
            transition_input.department_id,
        )
        try:
            campaign = await self._locked_campaign(campaign_id, scope)
            self._require_expected_version(
                campaign.version,
                transition_input.expected_version,
                entity_id=campaign.id,
            )
            if not self._is_legal_campaign_transition(campaign.status, transition_input.to_status):
                raise CampaignOutreachError(
                    409,
                    "INVALID_CAMPAIGN_TRANSITION",
                    "Campaign status transition is not allowed",
                    current_version=campaign.version,
                    entity_id=campaign.id,
                )
            if transition_input.to_status is CampaignStatus.ACTIVE:
                await self._require_activation_readiness(campaign)
            before = _safe_campaign_state(campaign)
            campaign.status = transition_input.to_status
            campaign.version += 1
            await self.session.flush()
            await self.session.refresh(campaign)
            result = await self._campaign_result(campaign)
            self.audit.add(
                action=AuditAction.CAMPAIGN_UPDATED,
                result=AuditResult.SUCCESS,
                department_id=scope.department_id,
                operator_id=actor_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="campaign",
                entity_id=campaign.id,
                before=before,
                after={
                    **_safe_campaign_state(campaign),
                    "cross_department_override": scope.cross_department_override,
                },
            )
            await self.session.commit()
            return result
        except BaseException:
            await self.session.rollback()
            raise

    async def bulk_add_members(
        self,
        context: AuthContext,
        campaign_id: UUID,
        add_input: CampaignMemberBulkAddInput,
        *,
        idempotency_key: str,
        ip: str,
        user_agent: str,
    ) -> MutationResult[CampaignMemberBulkAddResult]:
        scope, actor_id = await self.access.resolve_mutation_scope(
            context,
            add_input.department_id,
        )
        if add_input.source_pool_run_id is not None:
            raise CampaignOutreachError(
                422,
                "CAMPAIGN_MEMBER_SOURCE_RUN_NOT_ALLOWED",
                "Direct Campaign member add does not accept source_pool_run_id",
            )
        key = self._validated_idempotency_key(idempotency_key)
        members = self._canonical_members(add_input.members)
        request_hash = canonical_request_hash(
            {
                "campaign_id": campaign_id,
                "source_pool_run_id": None,
                "members": [
                    {
                        "influencer_id": member.influencer_id,
                        "preferred_platform_account_id": member.preferred_platform_account_id,
                    }
                    for member in members
                ],
            }
        )
        existing = await self.idempotency.get(
            scope.department_id,
            Phase3AOperationScope.CAMPAIGN_MEMBER_BULK_ADD,
            key,
        )
        if existing is not None:
            return self._replay_bulk_add(existing, request_hash)

        try:
            campaign = await self._locked_campaign(campaign_id, scope)
            self._require_campaign_open_for_mutation(campaign)
            result = await self._apply_member_bulk_add(
                context=context,
                scope=scope,
                actor_id=actor_id,
                campaign=campaign,
                members=members,
                source_pool_run_id=None,
                ip=ip,
                user_agent=user_agent,
            )
            self.idempotency.add(
                department_id=scope.department_id,
                operation_scope=Phase3AOperationScope.CAMPAIGN_MEMBER_BULK_ADD,
                idempotency_key=key,
                request_hash=request_hash,
                result_entity_id=campaign.id,
                result_payload=result.to_replay_payload(),
            )
            await self.session.commit()
            return MutationResult(status_code=200, result=result)
        except IntegrityError as error:
            await self.session.rollback()
            raced = await self.idempotency.get(
                scope.department_id,
                Phase3AOperationScope.CAMPAIGN_MEMBER_BULK_ADD,
                key,
            )
            if raced is not None:
                return self._replay_bulk_add(raced, request_hash)
            raise CampaignOutreachError(
                409,
                "CAMPAIGN_MEMBER_CONFLICT",
                "Campaign member mutation conflicted",
            ) from error
        except BaseException:
            await self.session.rollback()
            raise

    async def bulk_add_members_from_candidate_run(
        self,
        context: AuthContext,
        campaign_id: UUID,
        add_input: CampaignMemberFromCandidateRunBulkAddInput,
        *,
        idempotency_key: str,
        ip: str,
        user_agent: str,
    ) -> MutationResult[CampaignMemberBulkAddResult]:
        """Add explicitly selected persisted MATCH/UNKNOWN rows from one completed run."""

        scope, actor_id = await self.access.resolve_mutation_scope(
            context,
            add_input.department_id,
        )
        key = self._validated_idempotency_key(idempotency_key)
        member_ids = self._canonical_member_ids(add_input.member_ids)
        request_hash = canonical_request_hash(
            {
                "campaign_id": campaign_id,
                "source_pool_run_id": add_input.run_id,
                "member_ids": member_ids,
            }
        )
        existing = await self.idempotency.get(
            scope.department_id,
            Phase3AOperationScope.CAMPAIGN_MEMBER_BULK_ADD,
            key,
        )
        if existing is not None:
            return self._replay_bulk_add(existing, request_hash)

        try:
            campaign = await self._locked_campaign(campaign_id, scope)
            self._require_campaign_open_for_mutation(campaign)
            members = await self._selected_run_members(
                department_id=scope.department_id,
                run_id=add_input.run_id,
                member_ids=member_ids,
            )
            result = await self._apply_member_bulk_add(
                context=context,
                scope=scope,
                actor_id=actor_id,
                campaign=campaign,
                members=members,
                source_pool_run_id=add_input.run_id,
                ip=ip,
                user_agent=user_agent,
            )
            self.idempotency.add(
                department_id=scope.department_id,
                operation_scope=Phase3AOperationScope.CAMPAIGN_MEMBER_BULK_ADD,
                idempotency_key=key,
                request_hash=request_hash,
                result_entity_id=campaign.id,
                result_payload=result.to_replay_payload(),
            )
            await self.session.commit()
            return MutationResult(status_code=200, result=result)
        except IntegrityError as error:
            await self.session.rollback()
            raced = await self.idempotency.get(
                scope.department_id,
                Phase3AOperationScope.CAMPAIGN_MEMBER_BULK_ADD,
                key,
            )
            if raced is not None:
                return self._replay_bulk_add(raced, request_hash)
            raise CampaignOutreachError(
                409,
                "CAMPAIGN_MEMBER_CONFLICT",
                "Campaign member mutation conflicted",
            ) from error
        except BaseException:
            await self.session.rollback()
            raise

    async def remove_member(
        self,
        context: AuthContext,
        campaign_id: UUID,
        member_id: UUID,
        remove_input: CampaignMemberRemoveInput,
        *,
        ip: str,
        user_agent: str,
    ) -> CampaignMemberResult:
        scope, actor_id = await self.access.resolve_mutation_scope(
            context,
            remove_input.department_id,
        )
        try:
            campaign = await self._locked_campaign(campaign_id, scope)
            self._require_campaign_open_for_mutation(campaign)
            member = await self.repository.get_member(
                member_id,
                campaign_id=campaign.id,
                department_id=scope.department_id,
                for_update=True,
            )
            if member is None:
                raise CampaignOutreachError(
                    404,
                    "CAMPAIGN_MEMBER_NOT_FOUND",
                    "Campaign member not found",
                )
            self._require_expected_version(
                member.version,
                remove_input.expected_version,
                entity_id=member.id,
            )
            if member.removed_at is not None:
                raise CampaignOutreachError(
                    409,
                    "CAMPAIGN_MEMBER_ALREADY_REMOVED",
                    "Campaign member is already removed",
                    current_version=member.version,
                    entity_id=member.id,
                )
            member.removed_at = _as_utc(self.clock.now())
            member.version += 1
            await self.session.flush()
            await self.session.refresh(member)
            result = CampaignMemberResult.from_model(member)
            self.audit.add(
                action=AuditAction.CAMPAIGN_MEMBERS_REMOVED,
                result=AuditResult.SUCCESS,
                department_id=scope.department_id,
                operator_id=actor_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="campaign_member",
                entity_id=member.id,
                after={
                    "campaign_id": str(campaign.id),
                    "version": member.version,
                    "removed": True,
                    "cross_department_override": scope.cross_department_override,
                },
            )
            await self.session.commit()
            return result
        except BaseException:
            await self.session.rollback()
            raise

    @staticmethod
    def _canonical_members(
        members: tuple[CampaignMemberAddItem, ...],
    ) -> tuple[CampaignMemberAddItem, ...]:
        """Sort an already unique direct request without silently deduplicating it."""

        if len({member.influencer_id for member in members}) != len(members):
            raise CampaignOutreachError(
                422,
                "CAMPAIGN_MEMBER_DUPLICATE_INFLUENCER",
                "Campaign member request contains duplicate influencer_id values",
            )
        return tuple(sorted(members, key=lambda member: str(member.influencer_id)))

    @staticmethod
    def _canonical_member_ids(member_ids: tuple[UUID, ...]) -> tuple[UUID, ...]:
        """Canonicalize the already distinct selected persistent IDs for replay hashing."""

        if len(set(member_ids)) != len(member_ids):
            raise CampaignOutreachError(
                422,
                "CANDIDATE_POOL_RUN_MEMBER_DUPLICATE",
                "Candidate pool member_ids must be distinct",
            )
        return tuple(sorted(member_ids, key=str))

    @staticmethod
    def _validated_idempotency_key(value: str) -> str:
        try:
            return validate_idempotency_key(value)
        except ValueError as error:
            raise CampaignOutreachError(
                422,
                "IDEMPOTENCY_KEY_INVALID",
                str(error),
            ) from error

    async def _locked_campaign(self, campaign_id: UUID, scope: DepartmentScope) -> Campaign:
        campaign = await self.repository.get_campaign(
            campaign_id,
            department_id=scope.department_id,
            for_update=True,
        )
        if campaign is None:
            raise CampaignOutreachError(404, "CAMPAIGN_NOT_FOUND", "Campaign not found")
        return campaign

    @staticmethod
    def _campaign_result_from_record(record: CampaignOwnerRecord) -> CampaignResult:
        return CampaignResult.from_model(
            record.campaign,
            owner=CampaignOwnerSummary.model_validate(record.owner),
        )

    async def _campaign_result(self, campaign: Campaign) -> CampaignResult:
        """Project one mutation result without allowing a disabled owner to disappear."""

        owner = await self.auth_repository.get_operator(campaign.owner_operator_id)
        if owner is None or owner.department_id != campaign.department_id:
            raise CampaignOutreachError(
                500,
                "CAMPAIGN_OWNER_PROJECTION_INVALID",
                "Campaign owner projection is invalid",
            )
        return CampaignResult.from_model(
            campaign,
            owner=CampaignOwnerSummary.model_validate(owner),
        )

    async def _owner_operator_id(
        self,
        *,
        scope: DepartmentScope,
        context: AuthContext,
        actor_id: UUID,
        requested_owner_id: UUID | None,
    ) -> UUID:
        selected_target_actor = await self.access.target_department_operator_id(
            context=context,
            target_department_id=scope.department_id,
            fallback_operator_id=actor_id,
        )
        owner_id = requested_owner_id or selected_target_actor
        if owner_id is None:
            raise CampaignOutreachError(
                409,
                "TARGET_DEPARTMENT_OWNER_REQUIRED",
                "Cross-department Campaign creation requires a target Department owner",
            )
        owner = await self.auth_repository.get_operator(owner_id)
        if (
            owner is None
            or owner.department_id != scope.department_id
            or owner.status is not OperatorStatus.ACTIVE
        ):
            raise CampaignOutreachError(404, "OPERATOR_NOT_FOUND", "Owner operator not found")
        return owner.id

    @staticmethod
    def _canonical_create_owner_id(
        *,
        scope: DepartmentScope,
        actor_id: UUID,
        requested_owner_id: UUID | None,
    ) -> UUID:
        """Expand the create default without consulting mutable owner state.

        The resulting ID participates in the A1 request hash before a replay
        lookup.  First executions still call `_owner_operator_id` afterward,
        which enforces current Department ownership and active status.
        """

        if requested_owner_id is not None:
            return requested_owner_id
        if scope.cross_department_override:
            raise CampaignOutreachError(
                409,
                "TARGET_DEPARTMENT_OWNER_REQUIRED",
                "Cross-department Campaign creation requires a target Department owner",
            )
        return actor_id

    async def _target_side_actor_id(
        self,
        *,
        context: AuthContext,
        scope: DepartmentScope,
        actor_id: UUID,
        fallback_operator_id: UUID,
    ) -> UUID:
        target_side = await self.access.target_department_operator_id(
            context=context,
            target_department_id=scope.department_id,
            fallback_operator_id=actor_id,
        )
        return target_side or fallback_operator_id

    async def _selected_run_members(
        self,
        *,
        department_id: UUID,
        run_id: UUID,
        member_ids: tuple[UUID, ...],
    ) -> tuple[CampaignMemberAddItem, ...]:
        """Resolve one explicit candidate selection without auto-including UNKNOWN rows."""

        source_run = await self.repository.get_source_pool_run(
            run_id,
            department_id=department_id,
            for_update=True,
        )
        if source_run is None:
            raise CampaignOutreachError(
                404,
                "CANDIDATE_POOL_RUN_NOT_FOUND",
                "Candidate pool run not found",
            )
        if source_run.status is not CandidatePoolRunStatus.COMPLETED:
            raise CampaignOutreachError(
                409,
                "CANDIDATE_POOL_RUN_NOT_COMPLETED",
                "Candidate pool run must be completed before Campaign members are added",
            )
        selected = await self.repository.list_selected_source_run_members(
            source_pool_run_id=source_run.id,
            member_ids=member_ids,
        )
        if len(selected) != len(member_ids):
            raise CampaignOutreachError(
                422,
                "CANDIDATE_POOL_RUN_MEMBER_NOT_FOUND",
                "Every selected Candidate pool member must belong to the selected run",
            )
        if await self.repository.selected_source_run_has_multiple_accounts_per_influencer(
            source_pool_run_id=source_run.id,
            member_ids=member_ids,
        ):
            raise CampaignOutreachError(
                422,
                "CANDIDATE_POOL_RUN_MEMBER_AMBIGUOUS",
                "Selected Candidate pool members cannot choose multiple accounts "
                "for one influencer",
            )
        return tuple(
            CampaignMemberAddItem(
                influencer_id=member.influencer_id,
                preferred_platform_account_id=member.platform_account_id,
            )
            for member in selected
        )

    async def _apply_member_bulk_add(
        self,
        *,
        context: AuthContext,
        scope: DepartmentScope,
        actor_id: UUID,
        campaign: Campaign,
        members: tuple[CampaignMemberAddItem, ...],
        source_pool_run_id: UUID | None,
        ip: str,
        user_agent: str,
    ) -> CampaignMemberBulkAddResult:
        """Apply a bounded, set-prevalidated Member selection within the current transaction."""

        self._require_campaign_open_for_mutation(campaign)
        await self._validate_preferred_accounts(members)
        existing_members = {
            member.influencer_id: member
            for member in await self.repository.list_members_for_influencers(
                campaign.id,
                tuple(member.influencer_id for member in members),
                for_update=True,
            )
        }
        target_side_actor_id = await self._target_side_actor_id(
            context=context,
            scope=scope,
            actor_id=actor_id,
            fallback_operator_id=campaign.owner_operator_id,
        )
        added_count = 0
        restored_count = 0
        already_active_count = 0
        new_members: list[CampaignMemberInsertRow] = []
        restored_members: list[CampaignMemberRestoreRow] = []
        for requested in members:
            member = existing_members.get(requested.influencer_id)
            if member is None:
                new_members.append(
                    CampaignMemberInsertRow(
                        id=uuid4(),
                        department_id=scope.department_id,
                        campaign_id=campaign.id,
                        influencer_id=requested.influencer_id,
                        preferred_platform_account_id=requested.preferred_platform_account_id,
                        source_pool_run_id=source_pool_run_id,
                        added_by_operator_id=target_side_actor_id,
                    )
                )
                added_count += 1
                continue
            if member.removed_at is None:
                already_active_count += 1
                continue
            restored_members.append(
                CampaignMemberRestoreRow(
                    member_id=member.id,
                    expected_version=member.version,
                    preferred_platform_account_id=requested.preferred_platform_account_id,
                    source_pool_run_id=source_pool_run_id,
                    added_by_operator_id=target_side_actor_id,
                )
            )
            restored_count += 1
        await self.repository.insert_campaign_members(new_members)
        restored_rows = await self.repository.restore_campaign_members(
            campaign.id,
            restored_members,
        )
        if restored_rows != restored_count:
            raise CampaignOutreachError(
                409,
                "CAMPAIGN_MEMBER_CONFLICT",
                "Campaign member mutation conflicted",
            )
        active_count_after = await self.repository.count_active_members(campaign.id)
        result = CampaignMemberBulkAddResult(
            campaign_id=campaign.id,
            source_pool_run_id=source_pool_run_id,
            requested_count=len(members),
            added_count=added_count,
            restored_count=restored_count,
            already_active_count=already_active_count,
            active_count_after=active_count_after,
        )
        if added_count or restored_count:
            self.audit.add(
                action=AuditAction.CAMPAIGN_MEMBERS_ADDED,
                result=AuditResult.SUCCESS,
                department_id=scope.department_id,
                operator_id=actor_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="campaign",
                entity_id=campaign.id,
                after={
                    "added_count": added_count,
                    "restored_count": restored_count,
                    "already_active_count": already_active_count,
                    "active_count_after": active_count_after,
                    "cross_department_override": scope.cross_department_override,
                },
            )
        return result

    async def _validate_preferred_accounts(
        self,
        members: tuple[CampaignMemberAddItem, ...],
    ) -> None:
        accounts = {
            account.id: account
            for account in await self.repository.get_platform_accounts(
                tuple(member.preferred_platform_account_id for member in members)
            )
        }
        for member in members:
            account = accounts.get(member.preferred_platform_account_id)
            if account is None or account.influencer_id != member.influencer_id:
                raise CampaignOutreachError(
                    422,
                    "PREFERRED_PLATFORM_ACCOUNT_INVALID",
                    "Preferred platform account must belong to the Campaign member influencer",
                )

    async def _require_activation_readiness(self, campaign: Campaign) -> None:
        if await self.repository.count_active_members(campaign.id) < 1:
            raise CampaignOutreachError(
                409,
                "CAMPAIGN_ACTIVATION_REQUIRES_MEMBER",
                "Campaign activation requires an active member",
            )
        targets = await self.repository.list_targets_for_campaign(
            campaign.id,
            active_members_only=True,
        )
        for target in targets:
            if not self.channel_registry.is_enabled(target.channel):
                continue
            if await self._is_valid_target_reference(target):
                return
        raise CampaignOutreachError(
            409,
            "CAMPAIGN_ACTIVATION_REQUIRES_TARGET",
            "Campaign activation requires a valid enabled-channel target",
        )

    async def _is_valid_target_reference(self, target: object) -> bool:
        channel = getattr(target, "channel", None)
        influencer_id = getattr(target, "influencer_id", None)
        contact_id = getattr(target, "contact_id", None)
        account_id = getattr(target, "platform_account_id", None)
        if not isinstance(channel, OutreachChannel) or not isinstance(influencer_id, UUID):
            return False
        if contact_id is not None:
            contact = await self.repository.get_contact(contact_id)
            if (
                contact is None
                or contact.influencer_id != influencer_id
                or not contact.is_current
                or contact.validation_status is ContactValidationStatus.INVALID
            ):
                return False
            if channel is OutreachChannel.EMAIL:
                return (
                    contact.type is ContactType.EMAIL
                    and contact.validation_status is ContactValidationStatus.VALID
                )
            if channel is OutreachChannel.WECHAT:
                return contact.type is ContactType.WECHAT
            return channel is OutreachChannel.MANUAL
        if account_id is not None:
            account = await self.repository.get_platform_account(account_id)
            if account is None or account.influencer_id != influencer_id or not account.is_active:
                return False
            if channel is OutreachChannel.XIAOHONGSHU_PRIVATE_MESSAGE:
                return account.platform is Platform.XIAOHONGSHU
            return channel is OutreachChannel.MANUAL
        return False

    @staticmethod
    def _require_campaign_open_for_mutation(campaign: Campaign) -> None:
        if campaign.status is CampaignStatus.CLOSED:
            raise CampaignOutreachError(
                409,
                "CAMPAIGN_CLOSED",
                "Closed Campaigns are terminal",
                current_version=campaign.version,
                entity_id=campaign.id,
            )

    @staticmethod
    def _require_expected_version(
        current_version: int,
        expected_version: int,
        *,
        entity_id: UUID,
    ) -> None:
        if current_version != expected_version:
            raise CampaignOutreachError(
                409,
                "VERSION_CONFLICT",
                "Resource version is stale",
                current_version=current_version,
                entity_id=entity_id,
            )

    @staticmethod
    def _is_legal_campaign_transition(
        current: CampaignStatus,
        target: CampaignStatus,
    ) -> bool:
        return (
            target
            in {
                CampaignStatus.DRAFT: {CampaignStatus.ACTIVE},
                CampaignStatus.ACTIVE: {CampaignStatus.PAUSED},
                CampaignStatus.PAUSED: {CampaignStatus.ACTIVE, CampaignStatus.CLOSED},
                CampaignStatus.CLOSED: set(),
            }[current]
        )

    async def _replay_campaign(
        self,
        record: Phase3AIdempotencyRecord,
        request_hash: str,
    ) -> MutationResult[CampaignResult]:
        self._require_matching_hash(record, request_hash)
        if record.result_schema_version != 1:
            raise CampaignOutreachError(
                500,
                "IDEMPOTENCY_RESULT_UNSUPPORTED",
                "Campaign replay result schema is unsupported",
            )
        raw_owner_id = record.result_payload.get("owner_operator_id")
        try:
            owner_id = UUID(str(raw_owner_id))
        except (TypeError, ValueError) as error:
            raise CampaignOutreachError(
                500,
                "IDEMPOTENCY_RESULT_INVALID",
                "Campaign replay result has an invalid owner",
            ) from error
        owner = await self.auth_repository.get_operator(owner_id)
        if owner is None or owner.department_id != record.department_id:
            raise CampaignOutreachError(
                500,
                "IDEMPOTENCY_RESULT_INVALID",
                "Campaign replay result owner is invalid",
            )
        result = CampaignResult.from_replay_payload(
            record.result_payload,
            owner=CampaignOwnerSummary.model_validate(owner),
        )
        if result.id != record.result_entity_id:
            raise CampaignOutreachError(
                500,
                "IDEMPOTENCY_RESULT_INVALID",
                "Campaign replay result does not match its stored entity",
            )
        return MutationResult(status_code=201, result=result, replayed=True)

    @staticmethod
    def _replay_bulk_add(
        record: Phase3AIdempotencyRecord,
        request_hash: str,
    ) -> MutationResult[CampaignMemberBulkAddResult]:
        CampaignService._require_matching_hash(record, request_hash)
        if record.result_schema_version != 1:
            raise CampaignOutreachError(
                500,
                "IDEMPOTENCY_RESULT_UNSUPPORTED",
                "Campaign member replay result schema is unsupported",
            )
        result = CampaignMemberBulkAddResult.from_replay_payload(record.result_payload)
        if result.campaign_id != record.result_entity_id:
            raise CampaignOutreachError(
                500,
                "IDEMPOTENCY_RESULT_INVALID",
                "Campaign member replay result does not match its stored Campaign",
            )
        return MutationResult(status_code=200, result=result, replayed=True)

    @staticmethod
    def _require_matching_hash(record: Phase3AIdempotencyRecord, request_hash: str) -> None:
        if record.request_hash != request_hash:
            raise CampaignOutreachError(
                409,
                "IDEMPOTENCY_KEY_REUSED",
                "Idempotency key was already used for a different request",
            )

"""Outreach Target, Task, and Event services for the frozen Phase 3A domain."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

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
from backend_core.campaigns.repository import CampaignRepository
from backend_core.campaigns.service import Clock, MutationResult, SystemClock, _as_utc
from backend_core.growth.enums import (
    CampaignReviewMode,
    CampaignStatus,
    DuplicateHistoryPolicy,
    Phase3AOperationScope,
)
from backend_core.growth.idempotency import (
    Phase3AIdempotencyRepository,
    canonical_request_hash,
    validate_idempotency_key,
)
from backend_core.growth.models import Campaign, CampaignMember, Phase3AIdempotencyRecord
from backend_core.influencers.enums import (
    ContactType,
    ContactValidationStatus,
    Platform,
)
from backend_core.influencers.models import InfluencerContact, InfluencerPlatformAccount
from backend_core.outreach.enums import (
    MessageTemplateState,
    OutreachActorType,
    OutreachChannel,
    OutreachEventType,
    OutreachPriority,
    OutreachPrioritySource,
    OutreachTaskState,
)
from backend_core.outreach.models import OutreachEvent, OutreachTarget, OutreachTask
from backend_core.outreach.schemas import (
    HistoryWarning,
    OutreachEventResult,
    OutreachTargetCreateInput,
    OutreachTargetResult,
    OutreachTargetUpdateInput,
    OutreachTaskCreateInput,
    OutreachTaskCreateResult,
    OutreachTaskResult,
    OutreachTaskTransitionInput,
    OutreachTransitionResult,
)

SAMPLE_REVIEW_PERCENT = 10


def _database_as_utc(value: datetime) -> datetime:
    """Return a database timestamp as UTC, including SQLite's offset-less values."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class EndpointFacts:
    requires_review: bool
    sent_snapshot: dict[str, object]


class OutreachService:
    """Own frozen Target/Task/Event semantics without HTTP, providers, or UI."""

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

    async def get_target(
        self,
        context: AuthContext,
        target_id: UUID,
        *,
        department_id: UUID | None = None,
    ) -> OutreachTargetResult:
        scope = await self.access.resolve_read_scope(context, department_id)
        target = await self.repository.get_target(target_id, department_id=scope.department_id)
        if target is None:
            raise CampaignOutreachError(
                404, "OUTREACH_TARGET_NOT_FOUND", "Outreach target not found"
            )
        return self._target_result(target)

    async def get_task(
        self,
        context: AuthContext,
        task_id: UUID,
        *,
        department_id: UUID | None = None,
    ) -> OutreachTaskResult:
        scope = await self.access.resolve_read_scope(context, department_id)
        task = await self.repository.get_task(task_id, department_id=scope.department_id)
        if task is None:
            raise CampaignOutreachError(404, "OUTREACH_TASK_NOT_FOUND", "Outreach task not found")
        return OutreachTaskResult.from_model(task)

    async def list_task_events(
        self,
        context: AuthContext,
        task_id: UUID,
        *,
        department_id: UUID | None = None,
    ) -> list[OutreachEventResult]:
        scope = await self.access.resolve_read_scope(context, department_id)
        task = await self.repository.get_task(task_id, department_id=scope.department_id)
        if task is None:
            raise CampaignOutreachError(404, "OUTREACH_TASK_NOT_FOUND", "Outreach task not found")
        return [
            OutreachEventResult.model_validate(event)
            for event in await self.repository.list_task_events(task.id)
        ]

    async def create_target(
        self,
        context: AuthContext,
        campaign_id: UUID,
        create_input: OutreachTargetCreateInput,
        *,
        idempotency_key: str,
        ip: str,
        user_agent: str,
    ) -> MutationResult[OutreachTargetResult]:
        scope, actor_id = await self.access.resolve_mutation_scope(
            context,
            create_input.department_id,
        )
        key = self._validated_idempotency_key(idempotency_key)
        request_hash = canonical_request_hash(
            {
                "campaign_id": campaign_id,
                "member_id": create_input.member_id,
                "influencer_id": create_input.influencer_id,
                "channel": create_input.channel,
                "contact_id": create_input.contact_id,
                "platform_account_id": create_input.platform_account_id,
            }
        )
        existing = await self.idempotency.get(
            scope.department_id,
            Phase3AOperationScope.OUTREACH_TARGET_CREATE,
            key,
        )
        if existing is not None:
            return self._replay_target(existing, request_hash)

        try:
            campaign = await self._locked_campaign(campaign_id, scope)
            raced = await self.idempotency.get(
                scope.department_id,
                Phase3AOperationScope.OUTREACH_TARGET_CREATE,
                key,
            )
            if raced is not None:
                # A concurrent request can miss the initial lookup and wait on
                # this Campaign lock. Release the lock before returning the
                # durable replay (or its deterministic hash conflict).
                await self.session.commit()
                return self._replay_target(raced, request_hash)
            self._require_campaign_open_for_mutation(campaign)
            member = await self._locked_active_member(
                campaign=campaign,
                member_id=create_input.member_id,
                influencer_id=create_input.influencer_id,
                scope=scope,
            )
            self._require_channel_enabled(create_input.channel)
            await self._validate_endpoint(
                channel=create_input.channel,
                influencer_id=member.influencer_id,
                contact_id=create_input.contact_id,
                platform_account_id=create_input.platform_account_id,
            )
            duplicate = await self.repository.get_target_by_campaign_member_channel(
                campaign_id=campaign.id,
                member_id=member.id,
                channel=create_input.channel,
            )
            if duplicate is not None:
                raise CampaignOutreachError(
                    409,
                    "OUTREACH_TARGET_ALREADY_EXISTS",
                    "Campaign member already has an Outreach target for this channel",
                    current_version=duplicate.version,
                    entity_id=duplicate.id,
                )
            target = OutreachTarget(
                department_id=scope.department_id,
                campaign_id=campaign.id,
                member_id=member.id,
                influencer_id=member.influencer_id,
                channel=create_input.channel,
                contact_id=create_input.contact_id,
                platform_account_id=create_input.platform_account_id,
                version=1,
            )
            self.session.add(target)
            await self.session.flush()
            result = self._target_result(target)
            self.idempotency.add(
                department_id=scope.department_id,
                operation_scope=Phase3AOperationScope.OUTREACH_TARGET_CREATE,
                idempotency_key=key,
                request_hash=request_hash,
                result_entity_id=target.id,
                result_payload=result.to_replay_payload(),
            )
            self.audit.add(
                action=AuditAction.OUTREACH_TARGET_CREATED,
                result=AuditResult.SUCCESS,
                department_id=scope.department_id,
                operator_id=actor_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="outreach_target",
                entity_id=target.id,
                after={
                    "campaign_id": str(campaign.id),
                    "member_id": str(member.id),
                    "channel": target.channel.value,
                    "version": target.version,
                    "cross_department_override": scope.cross_department_override,
                },
            )
            await self.session.commit()
            return MutationResult(status_code=201, result=result)
        except IntegrityError as error:
            await self.session.rollback()
            raced = await self.idempotency.get(
                scope.department_id,
                Phase3AOperationScope.OUTREACH_TARGET_CREATE,
                key,
            )
            if raced is not None:
                return self._replay_target(raced, request_hash)
            raise CampaignOutreachError(
                409,
                "OUTREACH_TARGET_CONFLICT",
                "Outreach target mutation conflicted",
            ) from error
        except BaseException:
            await self.session.rollback()
            raise

    async def update_target(
        self,
        context: AuthContext,
        target_id: UUID,
        update_input: OutreachTargetUpdateInput,
        *,
        ip: str,
        user_agent: str,
    ) -> OutreachTargetResult:
        scope, actor_id = await self.access.resolve_mutation_scope(
            context,
            update_input.department_id,
        )
        try:
            target = await self._locked_target(target_id, scope)
            self._require_expected_version(
                target.version,
                update_input.expected_version,
                entity_id=target.id,
            )
            campaign = await self._locked_campaign(target.campaign_id, scope)
            self._require_campaign_open_for_mutation(campaign)
            self._require_channel_enabled(target.channel)
            await self._validate_endpoint(
                channel=target.channel,
                influencer_id=target.influencer_id,
                contact_id=update_input.contact_id,
                platform_account_id=update_input.platform_account_id,
            )
            if (
                target.contact_id == update_input.contact_id
                and target.platform_account_id == update_input.platform_account_id
            ):
                result = self._target_result(target)
                await self.session.commit()
                return result
            before = self._safe_target_state(target)
            target.contact_id = update_input.contact_id
            target.platform_account_id = update_input.platform_account_id
            target.version += 1
            await self.session.flush()
            result = self._target_result(target)
            self.audit.add(
                action=AuditAction.OUTREACH_TARGET_UPDATED,
                result=AuditResult.SUCCESS,
                department_id=scope.department_id,
                operator_id=actor_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="outreach_target",
                entity_id=target.id,
                before=before,
                after={
                    **self._safe_target_state(target),
                    "cross_department_override": scope.cross_department_override,
                },
            )
            await self.session.commit()
            return result
        except BaseException:
            await self.session.rollback()
            raise

    async def create_task(
        self,
        context: AuthContext,
        target_id: UUID,
        create_input: OutreachTaskCreateInput,
        *,
        idempotency_key: str,
        ip: str,
        user_agent: str,
    ) -> MutationResult[OutreachTaskCreateResult]:
        scope, actor_id = await self.access.resolve_mutation_scope(
            context,
            create_input.department_id,
        )
        key = self._validated_idempotency_key(idempotency_key)
        request_hash = canonical_request_hash(
            {
                "outreach_target_id": target_id,
                "kind": create_input.kind,
                "step_key": create_input.step_key,
                "priority": create_input.priority,
                "priority_reason_codes": create_input.priority_reason_codes,
                "assigned_operator_id": create_input.assigned_operator_id,
                "due_at": create_input.due_at,
                "template_version_id": create_input.template_version_id,
            }
        )
        existing = await self.repository.get_task_by_create_idempotency_key(
            department_id=scope.department_id,
            idempotency_key=key,
        )
        if existing is not None:
            return await self._replay_task_create(existing, request_hash)

        try:
            target = await self._locked_target(target_id, scope)
            campaign = await self._locked_campaign(target.campaign_id, scope)
            self._require_campaign_open_for_mutation(campaign)
            member = await self.repository.get_member(
                target.member_id,
                campaign_id=campaign.id,
                department_id=scope.department_id,
                for_update=True,
            )
            if member is None or member.removed_at is not None:
                raise CampaignOutreachError(
                    409,
                    "CAMPAIGN_MEMBER_INACTIVE",
                    "Outreach task requires an active Campaign member",
                )
            self._require_channel_enabled(target.channel)
            endpoint = await self._validate_endpoint(
                channel=target.channel,
                influencer_id=target.influencer_id,
                contact_id=target.contact_id,
                platform_account_id=target.platform_account_id,
            )
            await self._validate_task_assignment(
                scope.department_id, create_input.assigned_operator_id
            )
            await self._validate_template_reference(
                department_id=scope.department_id,
                channel=target.channel,
                template_version_id=create_input.template_version_id,
            )
            now = _as_utc(self.clock.now())
            state, history_warning = await self._initial_task_state(
                campaign=campaign,
                member=member,
                target=target,
                endpoint=endpoint,
                now=now,
            )
            priority_source = (
                OutreachPrioritySource.MANUAL
                if create_input.priority is OutreachPriority.HIGH
                else OutreachPrioritySource.DEFAULT
            )
            task = OutreachTask(
                department_id=scope.department_id,
                campaign_id=campaign.id,
                outreach_target_id=target.id,
                kind=create_input.kind,
                step_key=create_input.step_key,
                state=state,
                priority=create_input.priority,
                priority_source=priority_source,
                priority_reason_codes=list(create_input.priority_reason_codes) or None,
                assigned_operator_id=create_input.assigned_operator_id,
                due_at=_as_utc(create_input.due_at),
                template_version_id=create_input.template_version_id,
                version=1,
                create_idempotency_key=key,
                create_request_hash=request_hash,
            )
            self.session.add(task)
            await self.session.flush()
            self._add_event(
                task=task,
                target=target,
                event_type=OutreachEventType.TASK_CREATED,
                actor_id=actor_id,
                occurred_at=now,
                from_state=None,
                to_state=state,
                reason_code=None,
                idempotency_key=None,
                request_hash=None,
                target_snapshot=None,
                event_metadata={
                    "task_version": task.version,
                    "history_warning": (
                        history_warning.model_dump(mode="json")
                        if history_warning is not None
                        else None
                    ),
                },
            )
            result = OutreachTaskCreateResult(
                task=OutreachTaskResult.from_model(task),
                history_warning=history_warning,
            )
            self.audit.add(
                action=AuditAction.OUTREACH_TASK_CREATED,
                result=AuditResult.SUCCESS,
                department_id=scope.department_id,
                operator_id=actor_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="outreach_task",
                entity_id=task.id,
                after={
                    "campaign_id": str(campaign.id),
                    "outreach_target_id": str(target.id),
                    "state": task.state.value,
                    "priority": task.priority.value,
                    "priority_source": task.priority_source.value,
                    "version": task.version,
                    "cross_department_override": scope.cross_department_override,
                },
            )
            await self.session.commit()
            return MutationResult(status_code=201, result=result)
        except IntegrityError as error:
            await self.session.rollback()
            raced = await self.repository.get_task_by_create_idempotency_key(
                department_id=scope.department_id,
                idempotency_key=key,
            )
            if raced is not None:
                return await self._replay_task_create(raced, request_hash)
            duplicate = await self.repository.get_task_by_target_step(
                target_id=target_id,
                step_key=create_input.step_key,
            )
            if duplicate is not None:
                raise CampaignOutreachError(
                    409,
                    "OUTREACH_TASK_STEP_ALREADY_EXISTS",
                    "Outreach target already has this logical Task step",
                    current_version=duplicate.version,
                    entity_id=duplicate.id,
                ) from error
            raise CampaignOutreachError(
                409,
                "OUTREACH_TASK_CONFLICT",
                "Outreach task mutation conflicted",
            ) from error
        except BaseException:
            await self.session.rollback()
            raise

    async def transition_task(
        self,
        context: AuthContext,
        task_id: UUID,
        transition_input: OutreachTaskTransitionInput,
        *,
        idempotency_key: str,
        ip: str,
        user_agent: str,
    ) -> OutreachTransitionResult:
        scope, actor_id = await self.access.resolve_mutation_scope(
            context,
            transition_input.department_id,
        )
        key = self._validated_idempotency_key(idempotency_key)
        request_hash = canonical_request_hash(
            {
                "outreach_task_id": task_id,
                "to_state": transition_input.to_state,
                "expected_version": transition_input.expected_version,
                "reason_code": transition_input.reason_code,
            }
        )
        try:
            task = await self._locked_task(task_id, scope)
            existing_event = await self.repository.get_event_by_idempotency_key(
                task_id=task.id,
                idempotency_key=key,
            )
            if existing_event is not None:
                await self.session.commit()
                return self._replay_transition(existing_event, request_hash)
            self._require_expected_version(
                task.version,
                transition_input.expected_version,
                entity_id=task.id,
            )
            if not self._is_legal_task_transition(task.state, transition_input.to_state):
                raise CampaignOutreachError(
                    409,
                    "INVALID_OUTREACH_TASK_TRANSITION",
                    "Outreach task state transition is not allowed",
                    current_version=task.version,
                    entity_id=task.id,
                )
            target = await self._locked_target(task.outreach_target_id, scope)
            campaign = await self._locked_campaign(task.campaign_id, scope)
            if transition_input.to_state is OutreachTaskState.READY:
                # Targets are live references until execution. Do not promote a
                # Task when its canonical endpoint has become invalid.
                await self._locked_active_member(
                    campaign=campaign,
                    member_id=target.member_id,
                    influencer_id=target.influencer_id,
                    scope=scope,
                )
                await self._validate_endpoint(
                    channel=target.channel,
                    influencer_id=target.influencer_id,
                    contact_id=target.contact_id,
                    platform_account_id=target.platform_account_id,
                )
            if transition_input.to_state is OutreachTaskState.SENT:
                if task.template_version_id is not None:
                    raise CampaignOutreachError(
                        409,
                        "TEMPLATE_RENDERING_UNAVAILABLE",
                        "Phase 3A cannot execute a Task with a pinned template version",
                    )
                await self._locked_active_member(
                    campaign=campaign,
                    member_id=target.member_id,
                    influencer_id=target.influencer_id,
                    scope=scope,
                )
                if campaign.status is not CampaignStatus.ACTIVE:
                    raise CampaignOutreachError(
                        409,
                        "CAMPAIGN_NOT_EXECUTABLE",
                        "Only active Campaigns can execute Outreach tasks",
                    )
                self._require_channel_enabled(target.channel)
                endpoint = await self._validate_endpoint(
                    channel=target.channel,
                    influencer_id=target.influencer_id,
                    contact_id=target.contact_id,
                    platform_account_id=target.platform_account_id,
                )
                target_snapshot = endpoint.sent_snapshot
            else:
                target_snapshot = None
            now = _as_utc(self.clock.now())
            from_state = task.state
            task.state = transition_input.to_state
            task.version += 1
            await self.session.flush()
            event = self._add_event(
                task=task,
                target=target,
                event_type=self._event_type_for_transition(
                    from_state,
                    transition_input.to_state,
                ),
                actor_id=actor_id,
                occurred_at=now,
                from_state=from_state,
                to_state=task.state,
                reason_code=transition_input.reason_code,
                idempotency_key=key,
                request_hash=request_hash,
                target_snapshot=target_snapshot,
                event_metadata={"task_version": task.version},
            )
            await self.session.flush()
            result = OutreachTransitionResult(
                task_id=task.id,
                state=task.state,
                version=task.version,
                event_id=event.id,
                replayed=False,
            )
            self.audit.add(
                action=AuditAction.OUTREACH_TASK_TRANSITIONED,
                result=AuditResult.SUCCESS,
                department_id=scope.department_id,
                operator_id=actor_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="outreach_task",
                entity_id=task.id,
                before={"state": from_state.value, "version": task.version - 1},
                after={
                    "state": task.state.value,
                    "version": task.version,
                    "event_type": event.event_type.value,
                    "reason_code": transition_input.reason_code,
                    "cross_department_override": scope.cross_department_override,
                },
            )
            await self.session.commit()
            return result
        except IntegrityError as error:
            await self.session.rollback()
            raced_task = await self.repository.get_task(task_id, department_id=scope.department_id)
            if raced_task is not None:
                raced_event = await self.repository.get_event_by_idempotency_key(
                    task_id=raced_task.id,
                    idempotency_key=key,
                )
                if raced_event is not None:
                    return self._replay_transition(raced_event, request_hash)
            raise CampaignOutreachError(
                409,
                "OUTREACH_TASK_TRANSITION_CONFLICT",
                "Outreach task transition conflicted",
            ) from error
        except BaseException:
            await self.session.rollback()
            raise

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

    async def _locked_target(self, target_id: UUID, scope: DepartmentScope) -> OutreachTarget:
        target = await self.repository.get_target(
            target_id,
            department_id=scope.department_id,
            for_update=True,
        )
        if target is None:
            raise CampaignOutreachError(
                404, "OUTREACH_TARGET_NOT_FOUND", "Outreach target not found"
            )
        return target

    async def _locked_task(self, task_id: UUID, scope: DepartmentScope) -> OutreachTask:
        task = await self.repository.get_task(
            task_id,
            department_id=scope.department_id,
            for_update=True,
        )
        if task is None:
            raise CampaignOutreachError(404, "OUTREACH_TASK_NOT_FOUND", "Outreach task not found")
        return task

    async def _locked_active_member(
        self,
        *,
        campaign: Campaign,
        member_id: UUID,
        influencer_id: UUID,
        scope: DepartmentScope,
    ) -> CampaignMember:
        member = await self.repository.get_member(
            member_id,
            campaign_id=campaign.id,
            department_id=scope.department_id,
            for_update=True,
        )
        if member is None or member.influencer_id != influencer_id:
            raise CampaignOutreachError(
                404,
                "CAMPAIGN_MEMBER_NOT_FOUND",
                "Campaign member not found",
            )
        if member.removed_at is not None:
            raise CampaignOutreachError(
                409,
                "CAMPAIGN_MEMBER_INACTIVE",
                "Outreach target requires an active Campaign member",
            )
        return member

    async def _validate_endpoint(
        self,
        *,
        channel: OutreachChannel,
        influencer_id: UUID,
        contact_id: UUID | None,
        platform_account_id: UUID | None,
    ) -> EndpointFacts:
        has_contact = contact_id is not None
        has_account = platform_account_id is not None
        if has_contact == has_account:
            raise CampaignOutreachError(
                422,
                "OUTREACH_ENDPOINT_INVALID",
                "Exactly one canonical endpoint reference is required",
            )
        if contact_id is not None:
            contact = await self.repository.get_contact(contact_id)
            if (
                contact is None
                or contact.influencer_id != influencer_id
                or not contact.is_current
                or contact.validation_status is ContactValidationStatus.INVALID
            ):
                raise CampaignOutreachError(
                    422,
                    "OUTREACH_CONTACT_INVALID",
                    "Outreach target requires a current valid canonical contact",
                )
            self._require_contact_channel(channel, contact)
            return EndpointFacts(
                requires_review=contact.validation_status is ContactValidationStatus.UNVERIFIED,
                sent_snapshot={
                    "contact_id": str(contact.id),
                    "platform_account_id": None,
                    "channel": channel.value,
                    "masked_display": "***",
                    "contact_type": contact.type.value,
                    "validation_status": contact.validation_status.value,
                    "source": contact.source.value,
                    "endpoint_fingerprint": self._endpoint_fingerprint(
                        f"contact:{contact.type.value}",
                        contact.normalized_value,
                    ),
                },
            )
        if platform_account_id is None:
            raise CampaignOutreachError(422, "OUTREACH_ENDPOINT_INVALID", "Endpoint is required")
        account = await self.repository.get_platform_account(platform_account_id)
        if account is None or account.influencer_id != influencer_id or not account.is_active:
            raise CampaignOutreachError(
                422,
                "OUTREACH_PLATFORM_ACCOUNT_INVALID",
                "Outreach target requires an active canonical platform account",
            )
        self._require_account_channel(channel, account)
        return EndpointFacts(
            requires_review=False,
            sent_snapshot={
                "contact_id": None,
                "platform_account_id": str(account.id),
                "channel": channel.value,
                "masked_display": "***",
                "platform": account.platform.value,
                "is_active": account.is_active,
                "source": account.source.value,
                "endpoint_fingerprint": self._endpoint_fingerprint(
                    "platform_account",
                    self._platform_endpoint_identity(account),
                ),
            },
        )

    @staticmethod
    def _require_contact_channel(channel: OutreachChannel, contact: InfluencerContact) -> None:
        if channel is OutreachChannel.EMAIL:
            if (
                contact.type is not ContactType.EMAIL
                or contact.validation_status is not ContactValidationStatus.VALID
            ):
                raise CampaignOutreachError(
                    422,
                    "OUTREACH_CONTACT_INVALID",
                    "Email requires a current valid Email contact",
                )
            return
        if channel is OutreachChannel.WECHAT:
            if contact.type is not ContactType.WECHAT:
                raise CampaignOutreachError(
                    422,
                    "OUTREACH_CONTACT_INVALID",
                    "WeChat requires a current WeChat contact",
                )
            return
        if channel is OutreachChannel.MANUAL:
            return
        raise CampaignOutreachError(
            422,
            "OUTREACH_ENDPOINT_INVALID",
            "This channel requires a platform account reference",
        )

    @staticmethod
    def _require_account_channel(
        channel: OutreachChannel,
        account: InfluencerPlatformAccount,
    ) -> None:
        if channel is OutreachChannel.XIAOHONGSHU_PRIVATE_MESSAGE:
            if account.platform is not Platform.XIAOHONGSHU:
                raise CampaignOutreachError(
                    422,
                    "OUTREACH_PLATFORM_ACCOUNT_INVALID",
                    "Xiaohongshu private message requires a Xiaohongshu account",
                )
            return
        if channel is OutreachChannel.MANUAL:
            return
        if channel is OutreachChannel.DOUYIN_PRIVATE_MESSAGE:
            raise CampaignOutreachError(
                409,
                "CHANNEL_UNAVAILABLE",
                "Douyin cannot be enabled without canonical Douyin account data",
            )
        raise CampaignOutreachError(
            422,
            "OUTREACH_ENDPOINT_INVALID",
            "This channel requires a contact reference",
        )

    async def _validate_task_assignment(
        self,
        department_id: UUID,
        assigned_operator_id: UUID | None,
    ) -> None:
        if assigned_operator_id is None:
            return
        operator = await self.auth_repository.get_operator(assigned_operator_id)
        if (
            operator is None
            or operator.department_id != department_id
            or operator.status is not OperatorStatus.ACTIVE
        ):
            raise CampaignOutreachError(404, "OPERATOR_NOT_FOUND", "Assigned operator not found")

    async def _validate_template_reference(
        self,
        *,
        department_id: UUID,
        channel: OutreachChannel,
        template_version_id: UUID | None,
    ) -> None:
        if template_version_id is None:
            return
        record = await self.repository.get_template_version(template_version_id)
        if (
            record is None
            or record.template.department_id != department_id
            or record.template.channel is not channel
            or record.template.state is not MessageTemplateState.ACTIVE
        ):
            raise CampaignOutreachError(
                404,
                "MESSAGE_TEMPLATE_VERSION_NOT_FOUND",
                "Message template version not found",
            )

    async def _initial_task_state(
        self,
        *,
        campaign: Campaign,
        member: CampaignMember,
        target: OutreachTarget,
        endpoint: EndpointFacts,
        now: datetime,
    ) -> tuple[OutreachTaskState, HistoryWarning | None]:
        latest_event = await self.repository.latest_sent_event(
            influencer_id=target.influencer_id,
            channel=target.channel,
        )
        history_warning = (
            HistoryWarning(
                channel=latest_event.channel,
                last_sent_at=_database_as_utc(latest_event.occurred_at),
            )
            if latest_event is not None
            else None
        )
        if campaign.duplicate_history_policy is DuplicateHistoryPolicy.BLOCK_WITHIN_WINDOW:
            if campaign.duplicate_window_days is None:
                raise CampaignOutreachError(
                    500,
                    "CAMPAIGN_CONFIG_INVALID",
                    "Campaign duplicate history configuration is invalid",
                )
            within_window = await self.repository.latest_sent_event(
                influencer_id=target.influencer_id,
                channel=target.channel,
                not_before=now - timedelta(days=campaign.duplicate_window_days),
            )
            if within_window is not None:
                raise CampaignOutreachError(
                    409,
                    "DUPLICATE_HISTORY_BLOCKED",
                    "Recent cross-Campaign outreach blocks this Task",
                )
        requires_review = endpoint.requires_review
        if (
            history_warning is not None
            and campaign.duplicate_history_policy is DuplicateHistoryPolicy.REQUIRE_CONFIRMATION
        ):
            requires_review = True
        if campaign.review_mode is CampaignReviewMode.ALL:
            requires_review = True
        elif campaign.review_mode is CampaignReviewMode.FIRST_N:
            if campaign.review_count is None:
                raise CampaignOutreachError(
                    500,
                    "CAMPAIGN_CONFIG_INVALID",
                    "Campaign review configuration is invalid",
                )
            review_member_ids = await self.repository.list_active_member_ids(
                campaign.id,
                limit=campaign.review_count,
            )
            requires_review = requires_review or member.id in review_member_ids
        elif campaign.review_mode is CampaignReviewMode.SAMPLE:
            # The frozen schema records no sample size.  This fixed hash gate keeps
            # the selection deterministic by Member ID while retaining a small,
            # explicit Phase 3A conservative sample seam.
            digest = hashlib.sha256(member.id.bytes).digest()[0]
            requires_review = requires_review or digest < round(256 * SAMPLE_REVIEW_PERCENT / 100)
        elif campaign.review_mode is CampaignReviewMode.AUTO:
            pass
        else:
            raise CampaignOutreachError(
                500,
                "CAMPAIGN_CONFIG_INVALID",
                "Campaign review configuration is invalid",
            )
        return (
            OutreachTaskState.REVIEW_REQUIRED if requires_review else OutreachTaskState.READY,
            history_warning,
        )

    def _add_event(
        self,
        *,
        task: OutreachTask,
        target: OutreachTarget,
        event_type: OutreachEventType,
        actor_id: UUID,
        occurred_at: datetime,
        from_state: OutreachTaskState | None,
        to_state: OutreachTaskState | None,
        reason_code: str | None,
        idempotency_key: str | None,
        request_hash: str | None,
        target_snapshot: dict[str, object] | None,
        event_metadata: Mapping[str, object],
    ) -> OutreachEvent:
        event = OutreachEvent(
            department_id=task.department_id,
            campaign_id=task.campaign_id,
            influencer_id=target.influencer_id,
            task_id=task.id,
            channel=target.channel,
            event_type=event_type,
            actor_type=OutreachActorType.OPERATOR,
            actor_operator_id=actor_id,
            occurred_at=occurred_at,
            from_state=from_state,
            to_state=to_state,
            reason_code=reason_code,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            redacted_target_snapshot=target_snapshot,
            redacted_message_snapshot=None,
            event_metadata=dict(event_metadata),
        )
        self.session.add(event)
        return event

    async def _replay_task_create(
        self,
        task: OutreachTask,
        request_hash: str,
    ) -> MutationResult[OutreachTaskCreateResult]:
        if task.create_request_hash != request_hash:
            raise CampaignOutreachError(
                409,
                "IDEMPOTENCY_KEY_REUSED",
                "Idempotency key was already used for a different request",
            )
        created_event = await self.repository.get_task_created_event(task.id)
        history_warning = self._history_warning_from_event(created_event)
        return MutationResult(
            status_code=201,
            result=OutreachTaskCreateResult(
                task=OutreachTaskResult.from_model(task),
                history_warning=history_warning,
            ),
            replayed=True,
        )

    @staticmethod
    def _history_warning_from_event(event: OutreachEvent | None) -> HistoryWarning | None:
        if event is None or not isinstance(event.event_metadata, dict):
            return None
        payload = event.event_metadata.get("history_warning")
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise CampaignOutreachError(
                500,
                "OUTREACH_EVENT_INVALID",
                "Task creation Event has invalid history metadata",
            )
        return HistoryWarning.model_validate(payload)

    @staticmethod
    def _replay_target(
        record: Phase3AIdempotencyRecord,
        request_hash: str,
    ) -> MutationResult[OutreachTargetResult]:
        OutreachService._require_matching_hash(record, request_hash)
        if record.result_schema_version != 1:
            raise CampaignOutreachError(
                500,
                "IDEMPOTENCY_RESULT_UNSUPPORTED",
                "Outreach target replay result schema is unsupported",
            )
        result = OutreachTargetResult.from_replay_payload(record.result_payload)
        if result.id != record.result_entity_id:
            raise CampaignOutreachError(
                500,
                "IDEMPOTENCY_RESULT_INVALID",
                "Outreach target replay result does not match its stored entity",
            )
        return MutationResult(status_code=201, result=result, replayed=True)

    @staticmethod
    def _replay_transition(
        event: OutreachEvent,
        request_hash: str,
    ) -> OutreachTransitionResult:
        if event.request_hash != request_hash:
            raise CampaignOutreachError(
                409,
                "IDEMPOTENCY_KEY_REUSED",
                "Idempotency key was already used for a different request",
            )
        if event.to_state is None or not isinstance(event.event_metadata, dict):
            raise CampaignOutreachError(
                500,
                "OUTREACH_EVENT_INVALID",
                "Outreach transition Event has invalid replay metadata",
            )
        version = event.event_metadata.get("task_version")
        if not isinstance(version, int) or version < 1:
            raise CampaignOutreachError(
                500,
                "OUTREACH_EVENT_INVALID",
                "Outreach transition Event has invalid Task version metadata",
            )
        return OutreachTransitionResult(
            task_id=event.task_id,
            state=event.to_state,
            version=version,
            event_id=event.id,
            replayed=True,
        )

    @staticmethod
    def _target_result(target: OutreachTarget) -> OutreachTargetResult:
        # This domain surface deliberately returns no canonical Contact plaintext;
        # callers that need authorized Contact data use the canonical Influencer service.
        return OutreachTargetResult.from_model(target, masked_display="***")

    @staticmethod
    def _safe_target_state(target: OutreachTarget) -> dict[str, object]:
        return {
            "campaign_id": str(target.campaign_id),
            "member_id": str(target.member_id),
            "channel": target.channel.value,
            "contact_id": str(target.contact_id) if target.contact_id is not None else None,
            "platform_account_id": (
                str(target.platform_account_id) if target.platform_account_id is not None else None
            ),
            "version": target.version,
        }

    @staticmethod
    def _platform_endpoint_identity(account: InfluencerPlatformAccount) -> str:
        """Return the live account identity to freeze in a non-plaintext fingerprint."""

        canonical_identity = "\x1f".join(
            value
            for value in (
                account.platform_account_id,
                account.normalized_profile_url,
                account.account_handle,
            )
            if value
        )
        return "\x1f".join((account.platform.value, canonical_identity or str(account.id)))

    @staticmethod
    def _endpoint_fingerprint(kind: str, endpoint_value: str) -> str:
        """Hash the canonical endpoint at send time without persisting plaintext."""

        return hashlib.sha256(f"phase3a:endpoint:v1:{kind}:{endpoint_value}".encode()).hexdigest()

    def _require_channel_enabled(self, channel: OutreachChannel) -> None:
        if channel is OutreachChannel.DOUYIN_PRIVATE_MESSAGE:
            raise CampaignOutreachError(
                409,
                "CHANNEL_UNAVAILABLE",
                "Douyin cannot be enabled without canonical Douyin account data",
            )
        if not self.channel_registry.is_enabled(channel):
            raise CampaignOutreachError(
                409,
                "CHANNEL_DISABLED",
                "Outreach channel is not enabled in this Phase",
            )

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
    def _is_legal_task_transition(
        current: OutreachTaskState,
        target: OutreachTaskState,
    ) -> bool:
        return (
            target
            in {
                OutreachTaskState.REVIEW_REQUIRED: {
                    OutreachTaskState.READY,
                    OutreachTaskState.STOPPED,
                },
                OutreachTaskState.READY: {
                    OutreachTaskState.SENT,
                    OutreachTaskState.FAILED,
                    OutreachTaskState.STOPPED,
                },
                OutreachTaskState.FAILED: {OutreachTaskState.READY},
                OutreachTaskState.SENT: set(),
                OutreachTaskState.STOPPED: set(),
            }[current]
        )

    @staticmethod
    def _event_type_for_transition(
        from_state: OutreachTaskState,
        to_state: OutreachTaskState,
    ) -> OutreachEventType:
        event_types = {
            (
                OutreachTaskState.REVIEW_REQUIRED,
                OutreachTaskState.READY,
            ): OutreachEventType.REVIEW_APPROVED,
            (
                OutreachTaskState.REVIEW_REQUIRED,
                OutreachTaskState.STOPPED,
            ): OutreachEventType.OUTREACH_STOPPED,
            (OutreachTaskState.READY, OutreachTaskState.SENT): OutreachEventType.OUTREACH_SENT,
            (OutreachTaskState.READY, OutreachTaskState.FAILED): OutreachEventType.OUTREACH_FAILED,
            (
                OutreachTaskState.READY,
                OutreachTaskState.STOPPED,
            ): OutreachEventType.OUTREACH_STOPPED,
            (OutreachTaskState.FAILED, OutreachTaskState.READY): OutreachEventType.OUTREACH_RETRIED,
        }
        try:
            return event_types[(from_state, to_state)]
        except KeyError as error:
            raise CampaignOutreachError(
                500,
                "OUTREACH_EVENT_MAPPING_INVALID",
                "No Event type exists for the Task transition",
            ) from error

    @staticmethod
    def _require_matching_hash(record: Phase3AIdempotencyRecord, request_hash: str) -> None:
        if record.request_hash != request_hash:
            raise CampaignOutreachError(
                409,
                "IDEMPOTENCY_KEY_REUSED",
                "Idempotency key was already used for a different request",
            )

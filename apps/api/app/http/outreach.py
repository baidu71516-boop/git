"""Closed HTTP adaptation for existing Phase 3A Outreach operations.

The Today read model deliberately lives in a separate router.  `main.py` must
include that literal-route router immediately before this one so `/today` is
never captured by the UUID task detail route below.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from backend_core.auth import EXACT, EffectiveAuthorizationContext, ModuleKey
from backend_core.campaigns.access import DepartmentScope
from backend_core.outreach.enums import (
    OutreachChannel,
    OutreachPriority,
    OutreachTaskKind,
    OutreachTaskState,
)
from backend_core.outreach.schemas import (
    OutreachEventResult,
    OutreachTargetResult,
    OutreachTargetUpdateInput,
    OutreachTaskCreateInput,
    OutreachTaskCreateResult,
    OutreachTaskResult,
    OutreachTaskTransitionInput,
    OutreachTransitionResult,
)
from backend_core.outreach.service import OutreachService
from fastapi import APIRouter, Depends, Header, Request, Response, status
from pydantic import Field, StrictInt, StrictStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.http.dependencies import (
    get_client_ip,
    get_database_session,
    get_user_agent,
    require_module,
    require_module_write,
)
from app.http.phase3a_http import (
    Phase3AHttpWrite,
    domain_input_with_scope,
    error_responses,
    require_single_idempotency_key,
)
from app.http.phase3a_scope import resolve_phase3a_department_scope
from app.http.responses import SuccessEnvelope, envelope

# Keep this router independent of the Today projection router. `main.py` owns
# their inclusion order and places the literal `/outreach-tasks/today` first.
router = APIRouter(prefix="/api/v1", tags=["outreach"])
CampaignsReadContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module(EXACT(ModuleKey.CAMPAIGNS))),
]
CampaignsWriteContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module_write(EXACT(ModuleKey.CAMPAIGNS))),
]
TodayOutreachReadContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module(EXACT(ModuleKey.TODAY_OUTREACH))),
]
TodayOutreachWriteContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module_write(EXACT(ModuleKey.TODAY_OUTREACH))),
]

type PositiveStrictInt = Annotated[StrictInt, Field(ge=1)]
type ReasonCode = Annotated[
    StrictStr,
    Field(pattern=r"^[A-Z][A-Z0-9_]{0,79}$", min_length=1, max_length=80),
]
type StepKey = Annotated[StrictStr, Field(min_length=1, max_length=120)]


class OutreachTargetCreateRequest(Phase3AHttpWrite):
    """Public create contract; Campaign identity comes from the path."""

    member_id: UUID
    influencer_id: UUID
    channel: OutreachChannel
    contact_id: UUID | None = None
    platform_account_id: UUID | None = None


class OutreachTargetUpdateRequest(Phase3AHttpWrite):
    """Only endpoint reference and CAS version are mutable through PUT."""

    contact_id: UUID | None = None
    platform_account_id: UUID | None = None
    expected_version: PositiveStrictInt


class OutreachTaskCreateRequest(Phase3AHttpWrite):
    kind: OutreachTaskKind = OutreachTaskKind.FIRST_TOUCH
    step_key: StepKey = "initial"
    priority: OutreachPriority = OutreachPriority.NORMAL
    priority_reason_codes: tuple[ReasonCode, ...] = ()
    assigned_operator_id: UUID | None = None
    due_at: datetime
    template_version_id: UUID | None = None


class OutreachTaskTransitionRequest(Phase3AHttpWrite):
    to_state: OutreachTaskState
    expected_version: PositiveStrictInt
    reason_code: ReasonCode | None = None


def get_outreach_service(
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> OutreachService:
    return OutreachService(session)


@router.get(
    "/outreach-targets/{target_id}",
    response_model=SuccessEnvelope[OutreachTargetResult],
    responses=error_responses(401, 404, 422),
)
async def get_outreach_target(
    target_id: UUID,
    request: Request,
    context: CampaignsReadContext,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    service: Annotated[OutreachService, Depends(get_outreach_service)],
) -> dict[str, Any]:
    target = await service.get_target(
        context,
        target_id,
        department_id=scope.department_id,
    )
    return envelope(request, data=target)


@router.put(
    "/outreach-targets/{target_id}",
    response_model=SuccessEnvelope[OutreachTargetResult],
    responses=error_responses(401, 403, 404, 409, 422),
)
async def update_outreach_target(
    target_id: UUID,
    payload: OutreachTargetUpdateRequest,
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: CampaignsWriteContext,
    service: Annotated[OutreachService, Depends(get_outreach_service)],
) -> dict[str, Any]:
    target = await service.update_target(
        context,
        target_id,
        domain_input_with_scope(
            payload,
            OutreachTargetUpdateInput,
            department_id=scope.department_id,
        ),
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return envelope(request, data=target)


@router.post(
    "/outreach-targets/{target_id}/tasks",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessEnvelope[OutreachTaskCreateResult],
    responses=error_responses(401, 403, 404, 409, 422),
)
async def create_outreach_task(
    target_id: UUID,
    payload: OutreachTaskCreateRequest,
    request: Request,
    response: Response,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: CampaignsWriteContext,
    service: Annotated[OutreachService, Depends(get_outreach_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    result = await service.create_task(
        context,
        target_id,
        domain_input_with_scope(
            payload,
            OutreachTaskCreateInput,
            department_id=scope.department_id,
        ),
        idempotency_key=require_single_idempotency_key(request, idempotency_key),
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    response.status_code = result.status_code
    return envelope(request, data=result.result)


@router.get(
    "/outreach-tasks/{task_id}",
    response_model=SuccessEnvelope[OutreachTaskResult],
    responses=error_responses(401, 404, 422),
)
async def get_outreach_task(
    task_id: UUID,
    request: Request,
    context: TodayOutreachReadContext,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    service: Annotated[OutreachService, Depends(get_outreach_service)],
) -> dict[str, Any]:
    task = await service.get_task(
        context,
        task_id,
        department_id=scope.department_id,
    )
    return envelope(request, data=task)


@router.post(
    "/outreach-tasks/{task_id}/transitions",
    response_model=SuccessEnvelope[OutreachTransitionResult],
    responses=error_responses(401, 403, 404, 409, 422),
)
async def transition_outreach_task(
    task_id: UUID,
    payload: OutreachTaskTransitionRequest,
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: TodayOutreachWriteContext,
    service: Annotated[OutreachService, Depends(get_outreach_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    transition = await service.transition_task(
        context,
        task_id,
        domain_input_with_scope(
            payload,
            OutreachTaskTransitionInput,
            department_id=scope.department_id,
        ),
        idempotency_key=require_single_idempotency_key(request, idempotency_key),
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return envelope(request, data=transition)


@router.get(
    "/outreach-tasks/{task_id}/events",
    response_model=SuccessEnvelope[tuple[OutreachEventResult, ...]],
    responses=error_responses(401, 404, 422),
)
async def list_outreach_task_events(
    task_id: UUID,
    request: Request,
    context: TodayOutreachReadContext,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    service: Annotated[OutreachService, Depends(get_outreach_service)],
) -> dict[str, Any]:
    events = await service.list_task_events(
        context,
        task_id,
        department_id=scope.department_id,
    )
    return envelope(request, data=tuple(events))


__all__ = [
    "OutreachTargetCreateRequest",
    "OutreachTargetUpdateRequest",
    "OutreachTaskCreateRequest",
    "OutreachTaskTransitionRequest",
    "get_outreach_service",
    "router",
]

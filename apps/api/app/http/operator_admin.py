"""Permissions V1 same-Department Super Admin Operator administration API."""

from typing import Annotated, Any
from uuid import UUID

from backend_core.auth import EXACT, EffectiveAuthorizationContext, ModuleKey
from backend_core.auth.operator_admin_service import OperatorAdminService
from backend_core.auth.schemas import (
    OperatorAdminCreateInput,
    OperatorAdminPublic,
    OperatorAdminUpdateInput,
)
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.http.dependencies import (
    get_client_ip,
    get_database_session,
    get_user_agent,
    require_module,
    require_module_write,
)
from app.http.responses import SuccessEnvelope, envelope

router = APIRouter(prefix="/api/v1/admin/operators", tags=["admin"])
AdminReadContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module(EXACT(ModuleKey.ADMIN))),
]
AdminWriteContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module_write(EXACT(ModuleKey.ADMIN))),
]


def get_operator_admin_service(
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> OperatorAdminService:
    return OperatorAdminService(session)


@router.get("", response_model=SuccessEnvelope[list[OperatorAdminPublic]])
async def list_operators(
    request: Request,
    context: AdminReadContext,
    service: Annotated[OperatorAdminService, Depends(get_operator_admin_service)],
) -> dict[str, Any]:
    return envelope(request, data=await service.list_operators(context))


@router.get("/{operator_id}", response_model=SuccessEnvelope[OperatorAdminPublic])
async def get_operator(
    operator_id: UUID,
    request: Request,
    context: AdminReadContext,
    service: Annotated[OperatorAdminService, Depends(get_operator_admin_service)],
) -> dict[str, Any]:
    return envelope(request, data=await service.get_operator(context, operator_id=operator_id))


@router.post("", response_model=SuccessEnvelope[OperatorAdminPublic])
async def create_operator(
    payload: OperatorAdminCreateInput,
    request: Request,
    context: AdminWriteContext,
    service: Annotated[OperatorAdminService, Depends(get_operator_admin_service)],
) -> dict[str, Any]:
    operator = await service.create_operator(
        context,
        payload,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return envelope(request, data=operator)


@router.patch("/{operator_id}", response_model=SuccessEnvelope[OperatorAdminPublic])
async def update_operator(
    operator_id: UUID,
    payload: OperatorAdminUpdateInput,
    request: Request,
    context: AdminWriteContext,
    service: Annotated[OperatorAdminService, Depends(get_operator_admin_service)],
) -> dict[str, Any]:
    operator = await service.update_operator(
        context,
        operator_id=operator_id,
        payload=payload,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return envelope(request, data=operator)

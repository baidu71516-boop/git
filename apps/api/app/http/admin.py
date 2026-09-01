"""Minimal Phase 1A administrative security routes."""

from typing import Annotated, Any
from uuid import UUID

from backend_core.auth import EXACT, EffectiveAuthorizationContext, ModuleKey
from backend_core.auth.schemas import PasswordResetPublic, ResetPasswordInput
from backend_core.auth.service import AuthService
from fastapi import APIRouter, Depends, Request

from app.http.dependencies import (
    get_auth_service,
    get_client_ip,
    get_user_agent,
    require_module_write,
)
from app.http.responses import envelope

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
AdminWriteContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module_write(EXACT(ModuleKey.ADMIN))),
]


@router.post("/departments/{department_id}/reset-password")
async def reset_department_password(
    department_id: UUID,
    payload: ResetPasswordInput,
    request: Request,
    context: AdminWriteContext,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> dict[str, Any]:
    revoked_sessions = await service.reset_department_password(
        context,
        department_id=department_id,
        new_password=payload.new_password.get_secret_value(),
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    data = PasswordResetPublic(
        department_id=department_id,
        revoked_sessions=revoked_sessions,
    )
    return envelope(request, data=data)

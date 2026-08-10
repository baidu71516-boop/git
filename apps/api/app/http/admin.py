"""Minimal Phase 1A administrative security routes."""

from typing import Annotated, Any
from uuid import UUID

from backend_core.auth.schemas import PasswordResetPublic, ResetPasswordInput
from backend_core.auth.service import AuthContext, AuthService
from fastapi import APIRouter, Depends, Request

from app.http.dependencies import (
    get_auth_service,
    get_client_ip,
    get_user_agent,
    require_super_admin,
)
from app.http.responses import envelope

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.post("/departments/{department_id}/reset-password")
async def reset_department_password(
    department_id: UUID,
    payload: ResetPasswordInput,
    request: Request,
    context: Annotated[AuthContext, Depends(require_super_admin)],
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

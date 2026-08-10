"""Phase 1A authentication HTTP routes."""

from typing import Annotated, Any

from backend_core.auth.schemas import (
    AuthMePublic,
    DepartmentPublic,
    LoginInput,
    LoginPublic,
    OperatorPublic,
    SelectOperatorInput,
)
from backend_core.auth.service import AuthContext, AuthService
from backend_core.config import get_settings
from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.http.cookies import clear_auth_cookies, set_auth_cookies
from app.http.dependencies import (
    get_auth_service,
    get_client_ip,
    get_user_agent,
    require_auth,
    require_csrf_context,
)
from app.http.responses import envelope

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
settings = get_settings()


def context_public(context: AuthContext) -> AuthMePublic:
    return AuthMePublic(
        department=DepartmentPublic.model_validate(context.department),
        operator=(
            OperatorPublic.model_validate(context.operator)
            if context.operator is not None
            else None
        ),
        role=context.role,
        expires_at=context.auth_session.expires_at,
    )


@router.post("/login")
async def login(
    payload: LoginInput,
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> JSONResponse:
    result = await service.login(
        department_id=payload.department_id,
        password=payload.password.get_secret_value(),
        remember_me=payload.remember_me,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    public = LoginPublic(
        department=DepartmentPublic.model_validate(result.department),
        role=result.role,
        operator_required=True,
        expires_at=result.auth_session.expires_at,
    )
    response = JSONResponse(content=jsonable_encoder(envelope(request, data=public)))
    set_auth_cookies(response, result, settings)
    return response


@router.get("/me")
async def me(
    request: Request,
    context: Annotated[AuthContext, Depends(require_auth)],
) -> dict[str, Any]:
    return envelope(request, data=context_public(context))


@router.post("/select-operator")
async def select_operator(
    payload: SelectOperatorInput,
    request: Request,
    context: Annotated[AuthContext, Depends(require_csrf_context)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> dict[str, Any]:
    updated = await service.select_operator(
        context,
        payload.operator_id,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return envelope(request, data=context_public(updated))


@router.post("/logout")
async def logout(
    request: Request,
    context: Annotated[AuthContext, Depends(require_csrf_context)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> JSONResponse:
    await service.logout(
        context,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    response = JSONResponse(content=jsonable_encoder(envelope(request, data={"logged_out": True})))
    clear_auth_cookies(response, settings)
    return response

"""Operator selection lookup; Operator Role does not authorize the Session."""

from typing import Annotated, Any

from backend_core.auth.schemas import OperatorPublic
from backend_core.auth.service import AuthContext, AuthService
from backend_core.campaigns.access import DepartmentScope
from fastapi import APIRouter, Depends, Request

from app.http.dependencies import get_auth_service, require_auth
from app.http.phase3a_scope import resolve_phase3a_department_scope
from app.http.responses import SuccessEnvelope, envelope

router = APIRouter(prefix="/api/v1/operators", tags=["operators"])


@router.get("", response_model=SuccessEnvelope[list[OperatorPublic]])
async def list_operators(
    request: Request,
    context: Annotated[AuthContext, Depends(require_auth)],
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> dict[str, Any]:
    operators = await service.list_operators(context, department_id=scope.department_id)
    public = [OperatorPublic.model_validate(operator) for operator in operators]
    return envelope(request, data=public)

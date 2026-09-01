"""Department-scope resolution shared by Phase 3A HTTP routers."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from backend_core.auth import AuthContext, AuthService
from backend_core.campaigns.access import DepartmentScope
from fastapi import Depends, Header, Request
from fastapi.exceptions import RequestValidationError

from app.http.dependencies import get_auth_service, require_auth


def _header_validation_error(*, message: str) -> RequestValidationError:
    return RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": ("header", "X-Department-ID"),
                "msg": message,
                "input": None,
                "ctx": {"error": ValueError(message)},
            }
        ]
    )


def _requested_department_id(request: Request) -> UUID | None:
    """Read the header directly so scalar binding cannot choose a duplicate value."""

    values = request.headers.getlist("x-department-id")
    if len(values) > 1:
        raise _header_validation_error(message="X-Department-ID must not be repeated")
    if not values:
        return None
    try:
        return UUID(values[0])
    except ValueError as error:
        raise _header_validation_error(message="X-Department-ID must be a valid UUID") from error


async def resolve_phase3a_department_scope(
    request: Request,
    context: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[AuthService, Depends(get_auth_service)],
    # This scalar is intentionally not trusted for selection. It documents the
    # optional header in OpenAPI while `_requested_department_id` rejects repeats.
    _department_header: Annotated[
        str | None,
        Header(
            alias="X-Department-ID",
            description=(
                "Optional Phase 3A Department scope override. Omit to use the authenticated "
                "Department; cross-Department use is restricted to Super Admin. The header is "
                "single-valued; repeated values are rejected."
            ),
        ),
    ] = None,
) -> DepartmentScope:
    """Resolve the frozen Phase 3A Department boundary before router dispatch."""

    del _department_header
    requested_department_id = _requested_department_id(request)
    authorization = await service.resolve_effective_authorization(
        context,
        department_id=requested_department_id,
    )
    return DepartmentScope(
        department_id=authorization.department_scope.department_id,
        cross_department_override=authorization.department_scope.cross_department_override,
    )


async def resolve_phase3a_department_id(
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
) -> UUID:
    """Convenience dependency for routers that only need the target ID."""

    return scope.department_id


__all__ = ["resolve_phase3a_department_id", "resolve_phase3a_department_scope"]

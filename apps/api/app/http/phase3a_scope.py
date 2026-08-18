"""Department-scope resolution shared by Phase 3A HTTP routers."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from backend_core.auth import AuthContext
from backend_core.auth.repository import AuthRepository
from backend_core.campaigns.access import CampaignOutreachAccess, DepartmentScope
from backend_core.campaigns.errors import CampaignOutreachError
from fastapi import Depends, Header, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.http.dependencies import get_database_session, require_auth
from app.http.errors import ApiError


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
    session: Annotated[AsyncSession, Depends(get_database_session)],
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
    access = CampaignOutreachAccess(AuthRepository(session))
    try:
        return await access.resolve_read_scope(context, requested_department_id)
    except CampaignOutreachError as error:
        raise ApiError(error.status_code, error.code, error.message) from error


async def resolve_phase3a_department_id(
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
) -> UUID:
    """Convenience dependency for routers that only need the target ID."""

    return scope.department_id


__all__ = ["resolve_phase3a_department_id", "resolve_phase3a_department_scope"]

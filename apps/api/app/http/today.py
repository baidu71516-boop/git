"""Typed HTTP boundary for the read-only Phase 3A Today projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from backend_core.auth.service import AuthContext
from backend_core.campaigns.access import DepartmentScope
from backend_core.influencers.enums import ContactFilter
from backend_core.outreach.enums import OutreachChannel, OutreachPriority
from backend_core.outreach.today import TodayPage, TodayQuery, TodayService, TodayWorkKind
from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.http.dependencies import get_database_session, require_auth
from app.http.phase3a_http import error_responses, reject_closed_query_parameters
from app.http.phase3a_scope import resolve_phase3a_department_scope
from app.http.responses import SuccessEnvelope, envelope

today_router = APIRouter(prefix="/api/v1", tags=["outreach"])

TODAY_QUERY_PARAMETERS = frozenset(
    {
        "work_kind",
        "channel",
        "campaign_id",
        "owner_operator_id",
        "track",
        "followers_min",
        "followers_max",
        "contact_filter",
        "priority",
        "cursor",
        "limit",
    }
)


@dataclass(frozen=True, slots=True)
class TodayHttpRequest:
    """Parsed closed query and already-authorized Department scope."""

    context: AuthContext
    scope: DepartmentScope
    query: TodayQuery


def _today_validation_error(error: ValidationError) -> RequestValidationError:
    return RequestValidationError(error.errors())


async def parse_today_query(
    request: Request,
    work_kind: Annotated[
        TodayWorkKind,
        Query(description="Task kind: FIRST_TOUCH, FOLLOW_UP, or ALL."),
    ] = TodayWorkKind.ALL,
    channel: Annotated[
        OutreachChannel | None,
        Query(description="Filter by the OutreachTarget channel."),
    ] = None,
    campaign_id: Annotated[
        UUID | None,
        Query(description="Filter within the resolved Department by Campaign."),
    ] = None,
    owner_operator_id: Annotated[
        UUID | None,
        Query(
            description=(
                "Filters OutreachTask.assigned_operator_id, the Operator currently assigned to "
                "execute the task. It is not Campaign or Influencer ownership."
            )
        ),
    ] = None,
    track: Annotated[
        str | None,
        Query(
            max_length=160,
            description=(
                "Exact source_tags equality on the selected Campaign Member preferred "
                "PlatformAccount only."
            ),
        ),
    ] = None,
    followers_min: Annotated[
        int | None,
        Query(
            ge=0,
            description=(
                "Inclusive bound on the preferred account's current HUITUN followers_count."
            ),
        ),
    ] = None,
    followers_max: Annotated[
        int | None,
        Query(
            ge=0,
            description=(
                "Inclusive bound on the preferred account's current HUITUN followers_count."
            ),
        ),
    ] = None,
    contact_filter: Annotated[
        ContactFilter | None,
        Query(description="Current canonical-contact availability filter."),
    ] = None,
    priority: Annotated[
        OutreachPriority | None,
        Query(description="Task priority filter."),
    ] = None,
    cursor: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=4096,
            description=(
                "Opaque HMAC-signed continuation bound to the normalized query and resolved "
                "Department."
            ),
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Page size; defaults to 50."),
    ] = 50,
) -> TodayQuery:
    """Reject repeats/unknowns before semantic query parsing or scope selection."""

    reject_closed_query_parameters(request, TODAY_QUERY_PARAMETERS)
    try:
        return TodayQuery(
            work_kind=work_kind,
            channel=channel,
            campaign_id=campaign_id,
            owner_operator_id=owner_operator_id,
            track=track,
            followers_min=followers_min,
            followers_max=followers_max,
            contact_filter=contact_filter,
            priority=priority,
            cursor=cursor,
            limit=limit,
        )
    except ValidationError as error:
        raise _today_validation_error(error) from error


async def resolve_today_http_request(
    request: Request,
    query: Annotated[TodayQuery, Depends(parse_today_query)],
    context: Annotated[AuthContext, Depends(require_auth)],
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
) -> TodayHttpRequest:
    return TodayHttpRequest(context=context, scope=scope, query=query)


def get_today_service(
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> TodayService:
    return TodayService(session)


@today_router.get(
    "/outreach-tasks/today",
    response_model=SuccessEnvelope[TodayPage],
    responses=error_responses(401, 404, 409, 422),
    summary="List today's eligible outreach tasks",
    description=(
        "Read-only Today projection. Eligibility is active Campaign Member, READY Task, and "
        "due_at strictly before the next weekday Asia/Shanghai midnight. Phase 3A uses only "
        "Monday-Friday weekday boundaries; no holiday calendar is applied."
    ),
)
async def list_today(
    request: Request,
    today_request: Annotated[TodayHttpRequest, Depends(resolve_today_http_request)],
    service: Annotated[TodayService, Depends(get_today_service)],
) -> dict[str, Any]:
    page = await service.list_today(
        today_request.context,
        today_request.query,
        department_id=today_request.scope.department_id,
    )
    return envelope(request, data=page)


__all__ = [
    "TODAY_QUERY_PARAMETERS",
    "TodayHttpRequest",
    "get_today_service",
    "parse_today_query",
    "resolve_today_http_request",
    "today_router",
]

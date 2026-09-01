"""Thin HTTP adaptation for the Phase 1C read-only influencer library."""

from collections.abc import Awaitable
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from backend_core.auth import EXACT, EffectiveAuthorizationContext, ModuleKey
from backend_core.config import get_settings
from backend_core.influencers.enums import (
    ContactFilter,
    ContentActivityFilter,
    CRMStage,
    Notes7dFilter,
    Notes60dFilter,
)
from backend_core.influencers.freshness import (
    ContentActivityFreshnessPolicy,
    FreshnessPolicy,
    FreshnessStatus,
)
from backend_core.influencers.repository import InfluencerRepository
from backend_core.influencers.schemas import (
    InfluencerDetail,
    InfluencerFilterOptions,
    InfluencerListPage,
    InfluencerListQuery,
    MetricSnapshotPage,
)
from backend_core.influencers.service import (
    InfluencerNotFoundError,
    InfluencerPermissionError,
    InfluencerService,
)
from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.http.dependencies import get_database_session, require_module
from app.http.errors import ApiError
from app.http.responses import SuccessEnvelope, envelope

router = APIRouter(prefix="/api/v1/influencers", tags=["influencers"])
settings = get_settings()
InfluencerLibraryReadContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module(EXACT(ModuleKey.INFLUENCER_LIBRARY))),
]

LIST_QUERY_PARAMETERS = frozenset(
    {
        "q",
        "tag",
        "followers_min",
        "followers_max",
        "owner_operator_id",
        "crm_stage",
        "contact_filter",
        "notes_7d_filter",
        "notes_60d_filter",
        "content_activity_filter",
        "freshness_status",
        "requires_refresh",
        "last_huitun_observed_before",
        "last_huitun_observed_after",
        "page",
        "page_size",
    }
)
SNAPSHOT_QUERY_PARAMETERS = frozenset({"page", "page_size"})


def get_influencer_service(
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> InfluencerService:
    return InfluencerService(
        InfluencerRepository(session),
        freshness_policy=FreshnessPolicy.from_day_thresholds(
            settings.freshness_fresh_days,
            settings.freshness_aging_days,
            settings.freshness_stale_days,
        ),
        content_activity_freshness_policy=ContentActivityFreshnessPolicy.from_day_threshold(
            settings.content_activity_trusted_freshness_days,
        ),
    )


def _reject_duplicate_query_parameters(request: Request, allowed: frozenset[str]) -> None:
    unexpected = sorted(set(request.query_params) - allowed)
    if unexpected:
        name = unexpected[0]
        raise RequestValidationError(
            [
                {
                    "type": "extra_forbidden",
                    "loc": ("query", name),
                    "msg": "Extra inputs are not permitted",
                    "input": request.query_params.get(name),
                }
            ]
        )
    for name in allowed:
        if len(request.query_params.getlist(name)) > 1:
            raise RequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("query", name),
                        "msg": "Value error, query parameter must not be repeated",
                        "input": None,
                        "ctx": {"error": ValueError("query parameter must not be repeated")},
                    }
                ]
            )


def reject_duplicate_list_query_parameters(request: Request) -> None:
    _reject_duplicate_query_parameters(request, LIST_QUERY_PARAMETERS)


def reject_duplicate_snapshot_query_parameters(request: Request) -> None:
    _reject_duplicate_query_parameters(request, SNAPSHOT_QUERY_PARAMETERS)


def parse_influencer_list_query(
    request: Request,
    q: Annotated[str | None, Query(max_length=160)] = None,
    tag: Annotated[str | None, Query(max_length=160)] = None,
    followers_min: Annotated[int | None, Query(ge=0)] = None,
    followers_max: Annotated[int | None, Query(ge=0)] = None,
    owner_operator_id: Annotated[UUID | None, Query()] = None,
    crm_stage: Annotated[CRMStage | None, Query()] = None,
    contact_filter: Annotated[ContactFilter | None, Query()] = None,
    notes_7d_filter: Annotated[Notes7dFilter | None, Query()] = None,
    notes_60d_filter: Annotated[Notes60dFilter | None, Query()] = None,
    content_activity_filter: Annotated[ContentActivityFilter | None, Query()] = None,
    freshness_status: Annotated[FreshnessStatus | None, Query()] = None,
    requires_refresh: Annotated[bool | None, Query()] = None,
    last_huitun_observed_before: Annotated[datetime | None, Query()] = None,
    last_huitun_observed_after: Annotated[datetime | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> InfluencerListQuery:
    """Expose the frozen OpenAPI parameters while validating their raw values in core."""

    # FastAPI's typed parameters provide the OpenAPI contract and early structural
    # validation. The raw values are deliberately passed to the API-independent
    # Pydantic contract so its exact integer and cross-field rules remain final.
    _ = (
        q,
        tag,
        followers_min,
        followers_max,
        owner_operator_id,
        crm_stage,
        contact_filter,
        notes_7d_filter,
        notes_60d_filter,
        content_activity_filter,
        freshness_status,
        requires_refresh,
        last_huitun_observed_before,
        last_huitun_observed_after,
        page,
        page_size,
    )
    raw_query = {
        name: request.query_params[name]
        for name in LIST_QUERY_PARAMETERS
        if name in request.query_params
    }
    try:
        return InfluencerListQuery.model_validate(raw_query)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


def parse_snapshot_pagination(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> InfluencerListQuery:
    """Reuse the core strict query-integer contract for snapshot pagination."""

    _ = (page, page_size)
    raw_query = {
        name: request.query_params[name]
        for name in SNAPSHOT_QUERY_PARAMETERS
        if name in request.query_params
    }
    try:
        return InfluencerListQuery.model_validate(raw_query)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


async def _service_call[ResultT](awaitable: Awaitable[ResultT]) -> ResultT:
    try:
        return await awaitable
    except InfluencerNotFoundError as exc:
        raise ApiError(404, exc.code, exc.message) from exc
    except InfluencerPermissionError as exc:
        raise ApiError(403, exc.code, exc.message) from exc


@router.get(
    "",
    response_model=SuccessEnvelope[InfluencerListPage],
    dependencies=[Depends(reject_duplicate_list_query_parameters)],
)
async def list_influencers(
    request: Request,
    query: Annotated[InfluencerListQuery, Depends(parse_influencer_list_query)],
    context: InfluencerLibraryReadContext,
    service: Annotated[InfluencerService, Depends(get_influencer_service)],
) -> dict[str, Any]:
    page = await _service_call(service.list_influencers(context, query))
    return envelope(request, data=page)


@router.get("/filter-options", response_model=SuccessEnvelope[InfluencerFilterOptions])
async def get_filter_options(
    request: Request,
    context: InfluencerLibraryReadContext,
    service: Annotated[InfluencerService, Depends(get_influencer_service)],
) -> dict[str, Any]:
    options = await _service_call(service.get_filter_options(context))
    return envelope(request, data=options)


@router.get(
    "/{influencer_id}/metric-snapshots",
    response_model=SuccessEnvelope[MetricSnapshotPage],
    dependencies=[Depends(reject_duplicate_snapshot_query_parameters)],
)
async def list_metric_snapshots(
    influencer_id: UUID,
    request: Request,
    context: InfluencerLibraryReadContext,
    service: Annotated[InfluencerService, Depends(get_influencer_service)],
    pagination: Annotated[InfluencerListQuery, Depends(parse_snapshot_pagination)],
) -> dict[str, Any]:
    snapshots = await _service_call(
        service.list_metric_snapshots(
            context,
            influencer_id,
            page=pagination.page,
            page_size=pagination.page_size,
        )
    )
    return envelope(request, data=snapshots)


@router.get("/{influencer_id}", response_model=SuccessEnvelope[InfluencerDetail])
async def get_influencer_detail(
    influencer_id: UUID,
    request: Request,
    context: InfluencerLibraryReadContext,
    service: Annotated[InfluencerService, Depends(get_influencer_service)],
) -> dict[str, Any]:
    detail = await _service_call(service.get_influencer_detail(context, influencer_id))
    return envelope(request, data=detail)

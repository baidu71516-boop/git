"""Closed HTTP adaptation for Phase 3A Campaign and Campaign Member operations."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from backend_core.auth import ALL_OF, EXACT, EffectiveAuthorizationContext, ModuleKey
from backend_core.campaigns.access import DepartmentScope
from backend_core.campaigns.schemas import (
    CampaignCreateInput,
    CampaignCursor,
    CampaignMemberBulkAddInput,
    CampaignMemberBulkAddResult,
    CampaignMemberCursor,
    CampaignMemberFromCandidateRunBulkAddInput,
    CampaignMemberPage,
    CampaignMemberRemoveInput,
    CampaignMemberResult,
    CampaignPage,
    CampaignResult,
    CampaignStatusTransitionInput,
    CampaignUpdateInput,
)
from backend_core.campaigns.service import CampaignService
from backend_core.growth.enums import (
    CampaignReviewMode,
    CampaignStatus,
    DuplicateHistoryPolicy,
)
from backend_core.outreach.schemas import OutreachTargetCreateInput, OutreachTargetResult
from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.http.dependencies import (
    get_client_ip,
    get_database_session,
    get_user_agent,
    require_module,
    require_module_write,
)
from app.http.outreach import OutreachTargetCreateRequest, get_outreach_service
from app.http.phase3a_http import (
    Phase3AHttpWrite,
    decode_keyset_cursor,
    domain_input_with_scope,
    encode_keyset_cursor,
    error_responses,
    reject_closed_query_parameters,
    require_single_idempotency_key,
)
from app.http.phase3a_scope import resolve_phase3a_department_scope
from app.http.responses import SuccessEnvelope, envelope

router = APIRouter(prefix="/api/v1/campaigns", tags=["campaigns"])
CampaignsReadContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module(EXACT(ModuleKey.CAMPAIGNS))),
]
CampaignsWriteContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(require_module_write(EXACT(ModuleKey.CAMPAIGNS))),
]
CampaignsFromCandidateRunWriteContext = Annotated[
    EffectiveAuthorizationContext,
    Depends(
        require_module_write(
            ALL_OF(ModuleKey.CAMPAIGNS, ModuleKey.CANDIDATE_POOLS),
        )
    ),
]

CAMPAIGN_LIST_QUERY_PARAMETERS = frozenset({"cursor", "limit"})
CAMPAIGN_MEMBER_LIST_QUERY_PARAMETERS = frozenset({"cursor", "limit"})

type PositiveStrictInt = Annotated[StrictInt, Field(ge=1)]


class CampaignCreateRequest(Phase3AHttpWrite):
    name: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    owner_operator_id: UUID | None = Field(
        default=None,
        description=(
            "Active owner in the resolved Department. Same-Department requests may omit it "
            "to default to the selected Operator; cross-Department Super Admin requests must "
            "provide a target-Department owner."
        ),
    )
    review_mode: CampaignReviewMode = CampaignReviewMode.FIRST_N
    review_count: PositiveStrictInt | None = 50
    duplicate_history_policy: DuplicateHistoryPolicy = DuplicateHistoryPolicy.ALLOW_WITH_WARNING
    duplicate_window_days: PositiveStrictInt | None = None


class CampaignUpdateRequest(Phase3AHttpWrite):
    """The lifecycle endpoint is the only public route that can change status."""

    name: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    owner_operator_id: UUID
    review_mode: CampaignReviewMode
    review_count: PositiveStrictInt | None
    duplicate_history_policy: DuplicateHistoryPolicy
    duplicate_window_days: PositiveStrictInt | None
    expected_version: PositiveStrictInt


class CampaignLifecycleRequest(Phase3AHttpWrite):
    to_status: CampaignStatus
    expected_version: PositiveStrictInt


class CampaignMemberPairRequest(Phase3AHttpWrite):
    influencer_id: UUID
    preferred_platform_account_id: UUID


class CampaignMemberBulkAddRequest(Phase3AHttpWrite):
    """Direct add: every Influencer is explicitly paired with one required account."""

    members: tuple[CampaignMemberPairRequest, ...] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def require_unique_influencers(self) -> CampaignMemberBulkAddRequest:
        influencer_ids = [member.influencer_id for member in self.members]
        if len(influencer_ids) != len(set(influencer_ids)):
            raise ValueError("influencer_id must be unique within a direct member add request")
        return self


class CampaignMemberFromCandidateRunRequest(Phase3AHttpWrite):
    """Strict explicit-or-ALL_MATCH source-run selection request."""

    run_id: UUID
    selection_mode: Literal["ALL_MATCH"] | None = None
    member_ids: tuple[UUID, ...] | None = Field(default=None, min_length=1, max_length=10_000)
    excluded_member_ids: tuple[UUID, ...] | None = Field(default=None, max_length=10_000)

    @model_validator(mode="after")
    def require_exactly_one_selection_shape(self) -> CampaignMemberFromCandidateRunRequest:
        if self.selection_mode == "ALL_MATCH":
            if self.member_ids is not None or self.excluded_member_ids is None:
                raise ValueError(
                    "ALL_MATCH requires excluded_member_ids and does not accept member_ids"
                )
            if len(self.excluded_member_ids) != len(set(self.excluded_member_ids)):
                raise ValueError("excluded_member_ids must be distinct")
            return self

        if self.member_ids is None or self.excluded_member_ids is not None:
            raise ValueError(
                "Explicit selection requires member_ids and does not accept excluded_member_ids"
            )
        if len(self.member_ids) != len(set(self.member_ids)):
            raise ValueError("member_ids must be distinct")
        return self


class CampaignMemberRemoveRequest(Phase3AHttpWrite):
    expected_version: PositiveStrictInt


class CampaignListResponse(BaseModel):
    """HTTP page whose string cursor serializes the frozen core sort tuple."""

    model_config = ConfigDict(extra="forbid")

    items: tuple[CampaignResult, ...]
    next_cursor: str | None = None


class CampaignMemberListResponse(BaseModel):
    """HTTP page for active Members only; point reads retain removed-row visibility."""

    model_config = ConfigDict(extra="forbid")

    items: tuple[CampaignMemberResult, ...]
    next_cursor: str | None = None


def get_campaign_service(
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CampaignService:
    return CampaignService(session)


def reject_campaign_list_query_parameters(request: Request) -> None:
    reject_closed_query_parameters(request, CAMPAIGN_LIST_QUERY_PARAMETERS)


def reject_campaign_member_list_query_parameters(request: Request) -> None:
    reject_closed_query_parameters(request, CAMPAIGN_MEMBER_LIST_QUERY_PARAMETERS)


@router.get(
    "",
    response_model=SuccessEnvelope[CampaignListResponse],
    dependencies=[Depends(reject_campaign_list_query_parameters)],
    responses=error_responses(401, 404, 409, 422),
)
async def list_campaigns(
    request: Request,
    context: CampaignsReadContext,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    service: Annotated[CampaignService, Depends(get_campaign_service)],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    page: CampaignPage = await service.list_campaigns(
        context,
        department_id=scope.department_id,
        cursor=decode_keyset_cursor(cursor, CampaignCursor),
        limit=limit,
    )
    return envelope(
        request,
        data=CampaignListResponse(
            items=page.items,
            next_cursor=encode_keyset_cursor(page.next_cursor),
        ),
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessEnvelope[CampaignResult],
    responses=error_responses(401, 403, 404, 409, 422),
)
async def create_campaign(
    payload: CampaignCreateRequest,
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: CampaignsWriteContext,
    response: Response,
    service: Annotated[CampaignService, Depends(get_campaign_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    result = await service.create_campaign(
        context,
        domain_input_with_scope(
            payload,
            CampaignCreateInput,
            department_id=scope.department_id,
        ),
        idempotency_key=require_single_idempotency_key(request, idempotency_key),
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    response.status_code = result.status_code
    return envelope(request, data=result.result)


@router.get(
    "/{campaign_id}",
    response_model=SuccessEnvelope[CampaignResult],
    responses=error_responses(401, 404, 422),
)
async def get_campaign(
    campaign_id: UUID,
    request: Request,
    context: CampaignsReadContext,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    service: Annotated[CampaignService, Depends(get_campaign_service)],
) -> dict[str, Any]:
    campaign = await service.get_campaign(
        context,
        campaign_id,
        department_id=scope.department_id,
    )
    return envelope(request, data=campaign)


@router.put(
    "/{campaign_id}",
    response_model=SuccessEnvelope[CampaignResult],
    responses=error_responses(401, 403, 404, 409, 422),
)
async def update_campaign(
    campaign_id: UUID,
    payload: CampaignUpdateRequest,
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: CampaignsWriteContext,
    service: Annotated[CampaignService, Depends(get_campaign_service)],
) -> dict[str, Any]:
    campaign = await service.update_campaign(
        context,
        campaign_id,
        domain_input_with_scope(
            payload,
            CampaignUpdateInput,
            department_id=scope.department_id,
        ),
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return envelope(request, data=campaign)


@router.post(
    "/{campaign_id}/lifecycle",
    response_model=SuccessEnvelope[CampaignResult],
    responses=error_responses(401, 403, 404, 409, 422),
)
async def transition_campaign_lifecycle(
    campaign_id: UUID,
    payload: CampaignLifecycleRequest,
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: CampaignsWriteContext,
    service: Annotated[CampaignService, Depends(get_campaign_service)],
) -> dict[str, Any]:
    campaign = await service.transition_campaign_status(
        context,
        campaign_id,
        domain_input_with_scope(
            payload,
            CampaignStatusTransitionInput,
            department_id=scope.department_id,
        ),
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return envelope(request, data=campaign)


@router.get(
    "/{campaign_id}/members",
    response_model=SuccessEnvelope[CampaignMemberListResponse],
    dependencies=[Depends(reject_campaign_member_list_query_parameters)],
    responses=error_responses(401, 404, 409, 422),
)
async def list_campaign_members(
    campaign_id: UUID,
    request: Request,
    context: CampaignsReadContext,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    service: Annotated[CampaignService, Depends(get_campaign_service)],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    page: CampaignMemberPage = await service.list_members(
        context,
        campaign_id,
        department_id=scope.department_id,
        cursor=decode_keyset_cursor(cursor, CampaignMemberCursor),
        limit=limit,
    )
    return envelope(
        request,
        data=CampaignMemberListResponse(
            items=page.items,
            next_cursor=encode_keyset_cursor(page.next_cursor),
        ),
    )


@router.post(
    "/{campaign_id}/members/bulk-add",
    response_model=SuccessEnvelope[CampaignMemberBulkAddResult],
    responses=error_responses(401, 403, 404, 409, 422),
)
async def bulk_add_campaign_members(
    campaign_id: UUID,
    payload: CampaignMemberBulkAddRequest,
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: CampaignsWriteContext,
    service: Annotated[CampaignService, Depends(get_campaign_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    result = await service.bulk_add_members(
        context,
        campaign_id,
        domain_input_with_scope(
            payload,
            CampaignMemberBulkAddInput,
            department_id=scope.department_id,
        ),
        idempotency_key=require_single_idempotency_key(request, idempotency_key),
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return envelope(request, data=result.result)


@router.post(
    "/{campaign_id}/members/from-candidate-run",
    response_model=SuccessEnvelope[CampaignMemberBulkAddResult],
    responses=error_responses(401, 403, 404, 409, 422),
)
async def bulk_add_campaign_members_from_candidate_run(
    campaign_id: UUID,
    payload: CampaignMemberFromCandidateRunRequest,
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: CampaignsFromCandidateRunWriteContext,
    service: Annotated[CampaignService, Depends(get_campaign_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    result = await service.bulk_add_members_from_candidate_run(
        context,
        campaign_id,
        domain_input_with_scope(
            payload,
            CampaignMemberFromCandidateRunBulkAddInput,
            department_id=scope.department_id,
        ),
        idempotency_key=require_single_idempotency_key(request, idempotency_key),
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return envelope(request, data=result.result)


@router.post(
    "/{campaign_id}/members/{member_id}/remove",
    response_model=SuccessEnvelope[CampaignMemberResult],
    responses=error_responses(401, 403, 404, 409, 422),
)
async def remove_campaign_member(
    campaign_id: UUID,
    member_id: UUID,
    payload: CampaignMemberRemoveRequest,
    request: Request,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: CampaignsWriteContext,
    service: Annotated[CampaignService, Depends(get_campaign_service)],
) -> dict[str, Any]:
    member = await service.remove_member(
        context,
        campaign_id,
        member_id,
        domain_input_with_scope(
            payload,
            CampaignMemberRemoveInput,
            department_id=scope.department_id,
        ),
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return envelope(request, data=member)


@router.post(
    "/{campaign_id}/outreach-targets",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessEnvelope[OutreachTargetResult],
    responses=error_responses(401, 403, 404, 409, 422),
)
async def create_campaign_outreach_target(
    campaign_id: UUID,
    payload: OutreachTargetCreateRequest,
    request: Request,
    response: Response,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    context: CampaignsWriteContext,
    service: Annotated[Any, Depends(get_outreach_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    result = await service.create_target(
        context,
        campaign_id,
        domain_input_with_scope(
            payload,
            OutreachTargetCreateInput,
            department_id=scope.department_id,
        ),
        idempotency_key=require_single_idempotency_key(request, idempotency_key),
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    response.status_code = result.status_code
    return envelope(request, data=result.result)


@router.get(
    "/{campaign_id}/members/{member_id}",
    response_model=SuccessEnvelope[CampaignMemberResult],
    responses=error_responses(401, 404, 422),
)
async def get_campaign_member(
    campaign_id: UUID,
    member_id: UUID,
    request: Request,
    context: CampaignsReadContext,
    scope: Annotated[DepartmentScope, Depends(resolve_phase3a_department_scope)],
    service: Annotated[CampaignService, Depends(get_campaign_service)],
) -> dict[str, Any]:
    member = await service.get_member(
        context,
        campaign_id,
        member_id,
        department_id=scope.department_id,
    )
    return envelope(request, data=member)


__all__ = [
    "CampaignCreateRequest",
    "CampaignLifecycleRequest",
    "CampaignListResponse",
    "CampaignMemberBulkAddRequest",
    "CampaignMemberFromCandidateRunRequest",
    "CampaignMemberListResponse",
    "CampaignMemberRemoveRequest",
    "CampaignMemberPairRequest",
    "CampaignUpdateRequest",
    "get_campaign_service",
    "router",
]

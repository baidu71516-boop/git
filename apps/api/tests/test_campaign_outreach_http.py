"""HTTP contract coverage for the closed Phase 3A Campaign and Outreach routers."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from app.http.campaigns import get_campaign_service
from app.http.dependencies import get_auth_service, require_auth
from app.http.outreach import get_outreach_service
from app.http.phase3a_scope import resolve_phase3a_department_scope
from app.main import app
from backend_core.auth import AuthContext, AuthError
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, Operator
from backend_core.campaigns.access import DepartmentScope
from backend_core.campaigns.errors import CampaignOutreachError
from backend_core.campaigns.schemas import (
    CampaignCreateInput,
    CampaignCursor,
    CampaignMemberBulkAddInput,
    CampaignMemberBulkAddResult,
    CampaignMemberCursor,
    CampaignMemberFromCandidateRunBulkAddInput,
    CampaignMemberPage,
    CampaignMemberResult,
    CampaignOwnerSummary,
    CampaignPage,
    CampaignResult,
)
from backend_core.campaigns.service import MutationResult
from backend_core.config import get_settings
from backend_core.growth.enums import (
    CampaignReviewMode,
    CampaignStatus,
    DuplicateHistoryPolicy,
)
from backend_core.influencers.enums import InfluencerStatus, Platform
from backend_core.influencers.schemas import (
    InfluencerIdentitySummary,
    PlatformAccountIdentitySummary,
)
from backend_core.outreach.enums import (
    OutreachActorType,
    OutreachChannel,
    OutreachEventType,
    OutreachPriority,
    OutreachPrioritySource,
    OutreachTaskKind,
    OutreachTaskState,
)
from backend_core.outreach.schemas import (
    OutreachEventResult,
    OutreachTargetResult,
    OutreachTaskCreateResult,
    OutreachTaskResult,
    OutreachTransitionResult,
)
from httpx import ASGITransport, AsyncClient, Response

NOW = datetime(2026, 8, 18, 9, 30, tzinfo=UTC)
CAMPAIGN_ID = UUID("00000000-0000-0000-0000-000000000501")
OTHER_CAMPAIGN_ID = UUID("00000000-0000-0000-0000-000000000502")
STALE_CAMPAIGN_ID = UUID("00000000-0000-0000-0000-000000000503")
MEMBER_ID = UUID("00000000-0000-0000-0000-000000000504")
INFLUENCER_ID = UUID("00000000-0000-0000-0000-000000000505")
ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000506")
RUN_ID = UUID("00000000-0000-0000-0000-000000000507")
RUN_MEMBER_ID = UUID("00000000-0000-0000-0000-000000000508")
TARGET_ID = UUID("00000000-0000-0000-0000-000000000509")
TASK_ID = UUID("00000000-0000-0000-0000-000000000510")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000511")
ALL_MATCH_CONFLICT_MEMBER_ID = UUID("00000000-0000-0000-0000-000000000512")
ALL_MATCH_CONFLICT_ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000513")
ALL_MATCH_CONFLICT_ENTITY_ID = UUID("00000000-0000-0000-0000-000000000514")


def _context() -> AuthContext:
    department = Department(
        id=uuid4(),
        name=f"Campaign HTTP {uuid4().hex}",
        password_hash="not-used-by-http-tests",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    operator = Operator(
        id=uuid4(),
        department_id=department.id,
        name="Campaign operator",
        role=Role.OPERATOR,
        status=OperatorStatus.ACTIVE,
    )
    return AuthContext(
        department=department,
        operator=operator,
        role=Role.OPERATOR,
        auth_session=AuthSession(
            id=uuid4(),
            department_id=department.id,
            operator_id=operator.id,
            token_hash="a" * 64,
            csrf_token_hash="b" * 64,
            ip="192.0.2.51",
            user_agent="campaign-http-test",
            expires_at=NOW + timedelta(hours=1),
            revoked_at=None,
        ),
    )


def _campaign(context: AuthContext, *, campaign_id: UUID = CAMPAIGN_ID) -> CampaignResult:
    assert context.operator is not None
    return CampaignResult(
        id=campaign_id,
        department_id=context.department.id,
        owner_operator_id=context.operator.id,
        owner=CampaignOwnerSummary(
            id=context.operator.id,
            name=context.operator.name,
            status=context.operator.status,
        ),
        created_by_operator_id=context.operator.id,
        name="Autumn launch",
        status=CampaignStatus.DRAFT,
        review_mode=CampaignReviewMode.FIRST_N,
        review_count=50,
        duplicate_history_policy=DuplicateHistoryPolicy.ALLOW_WITH_WARNING,
        duplicate_window_days=None,
        version=3,
        created_at=NOW,
        updated_at=NOW,
    )


def _member(context: AuthContext) -> CampaignMemberResult:
    assert context.operator is not None
    return CampaignMemberResult(
        id=MEMBER_ID,
        department_id=context.department.id,
        campaign_id=CAMPAIGN_ID,
        influencer_id=INFLUENCER_ID,
        preferred_platform_account_id=ACCOUNT_ID,
        source_pool_run_id=None,
        added_by_operator_id=context.operator.id,
        removed_at=None,
        version=2,
        created_at=NOW,
        updated_at=NOW,
        influencer=InfluencerIdentitySummary(
            id=INFLUENCER_ID,
            display_name="Campaign member influencer",
            status=InfluencerStatus.ACTIVE,
        ),
        preferred_platform_account=PlatformAccountIdentitySummary(
            id=ACCOUNT_ID,
            platform=Platform.XIAOHONGSHU,
            platform_account_id="campaign-member-account",
            account_name="Campaign member account",
            account_handle="campaign-member-handle",
            is_active=True,
        ),
        is_active=True,
    )


def _target(context: AuthContext) -> OutreachTargetResult:
    return OutreachTargetResult(
        id=TARGET_ID,
        department_id=context.department.id,
        campaign_id=CAMPAIGN_ID,
        member_id=MEMBER_ID,
        influencer_id=INFLUENCER_ID,
        channel=OutreachChannel.EMAIL,
        contact_id=uuid4(),
        platform_account_id=None,
        version=2,
        masked_display="***@example.test",
    )


def _task(context: AuthContext) -> OutreachTaskResult:
    return OutreachTaskResult(
        id=TASK_ID,
        department_id=context.department.id,
        campaign_id=CAMPAIGN_ID,
        outreach_target_id=TARGET_ID,
        kind=OutreachTaskKind.FIRST_TOUCH,
        step_key="initial",
        state=OutreachTaskState.READY,
        priority=OutreachPriority.NORMAL,
        priority_source=OutreachPrioritySource.DEFAULT,
        priority_reason_codes=None,
        assigned_operator_id=None,
        due_at=NOW,
        template_version_id=None,
        version=2,
        created_at=NOW,
        updated_at=NOW,
    )


class FakeAuthService:
    def validate_csrf(self, _context: AuthContext, csrf_token: str | None) -> None:
        if csrf_token != "campaign-csrf":
            raise AuthError(403, "CSRF_FAILED", "CSRF validation failed")


class FakeCampaignService:
    def __init__(self, context: AuthContext) -> None:
        self.context = context
        self.calls: list[tuple[str, object]] = []

    async def list_campaigns(
        self,
        _context: AuthContext,
        *,
        cursor: CampaignCursor | None,
        limit: int,
        department_id: UUID,
    ) -> CampaignPage:
        self.calls.append(("list_campaigns", (cursor, limit, department_id)))
        return CampaignPage(
            items=(_campaign(self.context),),
            next_cursor=CampaignCursor(updated_at=NOW, id=CAMPAIGN_ID),
        )

    async def get_campaign(
        self,
        _context: AuthContext,
        campaign_id: UUID,
        *,
        department_id: UUID,
    ) -> CampaignResult:
        self.calls.append(("get_campaign", (campaign_id, department_id)))
        return _campaign(self.context, campaign_id=campaign_id)

    async def create_campaign(
        self,
        _context: AuthContext,
        payload: object,
        **kwargs: object,
    ) -> object:
        self.calls.append(("create_campaign", (payload, kwargs)))
        return MutationResult(status_code=201, result=_campaign(self.context))

    async def update_campaign(
        self,
        _context: AuthContext,
        campaign_id: UUID,
        payload: object,
        **kwargs: object,
    ) -> CampaignResult:
        self.calls.append(("update_campaign", (campaign_id, payload, kwargs)))
        if campaign_id == STALE_CAMPAIGN_ID:
            raise CampaignOutreachError(
                409,
                "VERSION_CONFLICT",
                "Campaign version is stale",
                current_version=9,
                entity_id=uuid4(),
            )
        return _campaign(self.context, campaign_id=campaign_id)

    async def transition_campaign_status(
        self,
        _context: AuthContext,
        campaign_id: UUID,
        payload: object,
        **kwargs: object,
    ) -> CampaignResult:
        self.calls.append(("transition_campaign_status", (campaign_id, payload, kwargs)))
        return _campaign(self.context, campaign_id=campaign_id)

    async def list_members(
        self,
        _context: AuthContext,
        campaign_id: UUID,
        *,
        cursor: CampaignMemberCursor | None,
        limit: int,
        department_id: UUID,
    ) -> CampaignMemberPage:
        self.calls.append(("list_members", (campaign_id, cursor, limit, department_id)))
        if cursor is not None and cursor.campaign_id != campaign_id:
            raise CampaignOutreachError(
                409,
                "CURSOR_MISMATCH",
                "Campaign member cursor does not match the resolved scope",
            )
        return CampaignMemberPage(
            items=(_member(self.context),),
            next_cursor=CampaignMemberCursor(
                created_at=NOW,
                id=MEMBER_ID,
                department_id=department_id,
                campaign_id=campaign_id,
            ),
        )

    async def bulk_add_members(
        self,
        _context: AuthContext,
        campaign_id: UUID,
        payload: object,
        **kwargs: object,
    ) -> MutationResult[CampaignMemberBulkAddResult]:
        self.calls.append(("bulk_add_members", (campaign_id, payload, kwargs)))
        return MutationResult(
            status_code=200,
            result=CampaignMemberBulkAddResult(
                campaign_id=campaign_id,
                source_pool_run_id=None,
                requested_count=1,
                added_count=1,
                restored_count=0,
                already_active_count=0,
                active_count_after=1,
            ),
        )

    async def bulk_add_members_from_candidate_run(
        self,
        _context: AuthContext,
        campaign_id: UUID,
        payload: object,
        **kwargs: object,
    ) -> MutationResult[CampaignMemberBulkAddResult]:
        self.calls.append(("bulk_add_members_from_candidate_run", (campaign_id, payload, kwargs)))
        if (
            isinstance(payload, CampaignMemberFromCandidateRunBulkAddInput)
            and payload.selection_mode == "ALL_MATCH"
            and payload.excluded_member_ids
        ):
            raise CampaignOutreachError(
                422,
                "CANDIDATE_POOL_RUN_MEMBER_AMBIGUOUS",
                "ALL_MATCH cannot choose multiple platform accounts for one influencer; "
                "exclude one account and retry",
                entity_id=ALL_MATCH_CONFLICT_ENTITY_ID,
                safe_details={
                    "conflicting_influencer": {
                        "id": str(INFLUENCER_ID),
                        "display_name": "Campaign member influencer",
                    },
                    "conflicting_member_ids": [str(RUN_MEMBER_ID)],
                    "conflicting_accounts": [
                        {
                            "member_id": str(RUN_MEMBER_ID),
                            "platform_account_id": str(ALL_MATCH_CONFLICT_ACCOUNT_ID),
                            "platform": "xiaohongshu",
                            "account_name": "Campaign member account",
                            "account_handle": "campaign-member-handle",
                        }
                    ],
                },
            )
        return MutationResult(
            status_code=200,
            result=CampaignMemberBulkAddResult(
                campaign_id=campaign_id,
                source_pool_run_id=RUN_ID,
                requested_count=1,
                added_count=1,
                restored_count=0,
                already_active_count=0,
                active_count_after=1,
            ),
        )

    async def remove_member(
        self,
        _context: AuthContext,
        campaign_id: UUID,
        member_id: UUID,
        payload: object,
        **kwargs: object,
    ) -> CampaignMemberResult:
        self.calls.append(("remove_member", (campaign_id, member_id, payload, kwargs)))
        return _member(self.context).model_copy(
            update={"removed_at": NOW, "version": 3, "is_active": False}
        )

    async def get_member(
        self,
        _context: AuthContext,
        campaign_id: UUID,
        member_id: UUID,
        *,
        department_id: UUID,
    ) -> CampaignMemberResult:
        self.calls.append(("get_member", (campaign_id, member_id, department_id)))
        return _member(self.context)


class FakeOutreachService:
    def __init__(self, context: AuthContext) -> None:
        self.context = context
        self.calls: list[tuple[str, object]] = []

    async def create_target(
        self,
        _context: AuthContext,
        campaign_id: UUID,
        payload: object,
        **kwargs: object,
    ) -> MutationResult[OutreachTargetResult]:
        self.calls.append(("create_target", (campaign_id, payload, kwargs)))
        return MutationResult(status_code=201, result=_target(self.context))

    async def get_target(
        self,
        _context: AuthContext,
        target_id: UUID,
        *,
        department_id: UUID,
    ) -> OutreachTargetResult:
        self.calls.append(("get_target", (target_id, department_id)))
        return _target(self.context)

    async def update_target(
        self,
        _context: AuthContext,
        target_id: UUID,
        payload: object,
        **kwargs: object,
    ) -> OutreachTargetResult:
        self.calls.append(("update_target", (target_id, payload, kwargs)))
        return _target(self.context)

    async def create_task(
        self,
        _context: AuthContext,
        target_id: UUID,
        payload: object,
        **kwargs: object,
    ) -> MutationResult[OutreachTaskCreateResult]:
        self.calls.append(("create_task", (target_id, payload, kwargs)))
        return MutationResult(
            status_code=201,
            result=OutreachTaskCreateResult(task=_task(self.context)),
        )

    async def get_task(
        self,
        _context: AuthContext,
        task_id: UUID,
        *,
        department_id: UUID,
    ) -> OutreachTaskResult:
        self.calls.append(("get_task", (task_id, department_id)))
        return _task(self.context)

    async def transition_task(
        self,
        _context: AuthContext,
        task_id: UUID,
        payload: object,
        **kwargs: object,
    ) -> OutreachTransitionResult:
        self.calls.append(("transition_task", (task_id, payload, kwargs)))
        return OutreachTransitionResult(
            task_id=task_id,
            state=OutreachTaskState.SENT,
            version=3,
            event_id=EVENT_ID,
            replayed=False,
        )

    async def list_task_events(
        self,
        _context: AuthContext,
        task_id: UUID,
        *,
        department_id: UUID,
    ) -> list[OutreachEventResult]:
        self.calls.append(("list_task_events", (task_id, department_id)))
        return [
            OutreachEventResult(
                id=EVENT_ID,
                task_id=task_id,
                campaign_id=CAMPAIGN_ID,
                influencer_id=INFLUENCER_ID,
                channel=OutreachChannel.EMAIL,
                event_type=OutreachEventType.OUTREACH_SENT,
                actor_type=OutreachActorType.OPERATOR,
                actor_operator_id=self.context.operator.id,
                occurred_at=NOW,
                from_state=OutreachTaskState.READY,
                to_state=OutreachTaskState.SENT,
                reason_code=None,
            )
        ]


def _install_overrides(
    context: AuthContext,
    campaign_service: FakeCampaignService,
    outreach_service: FakeOutreachService,
) -> None:
    app.dependency_overrides[require_auth] = lambda: context
    app.dependency_overrides[get_auth_service] = FakeAuthService
    app.dependency_overrides[resolve_phase3a_department_scope] = lambda: DepartmentScope(
        department_id=context.department.id,
        cross_department_override=False,
    )
    app.dependency_overrides[get_campaign_service] = lambda: campaign_service
    app.dependency_overrides[get_outreach_service] = lambda: outreach_service


def _mutation_headers(idempotency_key: str) -> dict[str, str]:
    return {
        "Idempotency-Key": idempotency_key,
        "X-CSRF-Token": "campaign-csrf",
        "User-Agent": "campaign-http-test",
    }


def _assert_error(response: Response, *, status_code: int, code: str) -> dict[str, object]:
    body = response.json()
    assert response.status_code == status_code
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == code
    assert body["request_id"]
    return body


def _campaign_update_payload() -> dict[str, object]:
    return {
        "name": "Autumn launch",
        "owner_operator_id": str(uuid4()),
        "review_mode": "FIRST_N",
        "review_count": 50,
        "duplicate_history_policy": "ALLOW_WITH_WARNING",
        "duplicate_window_days": None,
        "expected_version": 3,
    }


def test_campaign_and_outreach_http_adapt_closed_domain_routes() -> None:
    async def scenario() -> None:
        context = _context()
        campaign_service = FakeCampaignService(context)
        outreach_service = FakeOutreachService(context)
        _install_overrides(context, campaign_service, outreach_service)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                settings = get_settings()
                client.cookies.set(settings.csrf_cookie_name, "campaign-csrf")

                listed = await client.get("/api/v1/campaigns?limit=1")
                assert listed.status_code == 200
                cursor = listed.json()["data"]["next_cursor"]
                assert isinstance(cursor, str)
                continued = await client.get(f"/api/v1/campaigns?limit=1&cursor={cursor}")
                assert continued.status_code == 200
                assert campaign_service.calls[1][0] == "list_campaigns"
                assert campaign_service.calls[1][1][0] == CampaignCursor(
                    updated_at=NOW,
                    id=CAMPAIGN_ID,
                )

                created = await client.post(
                    "/api/v1/campaigns",
                    json={"name": "Autumn launch"},
                    headers=_mutation_headers("campaign-create"),
                )
                assert created.status_code == 201
                create_payload = campaign_service.calls[-1][1][0]
                assert isinstance(create_payload, CampaignCreateInput)
                assert create_payload.department_id == context.department.id

                updated = await client.put(
                    f"/api/v1/campaigns/{CAMPAIGN_ID}",
                    json=_campaign_update_payload(),
                    headers={"X-CSRF-Token": "campaign-csrf"},
                )
                assert updated.status_code == 200

                lifecycle = await client.post(
                    f"/api/v1/campaigns/{CAMPAIGN_ID}/lifecycle",
                    json={"to_status": "ACTIVE", "expected_version": 3},
                    headers={"X-CSRF-Token": "campaign-csrf"},
                )
                assert lifecycle.status_code == 200

                duplicate_direct = await client.post(
                    f"/api/v1/campaigns/{CAMPAIGN_ID}/members/bulk-add",
                    json={
                        "members": [
                            {
                                "influencer_id": str(INFLUENCER_ID),
                                "preferred_platform_account_id": str(ACCOUNT_ID),
                            },
                            {
                                "influencer_id": str(INFLUENCER_ID),
                                "preferred_platform_account_id": str(ACCOUNT_ID),
                            },
                        ]
                    },
                    headers=_mutation_headers("campaign-direct-duplicate"),
                )
                _assert_error(duplicate_direct, status_code=422, code="VALIDATION_ERROR")

                direct = await client.post(
                    f"/api/v1/campaigns/{CAMPAIGN_ID}/members/bulk-add",
                    json={
                        "members": [
                            {
                                "influencer_id": str(INFLUENCER_ID),
                                "preferred_platform_account_id": str(ACCOUNT_ID),
                            }
                        ]
                    },
                    headers=_mutation_headers("campaign-direct"),
                )
                assert direct.status_code == 200
                direct_payload = campaign_service.calls[-1][1][1]
                assert isinstance(direct_payload, CampaignMemberBulkAddInput)
                # The compatibility field remains internal, but the closed
                # direct HTTP DTO never supplies provenance.
                assert direct_payload.source_pool_run_id is None

                selected = await client.post(
                    f"/api/v1/campaigns/{CAMPAIGN_ID}/members/from-candidate-run",
                    json={"run_id": str(RUN_ID), "member_ids": [str(RUN_MEMBER_ID)]},
                    headers=_mutation_headers("campaign-selected-run"),
                )
                assert selected.status_code == 200
                selected_payload = campaign_service.calls[-1][1][1]
                assert isinstance(selected_payload, CampaignMemberFromCandidateRunBulkAddInput)
                assert selected_payload.run_id == RUN_ID
                assert selected_payload.selection_mode is None
                assert selected_payload.member_ids == (RUN_MEMBER_ID,)
                assert selected_payload.excluded_member_ids is None

                all_match = await client.post(
                    f"/api/v1/campaigns/{CAMPAIGN_ID}/members/from-candidate-run",
                    json={
                        "run_id": str(RUN_ID),
                        "selection_mode": "ALL_MATCH",
                        "excluded_member_ids": [],
                    },
                    headers=_mutation_headers("campaign-all-match"),
                )
                assert all_match.status_code == 200
                all_match_payload = campaign_service.calls[-1][1][1]
                assert isinstance(all_match_payload, CampaignMemberFromCandidateRunBulkAddInput)
                assert all_match_payload.run_id == RUN_ID
                assert all_match_payload.selection_mode == "ALL_MATCH"
                assert all_match_payload.member_ids is None
                assert all_match_payload.excluded_member_ids == ()

                members = await client.get(f"/api/v1/campaigns/{CAMPAIGN_ID}/members?limit=1")
                assert members.status_code == 200
                member_data = members.json()["data"]["items"][0]
                assert member_data["influencer"] == {
                    "id": str(INFLUENCER_ID),
                    "display_name": "Campaign member influencer",
                    "status": "active",
                }
                assert member_data["preferred_platform_account"]["id"] == str(ACCOUNT_ID)
                assert member_data["is_active"] is True
                assert "contacts" not in member_data
                assert "total" not in members.json()["data"]
                member_cursor = members.json()["data"]["next_cursor"]
                cursor_mismatch = await client.get(
                    f"/api/v1/campaigns/{OTHER_CAMPAIGN_ID}/members?cursor={member_cursor}"
                )
                _assert_error(cursor_mismatch, status_code=409, code="CURSOR_MISMATCH")

                member = await client.get(f"/api/v1/campaigns/{CAMPAIGN_ID}/members/{MEMBER_ID}")
                assert member.status_code == 200
                removed = await client.post(
                    f"/api/v1/campaigns/{CAMPAIGN_ID}/members/{MEMBER_ID}/remove",
                    json={"expected_version": 2},
                    headers={"X-CSRF-Token": "campaign-csrf"},
                )
                assert removed.status_code == 200
                removed_data = removed.json()["data"]
                assert removed_data["influencer"]["id"] == str(INFLUENCER_ID)
                assert removed_data["preferred_platform_account"]["id"] == str(ACCOUNT_ID)
                assert removed_data["is_active"] is False

                target_created = await client.post(
                    f"/api/v1/campaigns/{CAMPAIGN_ID}/outreach-targets",
                    json={
                        "member_id": str(MEMBER_ID),
                        "influencer_id": str(INFLUENCER_ID),
                        "channel": "EMAIL",
                        "contact_id": str(uuid4()),
                    },
                    headers=_mutation_headers("target-create"),
                )
                assert target_created.status_code == 201

                target = await client.get(f"/api/v1/outreach-targets/{TARGET_ID}")
                assert target.status_code == 200
                target_updated = await client.put(
                    f"/api/v1/outreach-targets/{TARGET_ID}",
                    json={"contact_id": str(uuid4()), "expected_version": 2},
                    headers={"X-CSRF-Token": "campaign-csrf"},
                )
                assert target_updated.status_code == 200

                task_created = await client.post(
                    f"/api/v1/outreach-targets/{TARGET_ID}/tasks",
                    json={"due_at": NOW.isoformat()},
                    headers=_mutation_headers("task-create"),
                )
                assert task_created.status_code == 201
                task = await client.get(f"/api/v1/outreach-tasks/{TASK_ID}")
                assert task.status_code == 200
                transitioned = await client.post(
                    f"/api/v1/outreach-tasks/{TASK_ID}/transitions",
                    json={"to_state": "SENT", "expected_version": 2},
                    headers=_mutation_headers("task-transition"),
                )
                assert transitioned.status_code == 200
                events = await client.get(f"/api/v1/outreach-tasks/{TASK_ID}/events")
                assert events.status_code == 200
                assert events.json()["data"][0]["id"] == str(EVENT_ID)
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_put_boundaries_and_safe_campaign_outreach_errors() -> None:
    async def scenario() -> None:
        context = _context()
        campaign_service = FakeCampaignService(context)
        outreach_service = FakeOutreachService(context)
        _install_overrides(context, campaign_service, outreach_service)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                settings = get_settings()
                client.cookies.set(settings.csrf_cookie_name, "campaign-csrf")

                campaign_status = await client.put(
                    f"/api/v1/campaigns/{CAMPAIGN_ID}",
                    json={**_campaign_update_payload(), "status": "ACTIVE"},
                    headers={"X-CSRF-Token": "campaign-csrf"},
                )
                _assert_error(campaign_status, status_code=422, code="VALIDATION_ERROR")
                assert not campaign_service.calls

                target_identity = await client.put(
                    f"/api/v1/outreach-targets/{TARGET_ID}",
                    json={
                        "contact_id": str(uuid4()),
                        "campaign_id": str(CAMPAIGN_ID),
                        "expected_version": 2,
                    },
                    headers={"X-CSRF-Token": "campaign-csrf"},
                )
                _assert_error(target_identity, status_code=422, code="VALIDATION_ERROR")
                assert not outreach_service.calls

                stale = await client.put(
                    f"/api/v1/campaigns/{STALE_CAMPAIGN_ID}",
                    json=_campaign_update_payload(),
                    headers={"X-CSRF-Token": "campaign-csrf"},
                )
                body = _assert_error(stale, status_code=409, code="VERSION_CONFLICT")
                assert body["error"]["details"] == {"current_version": 9}

                duplicate_key = await client.post(
                    "/api/v1/campaigns",
                    json={"name": "Autumn launch"},
                    headers=[
                        ("Idempotency-Key", "first"),
                        ("Idempotency-Key", "second"),
                        ("X-CSRF-Token", "campaign-csrf"),
                    ],
                )
                _assert_error(duplicate_key, status_code=422, code="IDEMPOTENCY_KEY_INVALID")
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_all_match_http_contract_is_strict_and_exposes_only_safe_conflict_details() -> None:
    async def scenario() -> None:
        context = _context()
        campaign_service = FakeCampaignService(context)
        outreach_service = FakeOutreachService(context)
        _install_overrides(context, campaign_service, outreach_service)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                settings = get_settings()
                client.cookies.set(settings.csrf_cookie_name, "campaign-csrf")
                endpoint = f"/api/v1/campaigns/{CAMPAIGN_ID}/members/from-candidate-run"
                invalid_payloads = (
                    {"run_id": str(RUN_ID)},
                    {"run_id": str(RUN_ID), "excluded_member_ids": []},
                    {"run_id": str(RUN_ID), "selection_mode": "ALL_MATCH"},
                    {
                        "run_id": str(RUN_ID),
                        "selection_mode": "ALL_MATCH",
                        "member_ids": [str(RUN_MEMBER_ID)],
                        "excluded_member_ids": [],
                    },
                    {
                        "run_id": str(RUN_ID),
                        "selection_mode": "ALL_MATCH",
                        "excluded_member_ids": [
                            str(RUN_MEMBER_ID),
                            str(RUN_MEMBER_ID),
                        ],
                    },
                )
                for index, payload in enumerate(invalid_payloads):
                    invalid = await client.post(
                        endpoint,
                        json=payload,
                        headers=_mutation_headers(f"invalid-all-match-{index}"),
                    )
                    _assert_error(invalid, status_code=422, code="VALIDATION_ERROR")
                assert not campaign_service.calls

                conflict = await client.post(
                    endpoint,
                    json={
                        "run_id": str(RUN_ID),
                        "selection_mode": "ALL_MATCH",
                        "excluded_member_ids": [str(ALL_MATCH_CONFLICT_MEMBER_ID)],
                    },
                    headers=_mutation_headers("all-match-conflict"),
                )
                body = _assert_error(
                    conflict,
                    status_code=422,
                    code="CANDIDATE_POOL_RUN_MEMBER_AMBIGUOUS",
                )
                details = cast(dict[str, object], body["error"])["details"]
                assert details == {
                    "conflicting_influencer": {
                        "id": str(INFLUENCER_ID),
                        "display_name": "Campaign member influencer",
                    },
                    "conflicting_member_ids": [str(RUN_MEMBER_ID)],
                    "conflicting_accounts": [
                        {
                            "member_id": str(RUN_MEMBER_ID),
                            "platform_account_id": str(ALL_MATCH_CONFLICT_ACCOUNT_ID),
                            "platform": "xiaohongshu",
                            "account_name": "Campaign member account",
                            "account_handle": "campaign-member-handle",
                        }
                    ],
                }
                assert str(ALL_MATCH_CONFLICT_ENTITY_ID) not in json.dumps(body)
                assert "contact" not in json.dumps(details).lower()
                assert len(campaign_service.calls) == 1
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def _resolve_ref(document: dict[str, object], schema: dict[str, object]) -> dict[str, object]:
    while "$ref" in schema:
        reference = str(schema["$ref"])
        schema = document["components"]["schemas"][reference.rsplit("/", maxsplit=1)[-1]]
    return schema


def test_campaign_outreach_openapi_is_typed_and_keeps_public_mutation_boundaries() -> None:
    document = app.openapi()
    paths = document["paths"]
    assert "/api/v1/campaigns/{campaign_id}/members/from-candidate-run" in paths
    assert "/api/v1/outreach-tasks/{task_id}" in paths

    campaign_update_schema = _resolve_ref(
        document,
        paths["/api/v1/campaigns/{campaign_id}"]["put"]["requestBody"]["content"][
            "application/json"
        ]["schema"],
    )
    campaign_update_fields = campaign_update_schema["properties"]
    assert "status" not in campaign_update_fields
    assert "department_id" not in campaign_update_fields

    candidate_run_add_schema = _resolve_ref(
        document,
        paths["/api/v1/campaigns/{campaign_id}/members/from-candidate-run"]["post"]["requestBody"][
            "content"
        ]["application/json"]["schema"],
    )
    assert candidate_run_add_schema["required"] == ["run_id"]
    assert set(candidate_run_add_schema["properties"]) == {
        "run_id",
        "selection_mode",
        "member_ids",
        "excluded_member_ids",
    }

    target_update_schema = _resolve_ref(
        document,
        paths["/api/v1/outreach-targets/{target_id}"]["put"]["requestBody"]["content"][
            "application/json"
        ]["schema"],
    )
    target_update_fields = target_update_schema["properties"]
    for immutable_field in (
        "department_id",
        "campaign_id",
        "member_id",
        "influencer_id",
        "channel",
    ):
        assert immutable_field not in target_update_fields

    target_parameters = paths["/api/v1/outreach-targets/{target_id}"]["get"]["parameters"]
    department_header = next(
        parameter for parameter in target_parameters if parameter["name"] == "X-Department-ID"
    )
    assert department_header["in"] == "header"
    assert department_header["required"] is False
    assert "Super Admin" in department_header["description"]

    owner_summary = document["components"]["schemas"]["CampaignOwnerSummary"]
    assert owner_summary["required"] == ["id", "name", "status"]
    campaign_result = document["components"]["schemas"]["CampaignResult"]
    assert "owner" in campaign_result["required"]
    assert campaign_result["properties"]["owner"]["$ref"].endswith("/CampaignOwnerSummary")

    member_result = document["components"]["schemas"]["CampaignMemberResult"]
    assert {"influencer", "preferred_platform_account", "is_active"} <= set(
        member_result["required"]
    )
    assert member_result["properties"]["influencer"]["$ref"].endswith("/InfluencerIdentitySummary")
    assert member_result["properties"]["preferred_platform_account"]["$ref"].endswith(
        "/PlatformAccountIdentitySummary"
    )
    account_identity = document["components"]["schemas"]["PlatformAccountIdentitySummary"]
    assert set(account_identity["required"]) == {
        "id",
        "platform",
        "platform_account_id",
        "account_name",
        "account_handle",
        "is_active",
    }

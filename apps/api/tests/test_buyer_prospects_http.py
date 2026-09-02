"""Focused HTTP coverage for closed Market Prospect Rule V1 routes."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.http.candidate_pools import get_candidate_pool_service
from app.http.dependencies import (
    get_auth_service,
    get_targeting_task_dispatcher,
    require_auth,
)
from app.main import app
from backend_core.auth import (
    AuthContext,
    AuthError,
    EffectiveAuthorizationContext,
    ModuleKey,
    ResolvedDepartmentScope,
)
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, Operator
from backend_core.campaigns.schemas import CampaignOwnerSummary
from backend_core.config import get_settings
from backend_core.growth.enums import (
    BuyerLeadTier,
    BuyerProspectOwnerFilter,
    BuyerProspectRecentCollectionWindow,
    CandidatePoolRunStatus,
    CandidatePoolStatus,
)
from backend_core.growth.schemas import (
    BuyerProspectRuleCreateInput,
    BuyerProspectRuleLifecycleInput,
    BuyerProspectRuleOperatorOption,
    BuyerProspectRuleOptionsPublic,
    BuyerProspectRulePage,
    BuyerProspectRulePublic,
    BuyerProspectRuleSourceOption,
    BuyerProspectRuleTaxonomyOption,
    BuyerProspectRuleUpdateInput,
    CandidatePoolRunPublic,
)
from backend_core.growth.service import TargetingError
from httpx import ASGITransport, AsyncClient, Response

NOW = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
RULE_ID = UUID("00000000-0000-0000-0000-000000000901")
POLICY_ID = UUID("00000000-0000-0000-0000-000000000902")
RUN_ID = UUID("00000000-0000-0000-0000-000000000903")
SOURCE_ID = UUID("00000000-0000-0000-0000-000000000904")
OTHER_DEPARTMENT_SOURCE_ID = UUID("00000000-0000-0000-0000-000000000905")


def _context(*, role: Role = Role.OPERATOR) -> AuthContext:
    department = Department(
        id=uuid4(),
        name=f"Buyer Prospect HTTP {uuid4().hex}",
        password_hash="not-used-by-http-tests",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    operator = Operator(
        id=uuid4(),
        department_id=department.id,
        name="Buyer Prospect operator",
        role=role,
        status=OperatorStatus.ACTIVE,
    )
    return AuthContext(
        department=department,
        operator=operator,
        role=role,
        auth_session=AuthSession(
            id=uuid4(),
            department_id=department.id,
            operator_id=operator.id,
            token_hash="a" * 64,
            csrf_token_hash="b" * 64,
            ip="192.0.2.91",
            user_agent="buyer-prospect-http-test",
            expires_at=NOW + timedelta(hours=1),
            revoked_at=None,
        ),
    )


class FakeAuthService:
    def __init__(self, grants: frozenset[ModuleKey]) -> None:
        self.grants = grants

    async def resolve_effective_authorization(
        self,
        context: AuthContext,
        *,
        department_id: UUID | None = None,
    ) -> EffectiveAuthorizationContext:
        if context.operator is None or context.effective_role is None:
            raise AuthError(409, "OPERATOR_REQUIRED", "Select an operator first")
        target_department_id = department_id or context.department.id
        if (
            target_department_id != context.department.id
            and context.effective_role is not Role.SUPER_ADMIN
        ):
            raise AuthError(404, "RESOURCE_NOT_FOUND", "Resource not found")
        return EffectiveAuthorizationContext(
            department=context.department,
            operator=context.operator,
            department_role_ceiling=context.department_role_ceiling or context.role,
            effective_role=context.effective_role,
            department_scope=ResolvedDepartmentScope(
                department_id=target_department_id,
                cross_department_override=target_department_id != context.department.id,
            ),
            auth_session=context.auth_session,
            authorized_modules=self.grants,
        )

    def validate_csrf(self, _context: AuthContext, csrf_token: str | None) -> None:
        if csrf_token != "buyer-prospect-csrf":
            raise AuthError(403, "CSRF_FAILED", "CSRF validation failed")


def _assert_error(response: Response, *, status_code: int, code: str) -> None:
    body = response.json()
    assert response.status_code == status_code
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == code


def _mutation_headers(idempotency_key: str | None = None) -> dict[str, str]:
    headers = {
        "X-CSRF-Token": "buyer-prospect-csrf",
        "X-Forwarded-For": "198.51.100.91, 198.51.100.92",
        "User-Agent": "buyer-prospect-http-test",
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _set_csrf_cookie(client: AsyncClient) -> None:
    client.cookies.set(get_settings().csrf_cookie_name, "buyer-prospect-csrf")


def _rule(
    context: AuthContext,
    *,
    name: str = "Summer beauty prospects",
    status: CandidatePoolStatus = CandidatePoolStatus.ACTIVE,
    version: int = 3,
) -> BuyerProspectRulePublic:
    assert context.operator is not None
    return BuyerProspectRulePublic(
        id=RULE_ID,
        department_id=context.department.id,
        owner_operator_id=context.operator.id,
        owner=CampaignOwnerSummary(
            id=context.operator.id,
            name=context.operator.name,
            status=context.operator.status,
        ),
        name=name,
        status=status,
        version=version,
        current_policy_id=POLICY_ID,
        current_policy_version=2,
        category_ids=("beauty",),
        follower_min=1_000,
        follower_max=10_000,
        buyer_lead_tiers=(BuyerLeadTier.HIGH,),
        source_collection_job_id=SOURCE_ID,
        recent_collection_window=BuyerProspectRecentCollectionWindow.DAYS_30,
        prospect_owner_filter=BuyerProspectOwnerFilter.ANY,
        prospect_owner_operator_id=None,
        exclude_contacted=False,
        latest_run=_run(),
        created_at=NOW,
        updated_at=NOW,
    )


def _run(*, replay: bool = False) -> CandidatePoolRunPublic:
    return CandidatePoolRunPublic(
        id=RUN_ID,
        pool_id=RULE_ID,
        policy_id=POLICY_ID,
        as_of=NOW,
        input_watermark={"schema_version": 1, "candidate_count": 1},
        status=CandidatePoolRunStatus.PENDING,
        match_count=0,
        unknown_count=0,
        not_match_count=0,
        error_code=None,
        error_message=None,
        created_at=NOW,
        updated_at=NOW,
        idempotent_replay=replay,
    )


def _create_payload(*, source_collection_job_id: UUID = SOURCE_ID) -> dict[str, object]:
    return {
        "name": "Summer beauty prospects",
        "category_ids": ["beauty"],
        "follower_min": 1_000,
        "follower_max": 10_000,
        "buyer_lead_tiers": [BuyerLeadTier.HIGH.value],
        "source_collection_job_id": str(source_collection_job_id),
        "recent_collection_window": BuyerProspectRecentCollectionWindow.DAYS_30.value,
        "prospect_owner_filter": BuyerProspectOwnerFilter.ANY.value,
        "exclude_contacted": False,
    }


def _update_payload(context: AuthContext) -> dict[str, object]:
    assert context.operator is not None
    return {
        **_create_payload(),
        "name": "Updated beauty prospects",
        "owner_operator_id": str(context.operator.id),
        "expected_pool_version": 3,
    }


def _lifecycle_payload(status: CandidatePoolStatus) -> dict[str, object]:
    return {"expected_pool_version": 3, "status": status.value}


class FakeCandidatePoolService:
    def __init__(self, context: AuthContext) -> None:
        self.context = context
        self.calls: list[tuple[str, object]] = []
        self.run_requests: set[str] = set()

    async def list_buyer_prospect_rules(
        self,
        _context: AuthContext,
        *,
        cursor: UUID | None,
        limit: int,
        include_archived: bool,
        department_id: UUID,
    ) -> BuyerProspectRulePage:
        self.calls.append(("list", (cursor, limit, include_archived, department_id)))
        return BuyerProspectRulePage(items=(_rule(self.context),), next_cursor=None)

    async def buyer_prospect_rule_options(
        self,
        _context: AuthContext,
        *,
        department_id: UUID,
    ) -> BuyerProspectRuleOptionsPublic:
        self.calls.append(("options", department_id))
        return BuyerProspectRuleOptionsPublic(
            taxonomy_category_ids=("AUTO", "BEAUTY"),
            taxonomy_categories=(
                BuyerProspectRuleTaxonomyOption(id="AUTO", label="汽车"),
                BuyerProspectRuleTaxonomyOption(id="BEAUTY", label="美妆"),
            ),
            source_collection_jobs=(
                BuyerProspectRuleSourceOption(
                    id=SOURCE_ID,
                    name="影视批次",
                    industry="影视",
                    subdirection="剧情 / 娱乐",
                ),
            ),
            operators=(
                BuyerProspectRuleOperatorOption(
                    id=self.context.operator.id,
                    name="操作人",
                ),
            ),
        )

    async def get_buyer_prospect_rule(
        self,
        _context: AuthContext,
        *,
        pool_id: UUID,
        department_id: UUID,
    ) -> BuyerProspectRulePublic:
        self.calls.append(("get", (pool_id, department_id)))
        if pool_id != RULE_ID:
            raise TargetingError(
                404, "BUYER_PROSPECT_RULE_NOT_FOUND", "Market Prospect Rule not found"
            )
        return _rule(self.context)

    async def create_buyer_prospect_rule(
        self,
        _context: AuthContext,
        payload: BuyerProspectRuleCreateInput,
        *,
        department_id: UUID,
        idempotency_key: str,
        ip: str,
        user_agent: str,
    ) -> BuyerProspectRulePublic:
        self.calls.append(
            (
                "create",
                (payload.model_dump(mode="json"), department_id, idempotency_key, ip, user_agent),
            )
        )
        if payload.source_collection_job_id == OTHER_DEPARTMENT_SOURCE_ID:
            raise TargetingError(404, "COLLECTION_JOB_NOT_FOUND", "Collection Job not found")
        return _rule(self.context, name=payload.name)

    async def update_buyer_prospect_rule(
        self,
        _context: AuthContext,
        *,
        pool_id: UUID,
        payload: BuyerProspectRuleUpdateInput,
        department_id: UUID,
        ip: str,
        user_agent: str,
    ) -> BuyerProspectRulePublic:
        self.calls.append(
            (
                "update",
                (pool_id, payload.model_dump(mode="json"), department_id, ip, user_agent),
            )
        )
        if payload.source_collection_job_id == OTHER_DEPARTMENT_SOURCE_ID:
            raise TargetingError(404, "COLLECTION_JOB_NOT_FOUND", "Collection Job not found")
        return _rule(self.context, name=payload.name, version=4)

    async def set_buyer_prospect_rule_lifecycle(
        self,
        _context: AuthContext,
        *,
        pool_id: UUID,
        payload: BuyerProspectRuleLifecycleInput,
        department_id: UUID,
        ip: str,
        user_agent: str,
    ) -> BuyerProspectRulePublic:
        self.calls.append(
            (
                "lifecycle",
                (pool_id, payload.model_dump(mode="json"), department_id, ip, user_agent),
            )
        )
        return _rule(self.context, status=payload.status, version=4)

    async def reserve_run(
        self,
        _context: AuthContext,
        *,
        pool_id: UUID,
        payload: object,
        department_id: UUID,
        idempotency_key: str,
        ip: str,
        user_agent: str,
    ) -> CandidatePoolRunPublic:
        model_dump = payload.model_dump
        request = model_dump(mode="json", exclude_none=True)
        self.calls.append(
            ("run", (pool_id, request, department_id, idempotency_key, ip, user_agent))
        )
        if pool_id != RULE_ID:
            raise TargetingError(
                404, "BUYER_PROSPECT_RULE_NOT_FOUND", "Market Prospect Rule not found"
            )
        replay = idempotency_key in self.run_requests
        self.run_requests.add(idempotency_key)
        return _run(replay=replay)


class FakeTargetingTaskDispatcher:
    def __init__(self) -> None:
        self.run_ids: list[UUID] = []

    async def materialize(self, run_id: UUID) -> None:
        self.run_ids.append(run_id)


BUYER_AUTHORING_GRANTS = frozenset({ModuleKey.CANDIDATE_POOLS, ModuleKey.DATA_COLLECTION})


def _install_overrides(
    context: AuthContext,
    service: FakeCandidatePoolService,
    *,
    grants: frozenset[ModuleKey] = BUYER_AUTHORING_GRANTS,
) -> FakeTargetingTaskDispatcher:
    dispatcher = FakeTargetingTaskDispatcher()
    app.dependency_overrides[require_auth] = lambda: context
    app.dependency_overrides[get_auth_service] = lambda: FakeAuthService(grants)
    app.dependency_overrides[get_candidate_pool_service] = lambda: service
    app.dependency_overrides[get_targeting_task_dispatcher] = lambda: dispatcher
    return dispatcher


def test_buyer_prospect_routes_are_registered() -> None:
    document = app.openapi()
    assert "/api/v1/buyer-prospects" in document["paths"]


def test_buyer_prospect_list_and_get_adapt_closed_service_contracts() -> None:
    async def scenario() -> None:
        context = _context()
        service = FakeCandidatePoolService(context)
        _install_overrides(context, service)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                listed = await client.get("/api/v1/buyer-prospects?limit=20")
                assert listed.status_code == 200
                assert listed.json()["data"]["items"][0]["id"] == str(RULE_ID)
                assert service.calls == [("list", (None, 20, False, context.department.id))]

                detail = await client.get(f"/api/v1/buyer-prospects/{RULE_ID}")
                assert detail.status_code == 200
                assert detail.json()["data"]["name"] == "Summer beauty prospects"
                assert service.calls[-1] == ("get", (RULE_ID, context.department.id))
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_buyer_prospect_options_expose_trusted_labels_and_source_context() -> None:
    async def scenario() -> None:
        context = _context()
        service = FakeCandidatePoolService(context)
        _install_overrides(context, service)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/v1/buyer-prospects/options")
                assert response.status_code == 200
                data = response.json()["data"]
                assert data["taxonomy_categories"] == [
                    {"id": "AUTO", "label": "汽车"},
                    {"id": "BEAUTY", "label": "美妆"},
                ]
                assert data["source_collection_jobs"] == [
                    {
                        "id": str(SOURCE_ID),
                        "name": "影视批次",
                        "industry": "影视",
                        "subdirection": "剧情 / 娱乐",
                    }
                ]
                assert service.calls == [("options", context.department.id)]
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_buyer_prospect_create_accepts_valid_closed_rule() -> None:
    async def scenario() -> None:
        context = _context()
        service = FakeCandidatePoolService(context)
        _install_overrides(context, service)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                _set_csrf_cookie(client)
                created = await client.post(
                    "/api/v1/buyer-prospects",
                    json=_create_payload(),
                    headers=_mutation_headers("buyer-rule-create-key"),
                )
                assert created.status_code == 201
                assert created.json()["data"]["id"] == str(RULE_ID)
                call = service.calls[-1]
                assert call[0] == "create"
                assert call[1][1:] == (
                    context.department.id,
                    "buyer-rule-create-key",
                    "198.51.100.91",
                    "buyer-prospect-http-test",
                )
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_buyer_prospect_create_accepts_empty_secondary_category_filter() -> None:
    async def scenario() -> None:
        context = _context()
        service = FakeCandidatePoolService(context)
        _install_overrides(context, service)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                _set_csrf_cookie(client)
                created = await client.post(
                    "/api/v1/buyer-prospects",
                    json={**_create_payload(), "category_ids": []},
                    headers=_mutation_headers("buyer-rule-empty-categories"),
                )
                assert created.status_code == 201
                create_call = service.calls[-1]
                assert create_call[0] == "create"
                assert create_call[1][0]["category_ids"] == []
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_buyer_prospect_update_adapts_employee_save_shape() -> None:
    async def scenario() -> None:
        context = _context()
        service = FakeCandidatePoolService(context)
        _install_overrides(context, service)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                _set_csrf_cookie(client)
                updated = await client.put(
                    f"/api/v1/buyer-prospects/{RULE_ID}",
                    json=_update_payload(context),
                    headers=_mutation_headers(),
                )
                assert updated.status_code == 200
                assert updated.json()["data"]["name"] == "Updated beauty prospects"
                call = service.calls[-1]
                assert call[0] == "update"
                assert call[1][0] == RULE_ID
                assert call[1][2:] == (
                    context.department.id,
                    "198.51.100.91",
                    "buyer-prospect-http-test",
                )
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_buyer_prospect_lifecycle_enable_disable_and_archive() -> None:
    async def scenario() -> None:
        context = _context()
        service = FakeCandidatePoolService(context)
        _install_overrides(context, service)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                _set_csrf_cookie(client)
                for expected_status in (
                    CandidatePoolStatus.ACTIVE,
                    CandidatePoolStatus.DISABLED,
                    CandidatePoolStatus.ARCHIVED,
                ):
                    response = await client.post(
                        f"/api/v1/buyer-prospects/{RULE_ID}/lifecycle",
                        json=_lifecycle_payload(expected_status),
                        headers=_mutation_headers(),
                    )
                    assert response.status_code == 200
                    assert response.json()["data"]["status"] == expected_status.value

                assert [call[0] for call in service.calls] == [
                    "lifecycle",
                    "lifecycle",
                    "lifecycle",
                ]
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_buyer_prospect_run_dispatches_and_replays_idempotently() -> None:
    async def scenario() -> None:
        context = _context()
        service = FakeCandidatePoolService(context)
        dispatcher = _install_overrides(context, service)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                _set_csrf_cookie(client)
                started = await client.post(
                    f"/api/v1/buyer-prospects/{RULE_ID}/runs",
                    json={},
                    headers=_mutation_headers("buyer-rule-run-key"),
                )
                assert started.status_code == 202
                assert started.json()["data"]["idempotent_replay"] is False

                replay = await client.post(
                    f"/api/v1/buyer-prospects/{RULE_ID}/runs",
                    json={},
                    headers=_mutation_headers("buyer-rule-run-key"),
                )
                assert replay.status_code == 200
                assert replay.json()["data"]["idempotent_replay"] is True
                assert dispatcher.run_ids == [RUN_ID, RUN_ID]
                assert service.calls == [
                    (
                        "run",
                        (
                            RULE_ID,
                            {},
                            context.department.id,
                            "buyer-rule-run-key",
                            "198.51.100.91",
                            "buyer-prospect-http-test",
                        ),
                    ),
                    (
                        "run",
                        (
                            RULE_ID,
                            {},
                            context.department.id,
                            "buyer-rule-run-key",
                            "198.51.100.91",
                            "buyer-prospect-http-test",
                        ),
                    ),
                ]
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_buyer_prospect_mutations_require_csrf() -> None:
    async def scenario() -> None:
        context = _context()
        service = FakeCandidatePoolService(context)
        _install_overrides(context, service)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                create = await client.post(
                    "/api/v1/buyer-prospects",
                    json=_create_payload(),
                    headers={"Idempotency-Key": "csrf-create-key"},
                )
                _assert_error(create, status_code=403, code="CSRF_FAILED")

                update = await client.put(
                    f"/api/v1/buyer-prospects/{RULE_ID}",
                    json=_update_payload(context),
                )
                _assert_error(update, status_code=403, code="CSRF_FAILED")

                lifecycle = await client.post(
                    f"/api/v1/buyer-prospects/{RULE_ID}/lifecycle",
                    json=_lifecycle_payload(CandidatePoolStatus.DISABLED),
                )
                _assert_error(lifecycle, status_code=403, code="CSRF_FAILED")

                run = await client.post(
                    f"/api/v1/buyer-prospects/{RULE_ID}/runs",
                    json={},
                    headers={"Idempotency-Key": "csrf-run-key"},
                )
                _assert_error(run, status_code=403, code="CSRF_FAILED")
                assert service.calls == []
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_buyer_prospect_authoring_requires_each_write_grant_and_non_viewer_role() -> None:
    async def scenario() -> None:
        context = _context()
        service = FakeCandidatePoolService(context)
        _install_overrides(context, service)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                _set_csrf_cookie(client)
                viewer = _context(role=Role.VIEWER)
                app.dependency_overrides[require_auth] = lambda: viewer
                viewer_response = await client.post(
                    f"/api/v1/buyer-prospects/{RULE_ID}/lifecycle",
                    json=_lifecycle_payload(CandidatePoolStatus.DISABLED),
                    headers=_mutation_headers(),
                )
                _assert_error(viewer_response, status_code=403, code="PERMISSION_DENIED")

                candidate_only = _context()
                app.dependency_overrides[require_auth] = lambda: candidate_only
                app.dependency_overrides[get_auth_service] = lambda: FakeAuthService(
                    frozenset({ModuleKey.CANDIDATE_POOLS})
                )
                missing_data_collection = await client.post(
                    "/api/v1/buyer-prospects",
                    json=_create_payload(),
                    headers=_mutation_headers("missing-data-collection-key"),
                )
                _assert_error(
                    missing_data_collection,
                    status_code=403,
                    code="MODULE_ACCESS_DENIED",
                )

                data_collection_only = _context()
                app.dependency_overrides[require_auth] = lambda: data_collection_only
                app.dependency_overrides[get_auth_service] = lambda: FakeAuthService(
                    frozenset({ModuleKey.DATA_COLLECTION})
                )
                missing_candidate_pools = await client.post(
                    "/api/v1/buyer-prospects",
                    json=_create_payload(),
                    headers=_mutation_headers("missing-candidate-pools-key"),
                )
                _assert_error(
                    missing_candidate_pools,
                    status_code=403,
                    code="MODULE_ACCESS_DENIED",
                )
                assert service.calls == []
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_buyer_prospect_scope_stays_in_department_without_cross_scope_disclosure() -> None:
    async def scenario() -> None:
        context = _context()
        service = FakeCandidatePoolService(context)
        _install_overrides(context, service)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                own_department = await client.get(
                    "/api/v1/buyer-prospects",
                    headers={"X-Department-ID": str(context.department.id)},
                )
                assert own_department.status_code == 200
                assert service.calls == [("list", (None, 50, False, context.department.id))]

                cross_department = await client.get(
                    "/api/v1/buyer-prospects",
                    headers={"X-Department-ID": str(uuid4())},
                )
                _assert_error(cross_department, status_code=404, code="RESOURCE_NOT_FOUND")
                assert len(service.calls) == 1
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_buyer_prospect_cross_department_source_is_rejected_without_disclosure() -> None:
    async def scenario() -> None:
        context = _context()
        service = FakeCandidatePoolService(context)
        _install_overrides(context, service)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                _set_csrf_cookie(client)
                rejected = await client.post(
                    "/api/v1/buyer-prospects",
                    json=_create_payload(source_collection_job_id=OTHER_DEPARTMENT_SOURCE_ID),
                    headers=_mutation_headers("other-department-source-key"),
                )
                _assert_error(rejected, status_code=404, code="COLLECTION_JOB_NOT_FOUND")
                call = service.calls[-1]
                assert call[0] == "create"
                assert call[1][1] == context.department.id
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_generic_buyer_candidate_pool_authoring_remains_rejected() -> None:
    async def scenario() -> None:
        context = _context()
        service = FakeCandidatePoolService(context)
        _install_overrides(context, service)
        payload = {
            "name": "Generic buyer rule",
            "kind": "POTENTIAL_BUYER",
            "source_collection_job_id": str(SOURCE_ID),
            "policy": {
                "policy_type": "BUYER_V1",
                "schema_version": 1,
                "taxonomy": {
                    "schema_version": 1,
                    "taxonomy_version": "reviewed-v1",
                    "reviewed": True,
                },
            },
        }
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                _set_csrf_cookie(client)
                rejected = await client.post(
                    "/api/v1/candidate-pools",
                    json=payload,
                    headers=_mutation_headers("generic-buyer-key"),
                )
                _assert_error(rejected, status_code=409, code="BUYER_RULE_READ_ONLY")
                assert service.calls == []
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())

"""Focused HTTP adaptation coverage for deterministic Candidate Pool operations."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.http.candidate_pools import get_candidate_pool_service
from app.http.dependencies import (
    get_auth_service,
    get_database_session,
    get_targeting_task_dispatcher,
    require_auth,
)
from app.http.phase3a_scope import resolve_phase3a_department_scope
from app.main import app
from backend_core.auth import AuthContext, AuthError
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, Operator
from backend_core.campaigns.access import DepartmentScope
from backend_core.campaigns.schemas import CampaignOwnerSummary
from backend_core.config import get_settings
from backend_core.growth.enums import (
    CandidatePoolKind,
    CandidatePoolRunStatus,
    CandidatePoolStatus,
    CandidateResult,
)
from backend_core.growth.schemas import (
    CandidatePoolCreateInput,
    CandidatePoolMemberPublic,
    CandidatePoolPage,
    CandidatePoolPublic,
    CandidatePoolRunMemberPage,
    CandidatePoolRunPage,
    CandidatePoolRunPublic,
    TargetingPolicyCreateInput,
    TargetingPolicyCreateResultPublic,
    TargetingPolicyPublic,
)
from backend_core.growth.service import TargetingError
from backend_core.growth.targeting import SellerTargetingPolicy
from backend_core.influencers.enums import InfluencerStatus, Platform
from backend_core.influencers.schemas import (
    InfluencerIdentitySummary,
    PlatformAccountIdentitySummary,
)
from httpx import ASGITransport, AsyncClient, Response

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
POOL_ID = UUID("00000000-0000-0000-0000-000000000301")
POLICY_ID = UUID("00000000-0000-0000-0000-000000000302")
RUN_ID = UUID("00000000-0000-0000-0000-000000000303")
MEMBER_ID = UUID("00000000-0000-0000-0000-000000000304")
INFLUENCER_ID = UUID("00000000-0000-0000-0000-000000000305")
ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000306")


def _context(*, role: Role = Role.OPERATOR, selected_operator: bool = True) -> AuthContext:
    department = Department(
        id=uuid4(),
        name=f"Candidate Pool HTTP {role.value}-{uuid4().hex}",
        password_hash="not-used-by-http-tests",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    operator = (
        Operator(
            id=uuid4(),
            department_id=department.id,
            name="Candidate Pool operator",
            role=role,
            status=OperatorStatus.ACTIVE,
        )
        if selected_operator
        else None
    )
    auth_session = AuthSession(
        id=uuid4(),
        department_id=department.id,
        operator_id=operator.id if operator is not None else None,
        token_hash="a" * 64,
        csrf_token_hash="b" * 64,
        ip="192.0.2.31",
        user_agent="candidate-pool-http-test",
        expires_at=NOW + timedelta(hours=1),
        revoked_at=None,
    )
    return AuthContext(
        department=department,
        operator=operator,
        role=role,
        auth_session=auth_session,
    )


def _pool(context: AuthContext) -> CandidatePoolPublic:
    assert context.operator is not None
    return CandidatePoolPublic(
        id=POOL_ID,
        department_id=context.department.id,
        owner_operator_id=context.operator.id,
        owner=CampaignOwnerSummary(
            id=context.operator.id,
            name=context.operator.name,
            status=context.operator.status,
        ),
        name="Seller prospects",
        kind=CandidatePoolKind.POTENTIAL_SELLER,
        source_collection_job_id=None,
        status=CandidatePoolStatus.ACTIVE,
        current_policy_id=POLICY_ID,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _run(
    *,
    replay: bool = False,
    status: CandidatePoolRunStatus = CandidatePoolRunStatus.PENDING,
) -> CandidatePoolRunPublic:
    return CandidatePoolRunPublic(
        id=RUN_ID,
        pool_id=POOL_ID,
        policy_id=POLICY_ID,
        as_of=NOW,
        input_watermark={"schema_version": 1, "candidate_count": 1},
        status=status,
        match_count=0,
        unknown_count=0,
        not_match_count=0,
        error_code=None,
        error_message=None,
        created_at=NOW,
        updated_at=NOW,
        idempotent_replay=replay,
    )


def _policy_create_result(context: AuthContext) -> TargetingPolicyCreateResultPublic:
    assert context.operator is not None
    return TargetingPolicyCreateResultPublic(
        id=POLICY_ID,
        pool_id=POOL_ID,
        version=2,
        schema_version=1,
        canonical_hash="c" * 64,
        created_by_operator_id=context.operator.id,
        created_at=NOW,
    )


def _seller_policy_payload(*, tags: tuple[str, ...] = ()) -> dict[str, object]:
    return {
        "policy_type": "SELLER_V1",
        "schema_version": 1,
        "tags_exact_any": list(tags),
    }


def _pool_create_payload(
    *,
    name: str = "Seller prospects",
    tags: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "name": name,
        "kind": CandidatePoolKind.POTENTIAL_SELLER.value,
        "policy": _seller_policy_payload(tags=tags),
    }


def _policy_create_payload(*, tags: tuple[str, ...] = ()) -> dict[str, object]:
    return {"policy": _seller_policy_payload(tags=tags)}


def _mutation_headers(idempotency_key: str) -> dict[str, str]:
    return {
        "Idempotency-Key": idempotency_key,
        "X-CSRF-Token": "candidate-pool-csrf",
        "X-Forwarded-For": "198.51.100.10, 198.51.100.11",
        "User-Agent": "candidate-pool-http-test",
    }


class FakeAuthService:
    def validate_csrf(self, _context: AuthContext, csrf_token: str | None) -> None:
        if csrf_token != "candidate-pool-csrf":
            raise AuthError(403, "CSRF_FAILED", "CSRF validation failed")


class FakeCandidatePoolService:
    def __init__(self, context: AuthContext) -> None:
        self.context = context
        self.calls: list[tuple[str, object]] = []
        self.pool_create_requests: dict[str, dict[str, object]] = {}
        self.policy_create_requests: dict[str, dict[str, object]] = {}

    @staticmethod
    def _idempotency_replay_or_conflict[ResultT](
        requests: dict[str, dict[str, object]],
        *,
        idempotency_key: str,
        request: dict[str, object],
        result: ResultT,
    ) -> ResultT:
        previous = requests.get(idempotency_key)
        if previous is None:
            requests[idempotency_key] = request
            return result
        if previous != request:
            raise TargetingError(
                409,
                "IDEMPOTENCY_KEY_REUSED",
                "Idempotency-Key was already used for a different request",
            )
        return result

    async def create_pool(
        self,
        _context: AuthContext,
        payload: CandidatePoolCreateInput,
        *,
        idempotency_key: str,
        ip: str,
        user_agent: str,
        department_id: UUID | None = None,
    ) -> CandidatePoolPublic:
        request = payload.model_dump(mode="json")
        self.calls.append(
            ("create_pool", (request, idempotency_key, ip, user_agent, department_id))
        )
        return self._idempotency_replay_or_conflict(
            self.pool_create_requests,
            idempotency_key=idempotency_key,
            request=request,
            result=_pool(self.context),
        )

    async def append_policy(
        self,
        _context: AuthContext,
        pool_id: UUID,
        payload: TargetingPolicyCreateInput,
        *,
        idempotency_key: str,
        ip: str,
        user_agent: str,
        department_id: UUID | None = None,
    ) -> TargetingPolicyCreateResultPublic:
        if pool_id != POOL_ID:
            raise TargetingError(404, "CANDIDATE_POOL_NOT_FOUND", "Candidate Pool not found")
        request = {"pool_id": str(pool_id), **payload.model_dump(mode="json")}
        self.calls.append(
            ("append_policy", (request, idempotency_key, ip, user_agent, department_id))
        )
        return self._idempotency_replay_or_conflict(
            self.policy_create_requests,
            idempotency_key=idempotency_key,
            request=request,
            result=_policy_create_result(self.context),
        )

    async def list_pools(
        self,
        _context: AuthContext,
        *,
        cursor: UUID | None,
        limit: int,
        department_id: UUID | None = None,
    ) -> CandidatePoolPage:
        self.calls.append(("list_pools", (cursor, limit, department_id)))
        return CandidatePoolPage(items=(_pool(self.context),), next_cursor=None)

    async def get_pool(
        self,
        _context: AuthContext,
        pool_id: UUID,
        *,
        department_id: UUID | None = None,
    ) -> CandidatePoolPublic:
        self.calls.append(("get_pool", (pool_id, department_id)))
        if pool_id != POOL_ID:
            raise TargetingError(404, "CANDIDATE_POOL_NOT_FOUND", "Candidate Pool not found")
        return _pool(self.context)

    async def list_policies(
        self,
        _context: AuthContext,
        pool_id: UUID,
        *,
        department_id: UUID | None = None,
    ) -> tuple[TargetingPolicyPublic, ...]:
        self.calls.append(("list_policies", (pool_id, department_id)))
        if pool_id != POOL_ID:
            raise TargetingError(404, "CANDIDATE_POOL_NOT_FOUND", "Candidate Pool not found")
        return (
            TargetingPolicyPublic(
                id=POLICY_ID,
                pool_id=POOL_ID,
                version=1,
                schema_version=1,
                definition=SellerTargetingPolicy(),
                canonical_hash="c" * 64,
                created_by_operator_id=self.context.operator.id,  # type: ignore[union-attr]
                created_at=NOW,
                updated_at=NOW,
            ),
        )

    async def get_policy(
        self,
        _context: AuthContext,
        *,
        pool_id: UUID,
        policy_id: UUID,
        department_id: UUID | None = None,
    ) -> TargetingPolicyPublic:
        self.calls.append(("get_policy", (pool_id, policy_id, department_id)))
        if pool_id != POOL_ID or policy_id != POLICY_ID:
            raise TargetingError(404, "TARGETING_POLICY_NOT_FOUND", "Targeting policy not found")
        return (await self.list_policies(_context, pool_id, department_id=department_id))[0]

    async def list_runs(
        self,
        _context: AuthContext,
        *,
        pool_id: UUID,
        cursor: UUID | None,
        limit: int,
        department_id: UUID | None = None,
    ) -> CandidatePoolRunPage:
        self.calls.append(("list_runs", (pool_id, cursor, limit, department_id)))
        if pool_id != POOL_ID:
            raise TargetingError(404, "CANDIDATE_POOL_NOT_FOUND", "Candidate Pool not found")
        return CandidatePoolRunPage(items=(_run(),), next_cursor=None)

    async def get_run(
        self,
        _context: AuthContext,
        *,
        pool_id: UUID,
        run_id: UUID,
        department_id: UUID | None = None,
    ) -> CandidatePoolRunPublic:
        self.calls.append(("get_run", (pool_id, run_id, department_id)))
        if pool_id != POOL_ID or run_id != RUN_ID:
            raise TargetingError(
                404,
                "CANDIDATE_POOL_RUN_NOT_FOUND",
                "Candidate Pool run not found",
            )
        return _run()

    async def list_run_members(
        self,
        _context: AuthContext,
        *,
        pool_id: UUID,
        run_id: UUID,
        cursor: UUID | None,
        limit: int,
        result: CandidateResult | None = None,
        department_id: UUID | None = None,
    ) -> CandidatePoolRunMemberPage:
        self.calls.append(
            ("list_run_members", (pool_id, run_id, cursor, limit, result, department_id))
        )
        if pool_id != POOL_ID or run_id != RUN_ID:
            raise TargetingError(
                404,
                "CANDIDATE_POOL_RUN_NOT_FOUND",
                "Candidate Pool run not found",
            )
        return CandidatePoolRunMemberPage(
            items=(
                CandidatePoolMemberPublic(
                    id=MEMBER_ID,
                    run_id=RUN_ID,
                    influencer_id=INFLUENCER_ID,
                    platform_account_id=ACCOUNT_ID,
                    result=CandidateResult.MATCH,
                    reason_codes=("CONTACT_AVAILABLE",),
                    redacted_evidence={"schema_version": 1, "criteria": []},
                    evidence_hash="d" * 64,
                    created_at=NOW,
                    updated_at=NOW,
                    influencer=InfluencerIdentitySummary(
                        id=INFLUENCER_ID,
                        display_name="Candidate identity",
                        status=InfluencerStatus.ACTIVE,
                    ),
                    platform_account=PlatformAccountIdentitySummary(
                        id=ACCOUNT_ID,
                        platform=Platform.XIAOHONGSHU,
                        platform_account_id=None,
                        account_name="Candidate account",
                        account_handle=None,
                        is_active=True,
                    ),
                ),
            ),
            next_cursor=None,
        )

    async def reserve_run(
        self,
        _context: AuthContext,
        *,
        pool_id: UUID,
        idempotency_key: str,
        ip: str,
        user_agent: str,
        department_id: UUID | None = None,
    ) -> CandidatePoolRunPublic:
        self.calls.append(
            ("reserve_run", (pool_id, idempotency_key, ip, user_agent, department_id))
        )
        if not idempotency_key:
            raise TargetingError(422, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key is invalid")
        if pool_id != POOL_ID:
            raise TargetingError(404, "CANDIDATE_POOL_NOT_FOUND", "Candidate Pool not found")
        return _run(
            replay=idempotency_key == "replay-key",
            status=(
                CandidatePoolRunStatus.RUNNING
                if idempotency_key == "running-key"
                else CandidatePoolRunStatus.PENDING
            ),
        )


class FakeTargetingTaskDispatcher:
    def __init__(self) -> None:
        self.run_ids: list[UUID] = []
        self.fail = False

    async def materialize(self, run_id: UUID) -> None:
        self.run_ids.append(run_id)
        if self.fail:
            raise RuntimeError("synthetic broker failure")


def _install_overrides(
    context: AuthContext,
    service: FakeCandidatePoolService,
    dispatcher: FakeTargetingTaskDispatcher | None = None,
) -> FakeTargetingTaskDispatcher:
    dispatcher = dispatcher or FakeTargetingTaskDispatcher()
    app.dependency_overrides[require_auth] = lambda: context
    app.dependency_overrides[get_auth_service] = FakeAuthService
    app.dependency_overrides[get_candidate_pool_service] = lambda: service
    app.dependency_overrides[get_targeting_task_dispatcher] = lambda: dispatcher
    app.dependency_overrides[resolve_phase3a_department_scope] = lambda: DepartmentScope(
        department_id=context.department.id,
        cross_department_override=False,
    )
    return dispatcher


def _assert_error(response: Response, *, status_code: int, code: str) -> None:
    body = response.json()
    assert response.status_code == status_code
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == code
    assert body["request_id"]


def test_candidate_pool_read_routes_adapt_closed_service_contracts() -> None:
    async def scenario() -> None:
        context = _context()
        service = FakeCandidatePoolService(context)
        _install_overrides(context, service)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                listed = await client.get("/api/v1/candidate-pools?limit=20")
                assert listed.status_code == 200
                assert listed.json()["data"]["items"][0]["id"] == str(POOL_ID)
                assert service.calls[0] == (
                    "list_pools",
                    (None, 20, context.department.id),
                )

                closed_query = await client.get("/api/v1/candidate-pools?not_allowed=true")
                _assert_error(closed_query, status_code=422, code="VALIDATION_ERROR")

                detail = await client.get(f"/api/v1/candidate-pools/{POOL_ID}")
                assert detail.status_code == 200
                assert detail.json()["data"]["name"] == "Seller prospects"

                policies = await client.get(f"/api/v1/candidate-pools/{POOL_ID}/policies")
                assert policies.status_code == 200
                assert policies.json()["data"][0]["definition"]["policy_type"] == "SELLER_V1"

                policy = await client.get(f"/api/v1/candidate-pools/{POOL_ID}/policies/{POLICY_ID}")
                assert policy.status_code == 200
                assert policy.json()["data"]["id"] == str(POLICY_ID)

                runs = await client.get(f"/api/v1/candidate-pools/{POOL_ID}/runs?limit=10")
                assert runs.status_code == 200
                assert runs.json()["data"]["items"][0]["id"] == str(RUN_ID)

                closed_runs_query = await client.get(
                    f"/api/v1/candidate-pools/{POOL_ID}/runs?limit=10&limit=11"
                )
                _assert_error(closed_runs_query, status_code=422, code="VALIDATION_ERROR")

                run = await client.get(f"/api/v1/candidate-pools/{POOL_ID}/runs/{RUN_ID}")
                assert run.status_code == 200
                assert run.json()["data"]["status"] == "PENDING"

                members = await client.get(
                    f"/api/v1/candidate-pools/{POOL_ID}/runs/{RUN_ID}/members?limit=10&result=MATCH"
                )
                assert members.status_code == 200
                assert members.json()["data"]["items"][0]["evidence_hash"] == "d" * 64

                invalid_member_result = await client.get(
                    f"/api/v1/candidate-pools/{POOL_ID}/runs/{RUN_ID}/members?result=NOT_MATCH"
                )
                _assert_error(invalid_member_result, status_code=422, code="VALIDATION_ERROR")

                missing = await client.get(f"/api/v1/candidate-pools/{uuid4()}")
                _assert_error(missing, status_code=404, code="CANDIDATE_POOL_NOT_FOUND")
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_candidate_pool_openapi_requires_canonical_identity_and_owner_projections() -> None:
    document = app.openapi()
    schemas = document["components"]["schemas"]
    pool = schemas["CandidatePoolPublic"]
    member = schemas["CandidatePoolMemberPublic"]
    account = schemas["PlatformAccountIdentitySummary"]

    assert "owner" in pool["required"]
    assert pool["properties"]["owner"]["$ref"].endswith("/CampaignOwnerSummary")
    assert "influencer" in member["required"]
    assert "platform_account" in member["required"]
    assert member["properties"]["influencer"]["$ref"].endswith("/InfluencerIdentitySummary")
    assert member["properties"]["platform_account"]["$ref"].endswith(
        "/PlatformAccountIdentitySummary"
    )
    assert set(account["required"]) == {
        "id",
        "platform",
        "platform_account_id",
        "account_name",
        "account_handle",
        "is_active",
    }
    assert account["properties"]["platform_account_id"]["anyOf"][1]["type"] == "null"
    assert account["properties"]["account_handle"]["anyOf"][1]["type"] == "null"
    assert not ({"email", "phone", "wechat", "contact"} & set(member["properties"]))


def test_candidate_pool_scope_header_is_single_valued_and_no_disclosure() -> None:
    async def scenario() -> None:
        context = _context()
        service = FakeCandidatePoolService(context)
        app.dependency_overrides[require_auth] = lambda: context
        app.dependency_overrides[get_auth_service] = FakeAuthService
        app.dependency_overrides[get_candidate_pool_service] = lambda: service
        # The resolver only needs persistence for an authorized cross-Department
        # lookup; these focused requests stay in the session Department or fail
        # before a lookup, so a sentinel makes accidental reads visible.
        app.dependency_overrides[get_database_session] = lambda: object()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                omitted = await client.get("/api/v1/candidate-pools")
                assert omitted.status_code == 200
                assert service.calls[-1] == (
                    "list_pools",
                    (None, 50, context.department.id),
                )

                own = await client.get(
                    "/api/v1/candidate-pools",
                    headers={"X-Department-ID": str(context.department.id)},
                )
                assert own.status_code == 200
                assert service.calls[-1] == (
                    "list_pools",
                    (None, 50, context.department.id),
                )

                cross = await client.get(
                    "/api/v1/candidate-pools",
                    headers={"X-Department-ID": str(uuid4())},
                )
                _assert_error(cross, status_code=404, code="RESOURCE_NOT_FOUND")

                repeated = await client.get(
                    "/api/v1/candidate-pools",
                    headers=[
                        ("X-Department-ID", str(context.department.id)),
                        ("X-Department-ID", str(context.department.id)),
                    ],
                )
                _assert_error(repeated, status_code=422, code="VALIDATION_ERROR")
                assert len(service.calls) == 2
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_candidate_pool_create_requires_mutation_prerequisites_and_replays_with_201() -> None:
    async def scenario() -> None:
        context = _context()
        service = FakeCandidatePoolService(context)
        _install_overrides(context, service)
        settings = get_settings()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                missing_csrf = await client.post(
                    "/api/v1/candidate-pools",
                    json=_pool_create_payload(),
                    headers={"Idempotency-Key": "pool-create-key"},
                )
                _assert_error(missing_csrf, status_code=403, code="CSRF_FAILED")

                client.cookies.set(settings.csrf_cookie_name, "candidate-pool-csrf")
                wrong_csrf = await client.post(
                    "/api/v1/candidate-pools",
                    json=_pool_create_payload(),
                    headers={
                        "Idempotency-Key": "pool-create-key",
                        "X-CSRF-Token": "wrong",
                    },
                )
                _assert_error(wrong_csrf, status_code=403, code="CSRF_FAILED")

                missing_idempotency = await client.post(
                    "/api/v1/candidate-pools",
                    json=_pool_create_payload(),
                    headers={"X-CSRF-Token": "candidate-pool-csrf"},
                )
                _assert_error(missing_idempotency, status_code=422, code="IDEMPOTENCY_KEY_INVALID")

                duplicate_idempotency = await client.post(
                    "/api/v1/candidate-pools",
                    json=_pool_create_payload(),
                    headers=[
                        ("Idempotency-Key", "pool-create-key"),
                        ("Idempotency-Key", "other-pool-create-key"),
                        ("X-CSRF-Token", "candidate-pool-csrf"),
                    ],
                )
                _assert_error(
                    duplicate_idempotency,
                    status_code=422,
                    code="IDEMPOTENCY_KEY_INVALID",
                )
                assert service.calls == []

                created = await client.post(
                    "/api/v1/candidate-pools",
                    json=_pool_create_payload(),
                    headers=_mutation_headers("pool-create-key"),
                )
                assert created.status_code == 201
                assert created.json()["data"]["id"] == str(POOL_ID)
                assert service.calls[0][0] == "create_pool"

                replay = await client.post(
                    "/api/v1/candidate-pools",
                    json=_pool_create_payload(),
                    headers=_mutation_headers("pool-create-key"),
                )
                assert replay.status_code == 201
                assert replay.json()["data"] == created.json()["data"]

                conflict = await client.post(
                    "/api/v1/candidate-pools",
                    json=_pool_create_payload(name="Changed seller prospects"),
                    headers=_mutation_headers("pool-create-key"),
                )
                _assert_error(conflict, status_code=409, code="IDEMPOTENCY_KEY_REUSED")
                assert [call[0] for call in service.calls] == [
                    "create_pool",
                    "create_pool",
                    "create_pool",
                ]

                viewer = _context(role=Role.VIEWER)
                app.dependency_overrides[require_auth] = lambda: viewer
                rejected_viewer = await client.post(
                    "/api/v1/candidate-pools",
                    json=_pool_create_payload(),
                    headers=_mutation_headers("viewer-pool-key"),
                )
                _assert_error(rejected_viewer, status_code=403, code="PERMISSION_DENIED")

                no_operator = _context(selected_operator=False)
                app.dependency_overrides[require_auth] = lambda: no_operator
                rejected_operator = await client.post(
                    "/api/v1/candidate-pools",
                    json=_pool_create_payload(),
                    headers=_mutation_headers("operator-pool-key"),
                )
                _assert_error(rejected_operator, status_code=409, code="OPERATOR_REQUIRED")
                assert len(service.calls) == 3
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_candidate_pool_policy_create_replays_with_201_and_uses_redacted_result() -> None:
    async def scenario() -> None:
        context = _context()
        service = FakeCandidatePoolService(context)
        _install_overrides(context, service)
        settings = get_settings()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                missing_csrf = await client.post(
                    f"/api/v1/candidate-pools/{POOL_ID}/policies",
                    json=_policy_create_payload(),
                    headers={"Idempotency-Key": "policy-create-key"},
                )
                _assert_error(missing_csrf, status_code=403, code="CSRF_FAILED")

                client.cookies.set(settings.csrf_cookie_name, "candidate-pool-csrf")
                missing_idempotency = await client.post(
                    f"/api/v1/candidate-pools/{POOL_ID}/policies",
                    json=_policy_create_payload(),
                    headers={"X-CSRF-Token": "candidate-pool-csrf"},
                )
                _assert_error(missing_idempotency, status_code=422, code="IDEMPOTENCY_KEY_INVALID")

                duplicate_idempotency = await client.post(
                    f"/api/v1/candidate-pools/{POOL_ID}/policies",
                    json=_policy_create_payload(),
                    headers=[
                        ("Idempotency-Key", "policy-create-key"),
                        ("Idempotency-Key", "other-policy-create-key"),
                        ("X-CSRF-Token", "candidate-pool-csrf"),
                    ],
                )
                _assert_error(
                    duplicate_idempotency,
                    status_code=422,
                    code="IDEMPOTENCY_KEY_INVALID",
                )
                assert service.calls == []

                created = await client.post(
                    f"/api/v1/candidate-pools/{POOL_ID}/policies",
                    json=_policy_create_payload(tags=("beauty",)),
                    headers=_mutation_headers("policy-create-key"),
                )
                assert created.status_code == 201
                assert set(created.json()["data"]) == {
                    "id",
                    "pool_id",
                    "version",
                    "schema_version",
                    "canonical_hash",
                    "created_by_operator_id",
                    "created_at",
                }
                assert "definition" not in created.json()["data"]
                assert service.calls[0][0] == "append_policy"

                replay = await client.post(
                    f"/api/v1/candidate-pools/{POOL_ID}/policies",
                    json=_policy_create_payload(tags=("beauty",)),
                    headers=_mutation_headers("policy-create-key"),
                )
                assert replay.status_code == 201
                assert replay.json()["data"] == created.json()["data"]

                conflict = await client.post(
                    f"/api/v1/candidate-pools/{POOL_ID}/policies",
                    json=_policy_create_payload(tags=("fashion",)),
                    headers=_mutation_headers("policy-create-key"),
                )
                _assert_error(conflict, status_code=409, code="IDEMPOTENCY_KEY_REUSED")

                missing_pool = await client.post(
                    f"/api/v1/candidate-pools/{uuid4()}/policies",
                    json=_policy_create_payload(),
                    headers=_mutation_headers("missing-pool-policy-key"),
                )
                _assert_error(missing_pool, status_code=404, code="CANDIDATE_POOL_NOT_FOUND")
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_candidate_pool_run_reservation_requires_csrf_operator_viewer_and_idempotency() -> None:
    async def scenario() -> None:
        context = _context()
        service = FakeCandidatePoolService(context)
        dispatcher = _install_overrides(context, service)
        settings = get_settings()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                missing_csrf = await client.post(
                    f"/api/v1/candidate-pools/{POOL_ID}/runs",
                    json={},
                    headers={"Idempotency-Key": "run-key"},
                )
                _assert_error(missing_csrf, status_code=403, code="CSRF_FAILED")

                client.cookies.set(settings.csrf_cookie_name, "candidate-pool-csrf")
                wrong_csrf = await client.post(
                    f"/api/v1/candidate-pools/{POOL_ID}/runs",
                    json={},
                    headers={"Idempotency-Key": "run-key", "X-CSRF-Token": "wrong"},
                )
                _assert_error(wrong_csrf, status_code=403, code="CSRF_FAILED")

                missing_idempotency = await client.post(
                    f"/api/v1/candidate-pools/{POOL_ID}/runs",
                    json={},
                    headers={"X-CSRF-Token": "candidate-pool-csrf"},
                )
                _assert_error(missing_idempotency, status_code=422, code="IDEMPOTENCY_KEY_INVALID")

                reserved = await client.post(
                    f"/api/v1/candidate-pools/{POOL_ID}/runs",
                    json={},
                    headers={
                        "Idempotency-Key": "run-key",
                        "X-CSRF-Token": "candidate-pool-csrf",
                    },
                )
                assert reserved.status_code == 202
                assert reserved.json()["data"]["idempotent_replay"] is False
                assert dispatcher.run_ids == [RUN_ID]

                replay = await client.post(
                    f"/api/v1/candidate-pools/{POOL_ID}/runs",
                    json={},
                    headers={
                        "Idempotency-Key": "replay-key",
                        "X-CSRF-Token": "candidate-pool-csrf",
                    },
                )
                assert replay.status_code == 200
                assert replay.json()["data"]["idempotent_replay"] is True
                assert dispatcher.run_ids == [RUN_ID, RUN_ID]

                dispatcher.fail = True
                broker_failure = await client.post(
                    f"/api/v1/candidate-pools/{POOL_ID}/runs",
                    json={},
                    headers={
                        "Idempotency-Key": "broker-failure-key",
                        "X-CSRF-Token": "candidate-pool-csrf",
                    },
                )
                assert broker_failure.status_code == 202
                assert dispatcher.run_ids == [RUN_ID, RUN_ID, RUN_ID]
                dispatcher.fail = False

                non_pending = await client.post(
                    f"/api/v1/candidate-pools/{POOL_ID}/runs",
                    json={},
                    headers={
                        "Idempotency-Key": "running-key",
                        "X-CSRF-Token": "candidate-pool-csrf",
                    },
                )
                assert non_pending.status_code == 202
                assert dispatcher.run_ids == [RUN_ID, RUN_ID, RUN_ID]

                viewer = _context(role=Role.VIEWER)
                app.dependency_overrides[require_auth] = lambda: viewer
                rejected_viewer = await client.post(
                    f"/api/v1/candidate-pools/{POOL_ID}/runs",
                    json={},
                    headers={
                        "Idempotency-Key": "viewer-key",
                        "X-CSRF-Token": "candidate-pool-csrf",
                    },
                )
                _assert_error(rejected_viewer, status_code=403, code="PERMISSION_DENIED")

                no_operator = _context(selected_operator=False)
                app.dependency_overrides[require_auth] = lambda: no_operator
                rejected_operator = await client.post(
                    f"/api/v1/candidate-pools/{POOL_ID}/runs",
                    json={},
                    headers={
                        "Idempotency-Key": "operator-key",
                        "X-CSRF-Token": "candidate-pool-csrf",
                    },
                )
                _assert_error(rejected_operator, status_code=409, code="OPERATOR_REQUIRED")
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())

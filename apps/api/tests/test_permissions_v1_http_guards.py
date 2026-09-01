"""Focused HTTP execution matrix for centralized Permissions V1 guards."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.http.dependencies import (
    get_auth_service,
    require_auth,
    require_effective_authorization,
    require_module,
    require_module_write,
    settings,
)
from backend_core.auth import (
    ALL_OF,
    ANY_OF,
    EXACT,
    AuthContext,
    AuthError,
    EffectiveAuthorizationContext,
    ModuleKey,
    ModuleRequirement,
    ResolvedDepartmentScope,
)
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, Operator
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient, Response


@dataclass(frozen=True, slots=True)
class GuardContext:
    raw: AuthContext
    effective: EffectiveAuthorizationContext


class CsrfService:
    def validate_csrf(self, _context: AuthContext, csrf_token: str | None) -> None:
        if csrf_token != "permissions-v1-csrf":
            raise AuthError(403, "CSRF_FAILED", "CSRF validation failed")


def _context(
    *,
    role: Role = Role.OPERATOR,
    grants: frozenset[ModuleKey] = frozenset(),
    ceiling: Role = Role.SUPER_ADMIN,
) -> GuardContext:
    department = Department(
        id=uuid4(),
        name="Permissions V1 guard HTTP",
        password_hash="not-used",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    operator = Operator(
        id=uuid4(),
        department_id=department.id,
        name="Permissions V1 operator",
        role=role,
        status=OperatorStatus.ACTIVE,
    )
    auth_session = AuthSession(
        id=uuid4(),
        department_id=department.id,
        operator_id=operator.id,
        token_hash="a" * 64,
        csrf_token_hash="b" * 64,
        ip="192.0.2.90",
        user_agent="permissions-v1-http-test",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        revoked_at=None,
    )
    raw = AuthContext(
        department=department,
        operator=operator,
        role=ceiling,
        auth_session=auth_session,
        department_role_ceiling=ceiling,
        effective_role=role,
    )
    return GuardContext(
        raw=raw,
        effective=EffectiveAuthorizationContext(
            department=department,
            operator=operator,
            department_role_ceiling=ceiling,
            effective_role=role,
            department_scope=ResolvedDepartmentScope(
                department_id=department.id,
                cross_department_override=False,
            ),
            auth_session=auth_session,
            authorized_modules=grants,
        ),
    )


def _app() -> FastAPI:
    app = FastAPI()

    @app.exception_handler(AuthError)
    async def auth_error_handler(_request: object, error: AuthError) -> JSONResponse:
        return JSONResponse(status_code=error.status_code, content={"code": error.code})

    def add_read(path: str, requirement: ModuleRequirement) -> None:
        async def endpoint(
            _context: object = Depends(require_module(requirement)),
        ) -> dict[str, bool]:
            return {"ok": True}

        app.add_api_route(path, endpoint, methods=["GET"])

    def add_write(path: str, requirement: ModuleRequirement) -> None:
        async def endpoint(
            _context: object = Depends(require_module_write(requirement)),
        ) -> dict[str, bool]:
            return {"ok": True}

        app.add_api_route(path, endpoint, methods=["POST"])

    for module in (
        ModuleKey.ADMIN,
        ModuleKey.DATA_COLLECTION,
        ModuleKey.CANDIDATE_POOLS,
        ModuleKey.CAMPAIGNS,
        ModuleKey.TODAY_OUTREACH,
        ModuleKey.DATA_UPDATES,
    ):
        add_read(f"/read/{module.value}", EXACT(module))
        add_write(f"/write/{module.value}", EXACT(module))
    add_read("/read/influencer-library", EXACT(ModuleKey.INFLUENCER_LIBRARY))
    add_read("/read/import-history", EXACT(ModuleKey.IMPORT_HISTORY))
    add_read(
        "/read/import-shared",
        ANY_OF(ModuleKey.DATA_COLLECTION, ModuleKey.IMPORT_HISTORY),
    )
    add_write(
        "/write/candidate-to-campaign",
        ALL_OF(ModuleKey.CAMPAIGNS, ModuleKey.CANDIDATE_POOLS),
    )
    return app


async def _request(
    app: FastAPI,
    context: GuardContext,
    method: str,
    path: str,
    *,
    csrf: bool = True,
) -> Response:
    app.dependency_overrides[require_auth] = lambda: context.raw
    app.dependency_overrides[require_effective_authorization] = lambda: context.effective
    app.dependency_overrides[get_auth_service] = CsrfService
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            if csrf:
                client.cookies.set(settings.csrf_cookie_name, "permissions-v1-csrf")
            headers = {"X-CSRF-Token": "permissions-v1-csrf"} if csrf else {}
            return await client.request(method, path, headers=headers)
    finally:
        app.dependency_overrides.clear()


def _assert(response: Response, status_code: int, code: str | None = None) -> None:
    assert response.status_code == status_code, response.text
    if code is not None:
        assert response.json()["code"] == code


def test_permissions_v1_http_guard_matrix_for_every_frozen_module_requirement() -> None:
    async def scenario() -> None:
        app = _app()
        mutating_modules = (
            ModuleKey.ADMIN,
            ModuleKey.DATA_COLLECTION,
            ModuleKey.CANDIDATE_POOLS,
            ModuleKey.CAMPAIGNS,
            ModuleKey.TODAY_OUTREACH,
            ModuleKey.DATA_UPDATES,
        )
        for module in mutating_modules:
            allowed = _context(
                role=Role.SUPER_ADMIN if module is ModuleKey.ADMIN else Role.OPERATOR,
                grants=frozenset({module}),
            )
            _assert(await _request(app, allowed, "GET", f"/read/{module.value}"), 200)
            _assert(await _request(app, allowed, "POST", f"/write/{module.value}"), 200)

            denied = _context(role=Role.OPERATOR)
            expected_code = (
                "PERMISSION_DENIED" if module is ModuleKey.ADMIN else "MODULE_ACCESS_DENIED"
            )
            _assert(
                await _request(app, denied, "GET", f"/read/{module.value}"),
                403,
                expected_code,
            )
            _assert(
                await _request(app, denied, "POST", f"/write/{module.value}"),
                403,
                expected_code,
            )

        viewer = _context(role=Role.VIEWER, grants=frozenset({ModuleKey.DATA_COLLECTION}))
        _assert(await _request(app, viewer, "GET", "/read/data_collection"), 200)
        _assert(
            await _request(app, viewer, "POST", "/write/data_collection"),
            403,
            "PERMISSION_DENIED",
        )
        _assert(
            await _request(app, viewer, "POST", "/write/data_collection", csrf=False),
            403,
            "PERMISSION_DENIED",
        )

        influencer_reader = _context(
            grants=frozenset({ModuleKey.INFLUENCER_LIBRARY}),
        )
        _assert(await _request(app, influencer_reader, "GET", "/read/influencer-library"), 200)
        _assert(
            await _request(app, _context(), "GET", "/read/influencer-library"),
            403,
            "MODULE_ACCESS_DENIED",
        )

        import_history = _context(grants=frozenset({ModuleKey.IMPORT_HISTORY}))
        data_collection = _context(grants=frozenset({ModuleKey.DATA_COLLECTION}))
        _assert(await _request(app, import_history, "GET", "/read/import-history"), 200)
        _assert(
            await _request(app, data_collection, "GET", "/read/import-history"),
            403,
            "MODULE_ACCESS_DENIED",
        )
        _assert(await _request(app, import_history, "GET", "/read/import-shared"), 200)
        _assert(await _request(app, data_collection, "GET", "/read/import-shared"), 200)
        _assert(
            await _request(app, _context(), "GET", "/read/import-shared"),
            403,
            "MODULE_ACCESS_DENIED",
        )

        both = _context(
            grants=frozenset({ModuleKey.CAMPAIGNS, ModuleKey.CANDIDATE_POOLS}),
        )
        _assert(await _request(app, both, "POST", "/write/candidate-to-campaign"), 200)
        _assert(
            await _request(
                app,
                _context(grants=frozenset({ModuleKey.CAMPAIGNS})),
                "POST",
                "/write/candidate-to-campaign",
            ),
            403,
            "MODULE_ACCESS_DENIED",
        )
        _assert(
            await _request(
                app,
                _context(
                    grants=frozenset(
                        {ModuleKey.CAMPAIGNS, ModuleKey.INFLUENCER_LIBRARY},
                    )
                ),
                "GET",
                "/read/today_outreach",
            ),
            403,
            "MODULE_ACCESS_DENIED",
        )
        _assert(
            await _request(
                app,
                _context(grants=frozenset({ModuleKey.DATA_UPDATES})),
                "POST",
                "/write/data_collection",
            ),
            403,
            "MODULE_ACCESS_DENIED",
        )

        csrf_context = _context(grants=frozenset({ModuleKey.DATA_UPDATES}))
        _assert(
            await _request(app, csrf_context, "POST", "/write/data_updates", csrf=False),
            403,
            "CSRF_FAILED",
        )

        no_operator = _context(grants=frozenset({ModuleKey.CAMPAIGNS}))

        async def missing_operator() -> EffectiveAuthorizationContext:
            raise AuthError(409, "OPERATOR_REQUIRED", "Select an operator first")

        app.dependency_overrides[require_auth] = lambda: no_operator.raw
        app.dependency_overrides[require_effective_authorization] = missing_operator
        app.dependency_overrides[get_auth_service] = CsrfService
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/read/campaigns")
            _assert(response, 409, "OPERATOR_REQUIRED")
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())

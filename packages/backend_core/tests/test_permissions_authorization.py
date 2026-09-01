"""Closed Permissions V1 module-requirement behavior."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend_core.auth.authorization import (
    ALL_OF,
    ANY_OF,
    EXACT,
    EffectiveAuthorizationContext,
    ResolvedDepartmentScope,
    require_module_mutation,
    require_module_read,
)
from backend_core.auth.enums import DepartmentStatus, ModuleKey, OperatorStatus, Role
from backend_core.auth.errors import AuthError
from backend_core.auth.models import AuthSession, Department, Operator


def _context(
    *,
    role: Role = Role.OPERATOR,
    grants: frozenset[ModuleKey] = frozenset(),
) -> EffectiveAuthorizationContext:
    department = Department(
        id=uuid4(),
        name="Permissions test",
        password_hash="not-used",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    operator = Operator(
        id=uuid4(),
        department_id=department.id,
        name="Permissions operator",
        role=role,
        status=OperatorStatus.ACTIVE,
    )
    session = AuthSession(
        id=uuid4(),
        department_id=department.id,
        operator_id=operator.id,
        token_hash="a" * 64,
        csrf_token_hash="b" * 64,
        ip="192.0.2.1",
        user_agent="pytest",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        revoked_at=None,
    )
    return EffectiveAuthorizationContext(
        department=department,
        operator=operator,
        department_role_ceiling=Role.SUPER_ADMIN,
        effective_role=role,
        department_scope=ResolvedDepartmentScope(
            department_id=department.id,
            cross_department_override=False,
        ),
        auth_session=session,
        authorized_modules=grants,
    )


def _assert_denied(callback: Callable[[], object]) -> None:
    try:
        callback()
    except AuthError as exc:
        assert exc.status_code == 403
        assert exc.code == "MODULE_ACCESS_DENIED"
    else:
        raise AssertionError("module access unexpectedly allowed")


def test_exact_requirement_allows_only_its_closed_module() -> None:
    context = _context(grants=frozenset({ModuleKey.CAMPAIGNS}))

    authorized = require_module_read(context, EXACT(ModuleKey.CAMPAIGNS))

    assert authorized is context
    _assert_denied(lambda: require_module_read(context, EXACT(ModuleKey.CANDIDATE_POOLS)))


def test_any_of_requirement_allows_either_grant_and_denies_neither() -> None:
    requirement = ANY_OF(ModuleKey.DATA_COLLECTION, ModuleKey.IMPORT_HISTORY)

    require_module_read(_context(grants=frozenset({ModuleKey.DATA_COLLECTION})), requirement)
    require_module_read(_context(grants=frozenset({ModuleKey.IMPORT_HISTORY})), requirement)
    _assert_denied(lambda: require_module_read(_context(), requirement))


def test_all_of_requirement_requires_every_closed_grant() -> None:
    requirement = ALL_OF(ModuleKey.CAMPAIGNS, ModuleKey.CANDIDATE_POOLS)

    require_module_read(
        _context(grants=frozenset({ModuleKey.CAMPAIGNS, ModuleKey.CANDIDATE_POOLS})),
        requirement,
    )
    _assert_denied(
        lambda: require_module_read(_context(grants=frozenset({ModuleKey.CAMPAIGNS})), requirement)
    )


def test_super_admin_has_implicit_module_access_without_persisted_grants() -> None:
    context = _context(role=Role.SUPER_ADMIN)

    require_module_read(context, EXACT(ModuleKey.ADMIN))
    require_module_read(context, ALL_OF(ModuleKey.CAMPAIGNS, ModuleKey.CANDIDATE_POOLS))


def test_viewer_read_is_grant_controlled_and_mutation_is_always_denied() -> None:
    context = _context(role=Role.VIEWER, grants=frozenset({ModuleKey.INFLUENCER_LIBRARY}))

    require_module_read(context, EXACT(ModuleKey.INFLUENCER_LIBRARY))
    try:
        require_module_mutation(context, EXACT(ModuleKey.INFLUENCER_LIBRARY))
    except AuthError as exc:
        assert exc.status_code == 403
        assert exc.code == "PERMISSION_DENIED"
    else:
        raise AssertionError("viewer mutation unexpectedly allowed")

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from backend_core.audit.enums import AuditAction
from backend_core.audit.models import AuditLog
from backend_core.auth.authorization import (
    EXACT,
    ModuleKey,
    require_module_mutation,
    require_module_read,
    viewer_mutation_allowed,
)
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role, role_at_or_below
from backend_core.auth.models import AuthSession, Department, DepartmentPermission, Operator
from backend_core.auth.security import hash_password, hash_token, verify_password
from backend_core.auth.service import (
    AuthError,
    AuthService,
    BootstrapService,
    OperatorCredentialSetupService,
)
from backend_core.auth.throttle import FailureState, LoginThrottleProtocol, RedisLoginThrottle
from backend_core.db import models as database_models  # noqa: F401
from fakeredis.aioredis import FakeRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fixtures.phase3a_auth_database import database_session


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: int) -> None:
        self.current += timedelta(**kwargs)


class MemoryThrottle(LoginThrottleProtocol):
    def __init__(self, clock: MutableClock) -> None:
        self.clock = clock
        self.failures: dict[tuple[UUID, str], tuple[int, datetime]] = {}

    async def is_locked(self, department_id: UUID, ip: str) -> bool:
        key = (department_id, ip)
        state = self.failures.get(key)
        if state is None:
            return False
        count, expires_at = state
        if expires_at <= self.clock.now():
            self.failures.pop(key)
            return False
        return count >= 5

    async def record_failure(self, department_id: UUID, ip: str) -> FailureState:
        key = (department_id, ip)
        previous = self.failures.get(key)
        count = 1 if previous is None else previous[0] + 1
        self.failures[key] = (count, self.clock.now() + timedelta(minutes=5))
        return FailureState(count=count, locked=count >= 5)

    async def clear(self, department_id: UUID, ip: str) -> None:
        self.failures.pop((department_id, ip), None)


async def add_department(
    session: AsyncSession,
    *,
    name: str = "Sales",
    password: str = "correct-horse-battery",
    operator_password: str = "operator-correct-horse",
    department_role: Role = Role.OPERATOR,
    operator_role: Role = Role.SUPER_ADMIN,
    status: DepartmentStatus = DepartmentStatus.ACTIVE,
) -> tuple[Department, Operator]:
    department = Department(
        name=name,
        password_hash=hash_password(password),
        status=status,
        session_days=30,
    )
    session.add(department)
    await session.flush()
    operator = Operator(
        department_id=department.id,
        name=f"{name} Manager",
        role=operator_role,
        status=OperatorStatus.ACTIVE,
        password_hash=hash_password(operator_password),
        credential_version=1,
    )
    session.add_all(
        [
            DepartmentPermission(department_id=department.id, role=department_role),
            operator,
        ]
    )
    await session.commit()
    return department, operator


def assert_auth_error(exc: AuthError, status_code: int, code: str) -> None:
    assert exc.status_code == status_code
    assert exc.code == code


def test_login_durations_token_hash_and_audit() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            clock = MutableClock()
            throttle = MemoryThrottle(clock)
            department, _ = await add_department(session)
            service = AuthService(session, throttle, clock=clock)

            regular = await service.login(
                department_id=department.id,
                password="correct-horse-battery",
                remember_me=False,
                ip="192.0.2.10",
                user_agent="pytest",
            )
            remembered = await service.login(
                department_id=department.id,
                password="correct-horse-battery",
                remember_me=True,
                ip="192.0.2.11",
                user_agent="pytest",
            )

            assert regular.auth_session.expires_at == clock.now() + timedelta(hours=12)
            assert remembered.auth_session.expires_at == clock.now() + timedelta(days=30)
            assert regular.auth_session.token_hash == hash_token(regular.session_token)
            assert regular.auth_session.token_hash != regular.session_token
            stored_tokens = list(await session.scalars(select(AuthSession.token_hash)))
            assert regular.session_token not in stored_tokens
            audits = list(
                await session.scalars(
                    select(AuditLog).where(AuditLog.action == AuditAction.LOGIN_SUCCESS)
                )
            )
            assert len(audits) == 2
            assert all(record.after is None for record in audits)

    asyncio.run(scenario())


def test_wrong_password_fifth_failure_lock_and_lock_expiry() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            clock = MutableClock()
            throttle = MemoryThrottle(clock)
            department, _ = await add_department(session)
            service = AuthService(session, throttle, clock=clock)

            for attempt in range(5):
                try:
                    await service.login(
                        department_id=department.id,
                        password="wrong-password",
                        remember_me=False,
                        ip="192.0.2.20",
                        user_agent="pytest",
                    )
                except AuthError as exc:
                    expected = "LOGIN_LOCKED" if attempt == 4 else "INVALID_CREDENTIALS"
                    assert_auth_error(exc, 423 if attempt == 4 else 401, expected)
                else:
                    raise AssertionError("wrong password was accepted")

            try:
                await service.login(
                    department_id=department.id,
                    password="correct-horse-battery",
                    remember_me=False,
                    ip="192.0.2.20",
                    user_agent="pytest",
                )
            except AuthError as exc:
                assert_auth_error(exc, 423, "LOGIN_LOCKED")
            else:
                raise AssertionError("locked login was accepted")

            # The lock is scoped by department + IP, so another IP remains usable.
            await service.login(
                department_id=department.id,
                password="correct-horse-battery",
                remember_me=False,
                ip="192.0.2.21",
                user_agent="pytest",
            )
            clock.advance(minutes=5, seconds=1)
            await service.login(
                department_id=department.id,
                password="correct-horse-battery",
                remember_me=False,
                ip="192.0.2.20",
                user_agent="pytest",
            )
            assert (department.id, "192.0.2.20") not in throttle.failures

            actions = list(await session.scalars(select(AuditLog.action)))
            assert actions.count(AuditAction.LOGIN_FAILED) == 5
            assert actions.count(AuditAction.LOGIN_LOCKED) == 2

    asyncio.run(scenario())


def test_disabled_department_cannot_log_in() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            clock = MutableClock()
            department, _ = await add_department(
                session,
                status=DepartmentStatus.DISABLED,
            )
            service = AuthService(session, MemoryThrottle(clock), clock=clock)
            try:
                await service.login(
                    department_id=department.id,
                    password="correct-horse-battery",
                    remember_me=False,
                    ip="192.0.2.30",
                    user_agent="pytest",
                )
            except AuthError as exc:
                assert_auth_error(exc, 403, "DEPARTMENT_DISABLED")
            else:
                raise AssertionError("disabled department was accepted")

    asyncio.run(scenario())


def test_session_expiry_logout_and_missing_authentication() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            clock = MutableClock()
            department, _ = await add_department(session)
            service = AuthService(session, MemoryThrottle(clock), clock=clock)
            result = await service.login(
                department_id=department.id,
                password="correct-horse-battery",
                remember_me=False,
                ip="192.0.2.40",
                user_agent="pytest",
            )
            context = await service.authenticate(result.session_token)
            await service.logout(context, ip="192.0.2.40", user_agent="pytest")
            try:
                await service.authenticate(result.session_token)
            except AuthError as exc:
                assert_auth_error(exc, 401, "INVALID_SESSION")
            else:
                raise AssertionError("revoked session remained valid")

            expiring = await service.login(
                department_id=department.id,
                password="correct-horse-battery",
                remember_me=False,
                ip="192.0.2.41",
                user_agent="pytest",
            )
            clock.advance(hours=12, seconds=1)
            try:
                await service.authenticate(expiring.session_token)
            except AuthError as exc:
                assert_auth_error(exc, 401, "SESSION_EXPIRED")
            else:
                raise AssertionError("expired session remained valid")

            try:
                await service.authenticate(None)
            except AuthError as exc:
                assert_auth_error(exc, 401, "AUTH_REQUIRED")
            else:
                raise AssertionError("missing authentication was accepted")

    asyncio.run(scenario())


def test_operator_selection_uses_valid_effective_role_without_changing_legacy_ceiling() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            clock = MutableClock()
            department, privileged_operator = await add_department(
                session,
                department_role=Role.SUPER_ADMIN,
            )
            other_department, other_operator = await add_department(
                session,
                name="Other",
                operator_password="other-operator-password",
            )
            service = AuthService(session, MemoryThrottle(clock), clock=clock)
            result = await service.login(
                department_id=department.id,
                password="correct-horse-battery",
                remember_me=True,
                ip="192.0.2.50",
                user_agent="pytest",
            )
            context = await service.authenticate(result.session_token)
            original_expiry = result.auth_session.expires_at
            selected_result = await service.select_operator(
                context,
                privileged_operator.id,
                "operator-correct-horse",
                ip="192.0.2.50",
                user_agent="pytest",
            )
            selected = selected_result.context
            assert privileged_operator.role == Role.SUPER_ADMIN
            assert selected.role == Role.SUPER_ADMIN
            assert selected.department_role_ceiling == Role.SUPER_ADMIN
            assert selected.effective_role == Role.SUPER_ADMIN
            assert selected.auth_session.operator_id == privileged_operator.id
            assert selected.auth_session.operator_credential_version == 1
            selected_expiry = selected.auth_session.expires_at
            if selected_expiry.tzinfo is None:
                selected_expiry = selected_expiry.replace(tzinfo=UTC)
            if original_expiry.tzinfo is None:
                original_expiry = original_expiry.replace(tzinfo=UTC)
            assert selected_expiry == original_expiry
            assert selected_result.session_token != result.session_token
            assert selected_result.csrf_token != result.csrf_token
            assert selected.auth_session.token_hash == hash_token(selected_result.session_token)
            await session.refresh(result.auth_session)
            revoked_at = result.auth_session.revoked_at
            assert revoked_at is not None
            if revoked_at.tzinfo is None:
                revoked_at = revoked_at.replace(tzinfo=UTC)
            assert revoked_at == clock.now()
            with pytest.raises(AuthError) as old_session:
                await service.authenticate(result.session_token)
            assert_auth_error(old_session.value, 401, "INVALID_SESSION")

            try:
                await service.select_operator(
                    selected,
                    other_operator.id,
                    "other-operator-password",
                    ip="192.0.2.50",
                    user_agent="pytest",
                )
            except AuthError as exc:
                assert_auth_error(exc, 401, "INVALID_OPERATOR_CREDENTIALS")
            else:
                raise AssertionError("cross-department operator was accepted")
            assert other_department.id != department.id

            selected_audit = await session.scalar(
                select(AuditLog).where(AuditLog.action == AuditAction.OPERATOR_AUTHENTICATED)
            )
            assert selected_audit is not None
            assert selected_audit.operator_id is None
            assert selected_audit.entity_type == "operator"
            assert selected_audit.entity_id == privileged_operator.id

    asyncio.run(scenario())


def test_operator_auth_audit_separates_initiator_target_and_session_provenance() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            clock = MutableClock()
            department, operator_a = await add_department(
                session,
                department_role=Role.SUPER_ADMIN,
                operator_password="operator-a-password",
            )
            operator_b = Operator(
                department_id=department.id,
                name="Operator B",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
                password_hash=hash_password("operator-b-password"),
                credential_version=1,
            )
            session.add(operator_b)
            await session.commit()
            service = AuthService(session, MemoryThrottle(clock), clock=clock)
            login = await service.login(
                department_id=department.id,
                password="correct-horse-battery",
                remember_me=False,
                ip="192.0.2.80",
                user_agent="department-login-client",
            )
            selected_a = await service.select_operator(
                await service.authenticate(login.session_token),
                operator_a.id,
                "operator-a-password",
                ip="192.0.2.81",
                user_agent="operator-a-request",
            )
            selected_b = await service.select_operator(
                selected_a.context,
                operator_b.id,
                "operator-b-password",
                ip="192.0.2.82",
                user_agent="operator-b-request",
            )

            first_audit = await session.scalar(
                select(AuditLog).where(
                    AuditLog.action == AuditAction.OPERATOR_AUTHENTICATED,
                    AuditLog.entity_id == operator_a.id,
                )
            )
            second_audit = await session.scalar(
                select(AuditLog).where(
                    AuditLog.action == AuditAction.OPERATOR_AUTHENTICATED,
                    AuditLog.entity_id == operator_b.id,
                )
            )
            assert first_audit is not None
            assert first_audit.operator_id is None
            assert first_audit.ip == "192.0.2.81"
            assert first_audit.user_agent == "operator-a-request"
            assert second_audit is not None
            assert second_audit.operator_id == operator_a.id
            assert second_audit.entity_type == "operator"
            assert second_audit.entity_id == operator_b.id
            assert second_audit.ip == "192.0.2.82"
            assert second_audit.user_agent == "operator-b-request"

            assert selected_b.auth_session.ip == "192.0.2.80"
            assert selected_b.auth_session.user_agent == "department-login-client"

    asyncio.run(scenario())


def test_operator_auth_requires_exact_password_and_initialized_credential() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            clock = MutableClock()
            department, operator = await add_department(
                session,
                department_role=Role.SUPER_ADMIN,
                operator_password="primary-operator-password",
            )
            other_operator = Operator(
                department_id=department.id,
                name="Other credential owner",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
                password_hash=hash_password("other-operator-password"),
                credential_version=1,
            )
            setup_required = Operator(
                department_id=department.id,
                name="Migrated without credential",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
                password_hash=None,
                credential_version=0,
            )
            session.add_all((other_operator, setup_required))
            await session.commit()
            service = AuthService(session, MemoryThrottle(clock), clock=clock)
            login = await service.login(
                department_id=department.id,
                password="correct-horse-battery",
                remember_me=False,
                ip="192.0.2.57",
                user_agent="pytest",
            )
            context = await service.authenticate(login.session_token)

            for rejected_password in (
                "wrong-operator-password",
                "correct-horse-battery",
                "other-operator-password",
            ):
                with pytest.raises(AuthError) as rejected:
                    await service.select_operator(
                        context,
                        operator.id,
                        rejected_password,
                        ip="192.0.2.57",
                        user_agent="pytest",
                    )
                assert_auth_error(rejected.value, 401, "INVALID_OPERATOR_CREDENTIALS")
                await session.refresh(login.auth_session)
                assert login.auth_session.revoked_at is None

            selected = await service.select_operator(
                context,
                operator.id,
                "primary-operator-password",
                ip="192.0.2.57",
                user_agent="pytest",
            )
            with pytest.raises(AuthError) as missing_setup:
                await service.select_operator(
                    selected.context,
                    setup_required.id,
                    "not-an-initialized-password",
                    ip="192.0.2.57",
                    user_agent="pytest",
                )
            assert_auth_error(missing_setup.value, 409, "CREDENTIAL_SETUP_REQUIRED")
            await session.refresh(selected.auth_session)
            assert selected.auth_session.revoked_at is None

            audits = list(
                await session.scalars(
                    select(AuditLog).where(
                        AuditLog.action.in_(
                            (
                                AuditAction.OPERATOR_AUTH_FAILED,
                                AuditAction.OPERATOR_AUTHENTICATED,
                            )
                        )
                    )
                )
            )
            serialized_audits = repr(
                [(audit.action.value, audit.before, audit.after) for audit in audits]
            )
            for secret in (
                "primary-operator-password",
                "wrong-operator-password",
                "correct-horse-battery",
                "other-operator-password",
                "not-an-initialized-password",
            ):
                assert secret not in serialized_audits
            assert [audit.action for audit in audits].count(AuditAction.OPERATOR_AUTH_FAILED) == 4
            assert [audit.action for audit in audits].count(AuditAction.OPERATOR_AUTHENTICATED) == 1

    asyncio.run(scenario())


def test_operator_authentication_target_switching_hits_department_ip_budget() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            clock = MutableClock()
            department, exact_target = await add_department(
                session,
                department_role=Role.SUPER_ADMIN,
                operator_password="exact-target-password",
            )
            service = AuthService(session, MemoryThrottle(clock), clock=clock)
            login = await service.login(
                department_id=department.id,
                password="correct-horse-battery",
                remember_me=False,
                ip="192.0.2.71",
                user_agent="pytest",
            )
            context = await service.authenticate(login.session_token)

            sprayed_targets = [uuid4() for _ in range(5)]
            for attempt, sprayed_target in enumerate(sprayed_targets):
                with pytest.raises(AuthError) as rejected:
                    await service.select_operator(
                        context,
                        sprayed_target,
                        "wrong-sprayed-password",
                        ip="192.0.2.71",
                        user_agent="pytest",
                    )
                expected_code = "LOGIN_LOCKED" if attempt == 4 else "INVALID_OPERATOR_CREDENTIALS"
                assert_auth_error(rejected.value, 423 if attempt == 4 else 401, expected_code)

            await session.refresh(login.auth_session)
            assert login.auth_session.operator_id is None
            assert login.auth_session.operator_credential_version is None
            assert login.auth_session.revoked_at is None

            # The aggregate lock rejects both another UUID and a valid target
            # without adding attacker-amplified Audit rows.
            with pytest.raises(AuthError) as aggregate_locked:
                await service.select_operator(
                    context,
                    exact_target.id,
                    "exact-target-password",
                    ip="192.0.2.71",
                    user_agent="pytest",
                )
            assert_auth_error(aggregate_locked.value, 423, "LOGIN_LOCKED")

            failures = list(
                await session.scalars(
                    select(AuditLog).where(
                        AuditLog.action == AuditAction.OPERATOR_AUTH_FAILED,
                    )
                )
            )
            assert len(failures) == 5
            assert {failure.entity_id for failure in failures} == set(sprayed_targets)
            assert failures[-1].after == {
                "reason": "invalid_credentials",
                "failure_count": 1,
                "aggregate_failure_count": 5,
            }

            selected = await service.select_operator(
                context,
                exact_target.id,
                "exact-target-password",
                ip="192.0.2.72",
                user_agent="pytest",
            )
            assert selected.context.operator is not None
            assert selected.context.operator.id == exact_target.id
            assert selected.context.auth_session.operator_credential_version == 1

    asyncio.run(scenario())


def test_bound_session_rejects_legacy_or_mismatched_operator_credential_version() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            clock = MutableClock()
            department, operator = await add_department(
                session,
                department_role=Role.SUPER_ADMIN,
                operator_password="versioned-operator-password",
            )
            service = AuthService(session, MemoryThrottle(clock), clock=clock)

            async def bind(ip: str):
                login = await service.login(
                    department_id=department.id,
                    password="correct-horse-battery",
                    remember_me=True,
                    ip=ip,
                    user_agent="pytest",
                )
                return await service.select_operator(
                    await service.authenticate(login.session_token),
                    operator.id,
                    "versioned-operator-password",
                    ip=ip,
                    user_agent="pytest",
                )

            legacy = await bind("192.0.2.58")
            legacy.auth_session.operator_credential_version = None
            await session.commit()
            with pytest.raises(AuthError) as legacy_rejected:
                await service.authenticate(legacy.session_token)
            assert_auth_error(legacy_rejected.value, 401, "INVALID_SESSION")
            await session.refresh(legacy.auth_session)
            assert legacy.auth_session.revoked_at is not None

            mismatch = await bind("192.0.2.59")
            operator.credential_version += 1
            await session.commit()
            with pytest.raises(AuthError) as mismatch_rejected:
                await service.authenticate(mismatch.session_token)
            assert_auth_error(mismatch_rejected.value, 401, "INVALID_SESSION")
            await session.refresh(mismatch.auth_session)
            assert mismatch.auth_session.revoked_at is not None

    asyncio.run(scenario())


def test_permissions_v1_role_order_module_keys_and_viewer_mutation_predicate() -> None:
    assert role_at_or_below(Role.VIEWER, Role.VIEWER)
    assert role_at_or_below(Role.VIEWER, Role.OPERATOR)
    assert role_at_or_below(Role.OPERATOR, Role.MANAGER)
    assert role_at_or_below(Role.MANAGER, Role.SUPER_ADMIN)
    assert not role_at_or_below(Role.OPERATOR, Role.VIEWER)
    assert not role_at_or_below(Role.MANAGER, Role.OPERATOR)
    assert not role_at_or_below(Role.SUPER_ADMIN, Role.MANAGER)
    assert {module_key.value for module_key in ModuleKey} == {
        "today_outreach",
        "campaigns",
        "candidate_pools",
        "influencer_library",
        "data_collection",
        "import_history",
        "data_updates",
        "admin",
    }
    try:
        ModuleKey("arbitrary_permission")
    except ValueError:
        pass
    else:
        raise AssertionError("ModuleKey accepted an arbitrary permission string")
    assert not viewer_mutation_allowed(Role.VIEWER)
    assert viewer_mutation_allowed(Role.OPERATOR)
    assert viewer_mutation_allowed(Role.MANAGER)
    assert viewer_mutation_allowed(Role.SUPER_ADMIN)


def test_valid_operator_roles_resolve_as_effective_role() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            clock = MutableClock()
            department, manager = await add_department(
                session,
                department_role=Role.MANAGER,
                operator_role=Role.MANAGER,
            )
            operator = Operator(
                department_id=department.id,
                name="Below ceiling",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
                password_hash=hash_password("below-ceiling-password"),
                credential_version=1,
            )
            session.add(operator)
            await session.commit()
            service = AuthService(session, MemoryThrottle(clock), clock=clock)
            login = await service.login(
                department_id=department.id,
                password="correct-horse-battery",
                remember_me=False,
                ip="192.0.2.51",
                user_agent="pytest",
            )
            unbound = await service.authenticate(login.session_token)

            equal_result = await service.select_operator(
                unbound,
                manager.id,
                "operator-correct-horse",
                ip="192.0.2.51",
                user_agent="pytest",
            )
            equal = equal_result.context
            assert equal.department_role_ceiling == Role.MANAGER
            assert equal.effective_role == Role.MANAGER

            below_result = await service.select_operator(
                equal,
                operator.id,
                "below-ceiling-password",
                ip="192.0.2.51",
                user_agent="pytest",
            )
            below = below_result.context
            assert below.department_role_ceiling == Role.MANAGER
            assert below.effective_role == Role.OPERATOR
            effective = await service.resolve_effective_authorization(below)
            assert effective.effective_role == Role.OPERATOR
            assert effective.department_scope.department_id == department.id
            assert not effective.department_scope.cross_department_override

    asyncio.run(scenario())


def test_above_ceiling_selection_does_not_bind_session() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            clock = MutableClock()
            department, above_ceiling = await add_department(
                session,
                department_role=Role.MANAGER,
                operator_role=Role.SUPER_ADMIN,
            )
            service = AuthService(session, MemoryThrottle(clock), clock=clock)
            login = await service.login(
                department_id=department.id,
                password="correct-horse-battery",
                remember_me=False,
                ip="192.0.2.52",
                user_agent="pytest",
            )
            context = await service.authenticate(login.session_token)
            try:
                await service.select_operator(
                    context,
                    above_ceiling.id,
                    "operator-correct-horse",
                    ip="192.0.2.52",
                    user_agent="pytest",
                )
            except AuthError as exc:
                assert_auth_error(exc, 403, "ROLE_CEILING_EXCEEDED")
            else:
                raise AssertionError("above-ceiling operator was selected")
            await session.refresh(login.auth_session)
            assert login.auth_session.operator_id is None
            assert login.auth_session.revoked_at is None

    asyncio.run(scenario())


def test_bound_operator_ceiling_or_status_change_revokes_session_fail_closed() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            clock = MutableClock()
            department, operator = await add_department(
                session,
                department_role=Role.MANAGER,
                operator_role=Role.OPERATOR,
            )
            service = AuthService(session, MemoryThrottle(clock), clock=clock)
            login = await service.login(
                department_id=department.id,
                password="correct-horse-battery",
                remember_me=False,
                ip="192.0.2.53",
                user_agent="pytest",
            )
            context = await service.authenticate(login.session_token)
            selected = await service.select_operator(
                context,
                operator.id,
                "operator-correct-horse",
                ip="192.0.2.53",
                user_agent="pytest",
            )
            operator.role = Role.SUPER_ADMIN
            await session.commit()

            try:
                await service.authenticate(selected.session_token)
            except AuthError as exc:
                assert_auth_error(exc, 403, "ROLE_CEILING_EXCEEDED")
            else:
                raise AssertionError("above-ceiling bound session remained valid")
            await session.refresh(selected.auth_session)
            assert selected.auth_session.revoked_at is not None

            try:
                await service.authenticate(selected.session_token)
            except AuthError as exc:
                assert_auth_error(exc, 401, "INVALID_SESSION")
            else:
                raise AssertionError("revoked session remained valid")

            second_login = await service.login(
                department_id=department.id,
                password="correct-horse-battery",
                remember_me=False,
                ip="192.0.2.54",
                user_agent="pytest",
            )
            operator.role = Role.OPERATOR
            await session.commit()
            second_context = await service.authenticate(second_login.session_token)
            second_selected = await service.select_operator(
                second_context,
                operator.id,
                "operator-correct-horse",
                ip="192.0.2.54",
                user_agent="pytest",
            )
            operator.status = OperatorStatus.DISABLED
            await session.commit()
            try:
                await service.authenticate(second_selected.session_token)
            except AuthError as exc:
                assert_auth_error(exc, 401, "INVALID_SESSION")
            else:
                raise AssertionError("disabled bound operator formed a business actor")
            await session.refresh(second_selected.auth_session)
            assert second_selected.auth_session.revoked_at is not None

    asyncio.run(scenario())


def test_operator_role_change_is_resolved_on_the_next_request() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            clock = MutableClock()
            department, operator = await add_department(
                session,
                department_role=Role.MANAGER,
                operator_role=Role.OPERATOR,
            )
            service = AuthService(session, MemoryThrottle(clock), clock=clock)
            login = await service.login(
                department_id=department.id,
                password="correct-horse-battery",
                remember_me=False,
                ip="192.0.2.55",
                user_agent="pytest",
            )
            context = await service.authenticate(login.session_token)
            selected = await service.select_operator(
                context,
                operator.id,
                "operator-correct-horse",
                ip="192.0.2.55",
                user_agent="pytest",
            )
            operator.role = Role.MANAGER
            await session.commit()

            refreshed = await service.authenticate(selected.session_token)
            assert refreshed.department_role_ceiling == Role.MANAGER
            assert refreshed.effective_role == Role.MANAGER
            effective = await service.resolve_effective_authorization(refreshed)
            assert effective.effective_role == Role.MANAGER

    asyncio.run(scenario())


def test_effective_authorization_reloads_grants_role_and_status_per_business_request() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            clock = MutableClock()
            department, operator = await add_department(
                session,
                department_role=Role.SUPER_ADMIN,
                operator_role=Role.OPERATOR,
            )
            service = AuthService(session, MemoryThrottle(clock), clock=clock)
            login = await service.login(
                department_id=department.id,
                password="correct-horse-battery",
                remember_me=False,
                ip="192.0.2.56",
                user_agent="pytest",
            )
            selected = await service.select_operator(
                await service.authenticate(login.session_token),
                operator.id,
                "operator-correct-horse",
                ip="192.0.2.56",
                user_agent="pytest",
            )
            assert selected.context.effective_role is Role.OPERATOR

            other_department, _ = await add_department(session, name="Other department")
            try:
                await service.resolve_effective_authorization(
                    await service.authenticate(selected.session_token),
                    department_id=other_department.id,
                )
            except AuthError as exc:
                assert_auth_error(exc, 404, "RESOURCE_NOT_FOUND")
            else:
                raise AssertionError("selected non-SA gained cross-Department authority")

            await service.repository.replace_operator_module_grants(
                operator_id=operator.id,
                department_id=department.id,
                module_keys=[ModuleKey.CAMPAIGNS],
            )
            await session.commit()
            first = await service.resolve_effective_authorization(
                await service.authenticate(selected.session_token)
            )
            require_module_read(first, EXACT(ModuleKey.CAMPAIGNS))
            try:
                require_module_read(first, EXACT(ModuleKey.ADMIN))
            except AuthError as exc:
                assert_auth_error(exc, 403, "PERMISSION_DENIED")
            else:
                raise AssertionError("selected non-SA gained admin authority from SA ceiling")

            await service.repository.replace_operator_module_grants(
                operator_id=operator.id,
                department_id=department.id,
                module_keys=[],
            )
            await session.commit()
            after_removal = await service.resolve_effective_authorization(
                await service.authenticate(selected.session_token)
            )
            try:
                require_module_read(after_removal, EXACT(ModuleKey.CAMPAIGNS))
            except AuthError as exc:
                assert_auth_error(exc, 403, "MODULE_ACCESS_DENIED")
            else:
                raise AssertionError("removed grant remained authorized")

            await service.repository.replace_operator_module_grants(
                operator_id=operator.id,
                department_id=department.id,
                module_keys=[ModuleKey.DATA_COLLECTION],
            )
            await session.commit()
            after_addition = await service.resolve_effective_authorization(
                await service.authenticate(selected.session_token)
            )
            require_module_read(after_addition, EXACT(ModuleKey.DATA_COLLECTION))

            operator.role = Role.VIEWER
            await session.commit()
            viewer = await service.resolve_effective_authorization(
                await service.authenticate(selected.session_token)
            )
            assert viewer.effective_role is Role.VIEWER
            try:
                require_module_read(viewer, EXACT(ModuleKey.ADMIN))
            except AuthError as exc:
                assert_auth_error(exc, 403, "PERMISSION_DENIED")
            else:
                raise AssertionError("Viewer gained admin authority from SA ceiling")
            try:
                require_module_mutation(viewer, EXACT(ModuleKey.DATA_COLLECTION))
            except AuthError as exc:
                assert_auth_error(exc, 403, "PERMISSION_DENIED")
            else:
                raise AssertionError("Viewer mutation remained authorized")

            operator.status = OperatorStatus.DISABLED
            await session.commit()
            try:
                await service.authenticate(selected.session_token)
            except AuthError as exc:
                assert_auth_error(exc, 401, "INVALID_SESSION")
            else:
                raise AssertionError("disabled bound Operator retained business access")

    asyncio.run(scenario())


def test_password_reset_revokes_all_department_sessions() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            clock = MutableClock()
            admin_department, admin_operator = await add_department(
                session,
                name="Admin",
                department_role=Role.SUPER_ADMIN,
            )
            target, _ = await add_department(session, name="Target")
            service = AuthService(session, MemoryThrottle(clock), clock=clock)
            admin_login = await service.login(
                department_id=admin_department.id,
                password="correct-horse-battery",
                remember_me=False,
                ip="192.0.2.60",
                user_agent="pytest",
            )
            admin_context = (
                await service.select_operator(
                    await service.authenticate(admin_login.session_token),
                    admin_operator.id,
                    "operator-correct-horse",
                    ip="192.0.2.60",
                    user_agent="pytest",
                )
            ).context
            target_logins = [
                await service.login(
                    department_id=target.id,
                    password="correct-horse-battery",
                    remember_me=True,
                    ip=f"192.0.2.{61 + index}",
                    user_agent="pytest",
                )
                for index in range(2)
            ]

            revoked = await service.reset_department_password(
                admin_context,
                department_id=target.id,
                new_password="new-secure-password",
                ip="192.0.2.60",
                user_agent="pytest",
            )
            assert revoked == 2
            for login in target_logins:
                try:
                    await service.authenticate(login.session_token)
                except AuthError as exc:
                    assert_auth_error(exc, 401, "INVALID_SESSION")
                else:
                    raise AssertionError("password reset left a target session active")

            reset_audit = await session.scalar(
                select(AuditLog).where(AuditLog.action == AuditAction.PASSWORD_RESET)
            )
            assert reset_audit is not None
            assert reset_audit.after == {"revoked_sessions": 2}

    asyncio.run(scenario())


def test_operator_credential_setup_initializes_exact_legacy_target_once() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            clock = MutableClock()
            department, _bootstrap_operator = await add_department(
                session,
                name="Credential Setup",
                department_role=Role.SUPER_ADMIN,
            )
            legacy = Operator(
                department_id=department.id,
                name="Legacy First Admin",
                role=Role.SUPER_ADMIN,
                status=OperatorStatus.ACTIVE,
                password_hash=None,
                credential_version=0,
            )
            session.add(legacy)
            await session.flush()
            legacy_session = AuthSession(
                department_id=department.id,
                operator_id=legacy.id,
                operator_credential_version=None,
                token_hash="legacy-setup-token",
                csrf_token_hash="legacy-setup-csrf",
                ip="127.0.0.1",
                user_agent="legacy-session",
                expires_at=clock.now() + timedelta(days=30),
                revoked_at=None,
            )
            session.add(legacy_session)
            await session.commit()
            setup = OperatorCredentialSetupService(session, clock=clock)

            legacy.role = Role.MANAGER
            await session.commit()
            with pytest.raises(AuthError) as non_admin_target:
                await setup.setup(
                    department_id=department.id,
                    operator_id=legacy.id,
                    password="initialized-legacy-password",
                    require_super_admin=True,
                )
            assert_auth_error(non_admin_target.value, 409, "SUPER_ADMIN_REQUIRED")
            await session.refresh(department)
            await session.refresh(legacy)
            assert legacy.password_hash is None
            assert legacy.credential_version == 0
            legacy.role = Role.SUPER_ADMIN
            await session.commit()

            with pytest.raises(AuthError) as department_reuse:
                await setup.setup(
                    department_id=department.id,
                    operator_id=legacy.id,
                    password="correct-horse-battery",
                )
            assert_auth_error(department_reuse.value, 422, "PASSWORD_REUSE_FORBIDDEN")
            await session.refresh(department)
            await session.refresh(legacy)
            await session.refresh(legacy_session)
            assert legacy.password_hash is None
            assert legacy.credential_version == 0
            assert legacy_session.revoked_at is None

            result = await setup.setup(
                department_id=department.id,
                operator_id=legacy.id,
                password="initialized-legacy-password",
                require_super_admin=True,
            )
            assert result.department.id == department.id
            assert result.operator.id == legacy.id
            assert result.revoked_sessions == 1
            await session.refresh(legacy)
            await session.refresh(legacy_session)
            assert legacy.password_hash is not None
            assert legacy.password_hash != "initialized-legacy-password"
            assert verify_password(legacy.password_hash, "initialized-legacy-password")
            assert legacy.credential_version == 1
            assert legacy_session.revoked_at is not None

            setup_audit = await session.scalar(
                select(AuditLog).where(AuditLog.action == AuditAction.OPERATOR_CREDENTIAL_SET)
            )
            assert setup_audit is not None
            assert setup_audit.department_id == department.id
            assert setup_audit.operator_id is None
            assert setup_audit.entity_id == legacy.id
            assert setup_audit.ip == "cli"
            assert setup_audit.user_agent == "setup-operator-credential"
            assert setup_audit.after == {"revoked_sessions": 1}
            assert "initialized-legacy-password" not in repr(
                (setup_audit.before, setup_audit.after)
            )

            initialized_hash = legacy.password_hash
            with pytest.raises(AuthError) as second_setup:
                await setup.setup(
                    department_id=department.id,
                    operator_id=legacy.id,
                    password="second-initialization-password",
                )
            assert_auth_error(
                second_setup.value,
                409,
                "CREDENTIAL_ALREADY_INITIALIZED",
            )
            await session.refresh(legacy)
            assert legacy.password_hash == initialized_hash
            assert legacy.credential_version == 1
            assert (
                len(
                    list(
                        await session.scalars(
                            select(AuditLog).where(
                                AuditLog.action == AuditAction.OPERATOR_CREDENTIAL_SET
                            )
                        )
                    )
                )
                == 1
            )

    asyncio.run(scenario())


def test_bootstrap_admin_is_one_time_and_has_no_default_credentials() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            bootstrap = BootstrapService(session)
            department, operator = await bootstrap.create_admin(
                department_name="Administration",
                operator_name="Initial Admin",
                department_password="provided-out-of-band",
                operator_password="operator-provided-out-of-band",
            )
            assert department.password_hash != "provided-out-of-band"
            assert verify_password(department.password_hash, "provided-out-of-band")
            assert operator.password_hash != "operator-provided-out-of-band"
            assert operator.password_hash is not None
            assert verify_password(operator.password_hash, "operator-provided-out-of-band")
            assert operator.credential_version == 1
            assert operator.role == Role.SUPER_ADMIN
            try:
                await bootstrap.create_admin(
                    department_name="Another Admin",
                    operator_name="Another Operator",
                    department_password="another-out-of-band-password",
                    operator_password="another-operator-password",
                )
            except AuthError as exc:
                assert_auth_error(exc, 409, "ADMIN_ALREADY_BOOTSTRAPPED")
            else:
                raise AssertionError("bootstrap-admin was allowed to run twice")

    asyncio.run(scenario())


def test_unknown_department_uses_generic_credentials_error() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            clock = MutableClock()
            service = AuthService(session, MemoryThrottle(clock), clock=clock)
            try:
                await service.login(
                    department_id=uuid4(),
                    password="anything",
                    remember_me=False,
                    ip="192.0.2.70",
                    user_agent="pytest",
                )
            except AuthError as exc:
                assert_auth_error(exc, 401, "INVALID_CREDENTIALS")
            else:
                raise AssertionError("unknown department was accepted")

    asyncio.run(scenario())


def test_redis_throttle_is_scoped_and_cleared_by_department_and_ip() -> None:
    async def scenario() -> None:
        redis = FakeRedis(decode_responses=True)
        throttle = RedisLoginThrottle(redis, max_failures=5, lock_seconds=300)
        department_id = uuid4()
        other_department_id = uuid4()

        for expected_count in range(1, 6):
            state = await throttle.record_failure(department_id, "192.0.2.80")
            assert state.count == expected_count
        assert await throttle.is_locked(department_id, "192.0.2.80")
        assert not await throttle.is_locked(department_id, "192.0.2.81")
        assert not await throttle.is_locked(other_department_id, "192.0.2.80")

        await throttle.clear(department_id, "192.0.2.80")
        assert not await throttle.is_locked(department_id, "192.0.2.80")
        await redis.aclose()

    asyncio.run(scenario())

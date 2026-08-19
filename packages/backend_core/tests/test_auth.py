import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from backend_core.audit.enums import AuditAction
from backend_core.audit.models import AuditLog
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, DepartmentPermission, Operator
from backend_core.auth.security import hash_password, hash_token
from backend_core.auth.service import AuthContext, AuthError, AuthService, BootstrapService
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
    department_role: Role = Role.OPERATOR,
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
        role=Role.SUPER_ADMIN,
        status=OperatorStatus.ACTIVE,
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


def test_operator_selection_is_attribution_not_privilege_escalation() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            clock = MutableClock()
            department, privileged_operator = await add_department(
                session,
                department_role=Role.VIEWER,
            )
            other_department, other_operator = await add_department(session, name="Other")
            service = AuthService(session, MemoryThrottle(clock), clock=clock)
            result = await service.login(
                department_id=department.id,
                password="correct-horse-battery",
                remember_me=False,
                ip="192.0.2.50",
                user_agent="pytest",
            )
            context = await service.authenticate(result.session_token)
            selected = await service.select_operator(
                context,
                privileged_operator.id,
                ip="192.0.2.50",
                user_agent="pytest",
            )
            assert privileged_operator.role == Role.SUPER_ADMIN
            assert selected.role == Role.VIEWER
            assert selected.auth_session.operator_id == privileged_operator.id

            try:
                await service.select_operator(
                    selected,
                    other_operator.id,
                    ip="192.0.2.50",
                    user_agent="pytest",
                )
            except AuthError as exc:
                assert_auth_error(exc, 404, "OPERATOR_NOT_FOUND")
            else:
                raise AssertionError("cross-department operator was accepted")
            assert other_department.id != department.id

            selected_audit = await session.scalar(
                select(AuditLog).where(AuditLog.action == AuditAction.OPERATOR_SELECTED)
            )
            assert selected_audit is not None
            assert selected_audit.operator_id == privileged_operator.id

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
            admin_context = AuthContext(
                department=admin_department,
                operator=admin_operator,
                role=Role.SUPER_ADMIN,
                auth_session=admin_login.auth_session,
            )
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


def test_bootstrap_admin_is_one_time_and_has_no_default_credentials() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            bootstrap = BootstrapService(session)
            department, operator = await bootstrap.create_admin(
                department_name="Administration",
                operator_name="Initial Admin",
                password="provided-out-of-band",
            )
            assert department.password_hash != "provided-out-of-band"
            assert operator.role == Role.SUPER_ADMIN
            try:
                await bootstrap.create_admin(
                    department_name="Another Admin",
                    operator_name="Another Operator",
                    password="another-out-of-band-password",
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

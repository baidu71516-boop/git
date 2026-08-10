"""Department authentication, operator attribution, and session rules."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Never, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.audit.repository import AuditRepository
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, DepartmentPermission, Operator
from backend_core.auth.repository import AuthRepository
from backend_core.auth.security import (
    generate_opaque_token,
    hash_password,
    hash_token,
    timing_safe_dummy_password_hash,
    token_matches,
    verify_password,
)
from backend_core.auth.throttle import LoginThrottleProtocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class AuthError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class LoginResult:
    department: Department
    role: Role
    auth_session: AuthSession
    session_token: str
    csrf_token: str


@dataclass(frozen=True)
class AuthContext:
    department: Department
    operator: Operator | None
    role: Role
    auth_session: AuthSession


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class AuthService:
    """The only implementation of Phase 1A authentication business rules."""

    def __init__(
        self,
        session: AsyncSession,
        throttle: LoginThrottleProtocol,
        *,
        default_hours: int = 12,
        remember_days: int = 30,
        clock: Clock | None = None,
    ) -> None:
        self.session = session
        self.repository = AuthRepository(session)
        self.audit = AuditRepository(session)
        self.throttle = throttle
        self.default_hours = default_hours
        self.remember_days = remember_days
        self.clock = clock or SystemClock()

    async def login(
        self,
        *,
        department_id: UUID,
        password: str,
        remember_me: bool,
        ip: str,
        user_agent: str,
    ) -> LoginResult:
        department = await self.repository.get_department(department_id)

        if await self.throttle.is_locked(department_id, ip):
            self.audit.add(
                action=AuditAction.LOGIN_LOCKED,
                result=AuditResult.DENIED,
                department_id=department.id if department else None,
                operator_id=None,
                ip=ip,
                user_agent=user_agent,
            )
            await self.session.commit()
            raise AuthError(423, "LOGIN_LOCKED", "Too many failed attempts; try again later")

        if department is None:
            verify_password(timing_safe_dummy_password_hash(), password)
            await self._record_failed_login(department_id, None, ip, user_agent)

        if department.status != DepartmentStatus.ACTIVE:
            self.audit.add(
                action=AuditAction.LOGIN_FAILED,
                result=AuditResult.DENIED,
                department_id=department.id,
                operator_id=None,
                ip=ip,
                user_agent=user_agent,
                after={"reason": "department_disabled"},
            )
            await self.session.commit()
            raise AuthError(403, "DEPARTMENT_DISABLED", "Department is disabled")

        if not verify_password(department.password_hash, password):
            await self._record_failed_login(department_id, department, ip, user_agent)

        permission = await self.repository.get_department_permission(department.id)
        if permission is None:
            self.audit.add(
                action=AuditAction.LOGIN_FAILED,
                result=AuditResult.DENIED,
                department_id=department.id,
                operator_id=None,
                ip=ip,
                user_agent=user_agent,
                after={"reason": "permission_missing"},
            )
            await self.session.commit()
            raise AuthError(403, "DEPARTMENT_PERMISSION_MISSING", "Department permission missing")

        await self.throttle.clear(department.id, ip)
        now = self.clock.now()
        duration = (
            timedelta(days=self.remember_days)
            if remember_me
            else timedelta(hours=self.default_hours)
        )
        session_token = generate_opaque_token()
        csrf_token = generate_opaque_token()
        auth_session = AuthSession(
            department_id=department.id,
            operator_id=None,
            token_hash=hash_token(session_token),
            csrf_token_hash=hash_token(csrf_token),
            ip=ip,
            user_agent=user_agent,
            expires_at=now + duration,
            revoked_at=None,
        )
        self.session.add(auth_session)
        self.audit.add(
            action=AuditAction.LOGIN_SUCCESS,
            result=AuditResult.SUCCESS,
            department_id=department.id,
            operator_id=None,
            ip=ip,
            user_agent=user_agent,
        )
        await self.session.commit()
        return LoginResult(
            department=department,
            role=permission.role,
            auth_session=auth_session,
            session_token=session_token,
            csrf_token=csrf_token,
        )

    async def _record_failed_login(
        self,
        department_id: UUID,
        department: Department | None,
        ip: str,
        user_agent: str,
    ) -> Never:
        failure = await self.throttle.record_failure(department_id, ip)
        self.audit.add(
            action=AuditAction.LOGIN_FAILED,
            result=AuditResult.FAILED,
            department_id=department.id if department else None,
            operator_id=None,
            ip=ip,
            user_agent=user_agent,
            after={"failure_count": failure.count},
        )
        if failure.locked:
            self.audit.add(
                action=AuditAction.LOGIN_LOCKED,
                result=AuditResult.DENIED,
                department_id=department.id if department else None,
                operator_id=None,
                ip=ip,
                user_agent=user_agent,
            )
        await self.session.commit()
        if failure.locked:
            raise AuthError(423, "LOGIN_LOCKED", "Too many failed attempts; try again later")
        raise AuthError(401, "INVALID_CREDENTIALS", "Invalid department or password")

    async def authenticate(self, session_token: str | None) -> AuthContext:
        if not session_token:
            raise AuthError(401, "AUTH_REQUIRED", "Authentication required")
        auth_session = await self.repository.get_auth_session_by_token_hash(
            hash_token(session_token)
        )
        if auth_session is None or auth_session.revoked_at is not None:
            raise AuthError(401, "INVALID_SESSION", "Session is invalid")
        now = self.clock.now()
        if _as_utc(auth_session.expires_at) <= now:
            auth_session.revoked_at = now
            await self.session.commit()
            raise AuthError(401, "SESSION_EXPIRED", "Session has expired")

        department = await self.repository.get_department(auth_session.department_id)
        if department is None or department.status != DepartmentStatus.ACTIVE:
            raise AuthError(401, "INVALID_SESSION", "Session is invalid")
        permission = await self.repository.get_department_permission(department.id)
        if permission is None:
            raise AuthError(403, "DEPARTMENT_PERMISSION_MISSING", "Department permission missing")

        operator: Operator | None = None
        if auth_session.operator_id is not None:
            candidate = await self.repository.get_operator(auth_session.operator_id)
            if candidate is not None and candidate.status == OperatorStatus.ACTIVE:
                operator = candidate
        return AuthContext(
            department=department,
            operator=operator,
            role=permission.role,
            auth_session=auth_session,
        )

    def validate_csrf(self, context: AuthContext, csrf_token: str | None) -> None:
        if not csrf_token or not token_matches(
            csrf_token,
            context.auth_session.csrf_token_hash,
        ):
            raise AuthError(403, "CSRF_FAILED", "CSRF validation failed")

    async def list_active_departments(self) -> list[Department]:
        return await self.repository.list_active_departments()

    async def list_operators(self, context: AuthContext) -> list[Operator]:
        return await self.repository.list_active_operators(context.department.id)

    async def select_operator(
        self,
        context: AuthContext,
        operator_id: UUID,
        *,
        ip: str,
        user_agent: str,
    ) -> AuthContext:
        operator = await self.repository.get_operator(operator_id)
        if (
            operator is None
            or operator.department_id != context.department.id
            or operator.status != OperatorStatus.ACTIVE
        ):
            raise AuthError(404, "OPERATOR_NOT_FOUND", "Operator not found")

        original_role = context.role
        context.auth_session.operator_id = operator.id
        self.audit.add(
            action=AuditAction.OPERATOR_SELECTED,
            result=AuditResult.SUCCESS,
            department_id=context.department.id,
            operator_id=operator.id,
            ip=ip,
            user_agent=user_agent,
        )
        await self.session.commit()
        return AuthContext(
            department=context.department,
            operator=operator,
            role=original_role,
            auth_session=context.auth_session,
        )

    async def logout(
        self,
        context: AuthContext,
        *,
        ip: str,
        user_agent: str,
    ) -> None:
        context.auth_session.revoked_at = self.clock.now()
        self.audit.add(
            action=AuditAction.LOGOUT,
            result=AuditResult.SUCCESS,
            department_id=context.department.id,
            operator_id=context.operator.id if context.operator else None,
            ip=ip,
            user_agent=user_agent,
        )
        await self.session.commit()

    async def reset_department_password(
        self,
        context: AuthContext,
        *,
        department_id: UUID,
        new_password: str,
        ip: str,
        user_agent: str,
    ) -> int:
        if context.role != Role.SUPER_ADMIN:
            raise AuthError(403, "PERMISSION_DENIED", "Super admin permission required")
        department = await self.repository.get_department(department_id)
        if department is None:
            raise AuthError(404, "DEPARTMENT_NOT_FOUND", "Department not found")

        department.password_hash = hash_password(new_password)
        now = self.clock.now()
        revoked_count = await self.repository.revoke_department_sessions(department.id, now)
        self.audit.add(
            action=AuditAction.PASSWORD_RESET,
            result=AuditResult.SUCCESS,
            department_id=department.id,
            operator_id=context.operator.id if context.operator else None,
            ip=ip,
            user_agent=user_agent,
            entity_type="department",
            entity_id=department.id,
            after={"revoked_sessions": revoked_count},
        )
        await self.session.commit()
        return revoked_count


class BootstrapService:
    """Create the only initial administrative Department and Operator."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = AuthRepository(session)
        self.audit = AuditRepository(session)

    async def create_admin(
        self,
        *,
        department_name: str,
        operator_name: str,
        password: str,
    ) -> tuple[Department, Operator]:
        if await self.repository.super_admin_count() > 0:
            raise AuthError(409, "ADMIN_ALREADY_BOOTSTRAPPED", "Admin is already bootstrapped")
        if await self.repository.get_department_by_name(department_name) is not None:
            raise AuthError(409, "DEPARTMENT_EXISTS", "Department name already exists")

        department = Department(
            name=department_name,
            password_hash=hash_password(password),
            status=DepartmentStatus.ACTIVE,
            session_days=30,
        )
        self.session.add(department)
        await self.session.flush()
        permission = DepartmentPermission(department_id=department.id, role=Role.SUPER_ADMIN)
        operator = Operator(
            department_id=department.id,
            name=operator_name,
            role=Role.SUPER_ADMIN,
            status=OperatorStatus.ACTIVE,
        )
        self.session.add_all([permission, operator])
        await self.session.flush()
        self.audit.add(
            action=AuditAction.BOOTSTRAP_ADMIN,
            result=AuditResult.SUCCESS,
            department_id=department.id,
            operator_id=operator.id,
            ip="cli",
            user_agent="bootstrap-admin",
        )
        await self.session.commit()
        return department, operator

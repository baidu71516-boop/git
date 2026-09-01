"""Department authentication, operator attribution, and session rules."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Never, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.audit.repository import AuditRepository
from backend_core.auth.authorization import (
    EffectiveAuthorizationContext,
    ModuleKey,
    ResolvedDepartmentScope,
    role_is_within_department_ceiling,
)
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.errors import AuthError
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

__all__ = [
    "AuthContext",
    "AuthError",
    "AuthService",
    "BootstrapService",
    "LoginResult",
    "OperatorAuthenticationResult",
    "OperatorCredentialSetupResult",
    "OperatorCredentialSetupService",
]


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class LoginResult:
    department: Department
    role: Role
    auth_session: AuthSession
    session_token: str
    csrf_token: str


@dataclass(frozen=True)
class OperatorAuthenticationResult:
    context: "AuthContext"
    auth_session: AuthSession
    session_token: str
    csrf_token: str


@dataclass(frozen=True)
class OperatorCredentialSetupResult:
    department: Department
    operator: Operator
    revoked_sessions: int


@dataclass(frozen=True)
class AuthContext:
    department: Department
    operator: Operator | None
    # Kept as a temporary compatibility alias for the pre-V1 Department role.
    # New authorization code must use the explicit fields below.
    role: Role
    auth_session: AuthSession
    department_role_ceiling: Role | None = None
    effective_role: Role | None = None

    def __post_init__(self) -> None:
        if self.department_role_ceiling is None:
            object.__setattr__(self, "department_role_ceiling", self.role)
        if self.effective_role is None and self.operator is not None:
            object.__setattr__(self, "effective_role", self.operator.role)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _validate_new_password(password: str) -> None:
    if len(password) < 12 or len(password) > 128:
        raise AuthError(422, "PASSWORD_POLICY_FAILED", "Password must contain 12 to 128 characters")


def _operator_throttle_identity(department_id: UUID, operator_id: UUID) -> UUID:
    """Reuse the login throttle without sharing its Department-login counter."""

    return uuid5(NAMESPACE_URL, f"operator-auth:{department_id}:{operator_id}")


def _operator_aggregate_throttle_identity(department_id: UUID) -> UUID:
    """Bound target-switch failures for one authenticated Department and client IP."""

    return uuid5(NAMESPACE_URL, f"operator-auth-aggregate:{department_id}")


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

        # Department password changes and every session-minting path share this
        # serialization point. A reset therefore cannot miss a concurrently
        # created Department or Operator-bound session.
        locked_departments = await self.repository.lock_departments_for_update((department_id,))
        department = locked_departments.get(department_id)
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
            operator_credential_version=None,
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
            if (
                candidate is None
                or candidate.department_id != department.id
                or candidate.status != OperatorStatus.ACTIVE
                or candidate.password_hash is None
                or candidate.credential_version < 1
                or auth_session.operator_credential_version is None
                or auth_session.operator_credential_version != candidate.credential_version
            ):
                await self._revoke_bound_session(auth_session, now)
                raise AuthError(401, "INVALID_SESSION", "Session is invalid")
            if not role_is_within_department_ceiling(candidate.role, permission.role):
                await self._revoke_bound_session(auth_session, now)
                raise AuthError(
                    403,
                    "ROLE_CEILING_EXCEEDED",
                    "Operator role exceeds Department role ceiling",
                )
            operator = candidate
        return AuthContext(
            department=department,
            operator=operator,
            role=permission.role,
            auth_session=auth_session,
            department_role_ceiling=permission.role,
            effective_role=operator.role if operator is not None else None,
        )

    async def _revoke_bound_session(self, auth_session: AuthSession, now: datetime) -> None:
        auth_session.revoked_at = now
        await self.session.commit()

    async def resolve_effective_authorization(
        self,
        context: AuthContext,
        *,
        department_id: UUID | None = None,
    ) -> EffectiveAuthorizationContext:
        """Resolve current-state V1 authority without changing existing route wiring."""

        department = await self.repository.get_department(context.auth_session.department_id)
        if department is None or department.status != DepartmentStatus.ACTIVE:
            raise AuthError(401, "INVALID_SESSION", "Session is invalid")
        permission = await self.repository.get_department_permission(department.id)
        if permission is None:
            raise AuthError(403, "DEPARTMENT_PERMISSION_MISSING", "Department permission missing")
        if context.auth_session.operator_id is None:
            raise AuthError(409, "OPERATOR_REQUIRED", "Select an operator first")

        operator = await self.repository.get_operator(context.auth_session.operator_id)
        now = self.clock.now()
        if (
            operator is None
            or operator.department_id != department.id
            or operator.status != OperatorStatus.ACTIVE
            or operator.password_hash is None
            or operator.credential_version < 1
            or context.auth_session.operator_credential_version is None
            or context.auth_session.operator_credential_version != operator.credential_version
        ):
            await self._revoke_bound_session(context.auth_session, now)
            raise AuthError(401, "INVALID_SESSION", "Session is invalid")
        if not role_is_within_department_ceiling(operator.role, permission.role):
            await self._revoke_bound_session(context.auth_session, now)
            raise AuthError(
                403,
                "ROLE_CEILING_EXCEEDED",
                "Operator role exceeds Department role ceiling",
            )

        target_department_id = department_id or department.id
        if target_department_id != department.id and operator.role is not Role.SUPER_ADMIN:
            raise AuthError(404, "RESOURCE_NOT_FOUND", "Resource not found")
        if target_department_id != department.id:
            target_department = await self.repository.get_department(target_department_id)
            if target_department is None or target_department.status != DepartmentStatus.ACTIVE:
                raise AuthError(404, "DEPARTMENT_NOT_FOUND", "Department not found")

        # Super Admin is deliberately implicit-all.  Non-Super-Admin grants are
        # read on every business request so an Operator-admin change takes
        # effect without a Session refresh or an authorization snapshot.
        authorized_modules = (
            frozenset()
            if operator.role is Role.SUPER_ADMIN
            else frozenset(
                module_key
                for module_key in await self.repository.list_operator_module_grants(
                    operator_id=operator.id,
                    department_id=department.id,
                )
                if module_key is not ModuleKey.ADMIN
            )
        )

        return EffectiveAuthorizationContext(
            department=department,
            operator=operator,
            department_role_ceiling=permission.role,
            effective_role=operator.role,
            department_scope=ResolvedDepartmentScope(
                department_id=target_department_id,
                cross_department_override=target_department_id != department.id,
            ),
            auth_session=context.auth_session,
            authorized_modules=authorized_modules,
        )

    def validate_csrf(self, context: AuthContext, csrf_token: str | None) -> None:
        if not csrf_token or not token_matches(
            csrf_token,
            context.auth_session.csrf_token_hash,
        ):
            raise AuthError(403, "CSRF_FAILED", "CSRF validation failed")

    async def list_active_departments(self) -> list[Department]:
        return await self.repository.list_active_departments()

    async def list_operators(
        self,
        context: AuthContext,
        *,
        department_id: UUID | None = None,
    ) -> list[Operator]:
        """List active operators in a Department scope authorized by the HTTP layer."""

        return await self.repository.list_active_operators(department_id or context.department.id)

    async def select_operator(
        self,
        context: AuthContext,
        operator_id: UUID,
        operator_password: str,
        *,
        ip: str,
        user_agent: str,
    ) -> OperatorAuthenticationResult:
        throttle_identity = _operator_throttle_identity(context.department.id, operator_id)
        aggregate_throttle_identity = _operator_aggregate_throttle_identity(context.department.id)
        # Once either budget is exhausted, reject from Redis without Argon2 or
        # an attacker-controlled stream of database Audit inserts.
        if await self.throttle.is_locked(
            aggregate_throttle_identity,
            ip,
        ) or await self.throttle.is_locked(throttle_identity, ip):
            raise AuthError(423, "LOGIN_LOCKED", "Too many failed attempts; try again later")

        locked_departments = await self.repository.lock_departments_for_update(
            (context.department.id,)
        )
        department = locked_departments.get(context.department.id)
        # Concurrent spray requests may all pass the optimistic pre-check before
        # the first failure is recorded. Recheck after the shared Department lock
        # so queued requests cannot perform unbounded Argon2 work.
        if await self.throttle.is_locked(
            aggregate_throttle_identity,
            ip,
        ) or await self.throttle.is_locked(throttle_identity, ip):
            await self.session.rollback()
            raise AuthError(423, "LOGIN_LOCKED", "Too many failed attempts; try again later")
        old_session = await self.repository.get_auth_session_for_update(context.auth_session.id)
        now = self.clock.now()
        if (
            department is None
            or department.status is not DepartmentStatus.ACTIVE
            or old_session is None
            or old_session.revoked_at is not None
            or old_session.department_id != context.department.id
            or old_session.operator_id != context.auth_session.operator_id
            or _as_utc(old_session.expires_at) <= now
        ):
            await self.session.rollback()
            raise AuthError(401, "INVALID_SESSION", "Session is invalid")

        initiating_operator: Operator | None = None
        if old_session.operator_id is not None:
            initiating_operator = await self.repository.get_operator_in_department(
                operator_id=old_session.operator_id,
                department_id=department.id,
                for_update=True,
            )
            if (
                initiating_operator is None
                or initiating_operator.status is not OperatorStatus.ACTIVE
                or initiating_operator.password_hash is None
                or initiating_operator.credential_version < 1
                or old_session.operator_credential_version != initiating_operator.credential_version
            ):
                await self.session.rollback()
                raise AuthError(401, "INVALID_SESSION", "Session is invalid")

        permission = await self.repository.get_department_permission(department.id)
        if permission is None:
            await self.session.rollback()
            raise AuthError(403, "DEPARTMENT_PERMISSION_MISSING", "Department permission missing")

        operator = await self.repository.get_operator_in_department(
            operator_id=operator_id,
            department_id=department.id,
            for_update=True,
        )
        if operator is None or operator.status != OperatorStatus.ACTIVE:
            verify_password(timing_safe_dummy_password_hash(), operator_password)
            verify_password(department.password_hash, operator_password)
            await self._record_failed_operator_auth(
                department_id=department.id,
                actor_operator_id=old_session.operator_id,
                target_operator_id=operator_id,
                throttle_identity=throttle_identity,
                aggregate_throttle_identity=aggregate_throttle_identity,
                ip=ip,
                user_agent=user_agent,
            )
        if operator.password_hash is None or operator.credential_version < 1:
            await self._record_operator_auth_denial(
                department_id=department.id,
                actor_operator_id=old_session.operator_id,
                target_operator_id=operator.id,
                throttle_identity=throttle_identity,
                aggregate_throttle_identity=aggregate_throttle_identity,
                ip=ip,
                user_agent=user_agent,
                reason="credential_setup_required",
                result=AuditResult.DENIED,
                status_code=409,
                code="CREDENTIAL_SETUP_REQUIRED",
                message="Operator credential setup is required",
            )
        if not role_is_within_department_ceiling(
            operator.role,
            permission.role,
        ):
            await self._record_operator_auth_denial(
                department_id=department.id,
                actor_operator_id=old_session.operator_id,
                target_operator_id=operator.id,
                throttle_identity=throttle_identity,
                aggregate_throttle_identity=aggregate_throttle_identity,
                ip=ip,
                user_agent=user_agent,
                reason="role_ceiling_exceeded",
                result=AuditResult.DENIED,
                status_code=403,
                code="ROLE_CEILING_EXCEEDED",
                message="Operator role exceeds Department role ceiling",
            )
        password_matches_operator = verify_password(operator.password_hash, operator_password)
        password_matches_department = verify_password(
            department.password_hash,
            operator_password,
        )
        if not password_matches_operator or password_matches_department:
            await self._record_failed_operator_auth(
                department_id=department.id,
                actor_operator_id=old_session.operator_id,
                target_operator_id=operator.id,
                throttle_identity=throttle_identity,
                aggregate_throttle_identity=aggregate_throttle_identity,
                ip=ip,
                user_agent=user_agent,
            )

        # A successful exact-target authentication clears only that target's
        # bucket. The aggregate failure budget remains until its bounded TTL so
        # a known low-privilege credential cannot reset a Department-wide spray.
        await self.throttle.clear(throttle_identity, ip)
        session_token = generate_opaque_token()
        csrf_token = generate_opaque_token()
        old_session.revoked_at = now
        old_session.updated_at = now
        auth_session = AuthSession(
            department_id=department.id,
            operator_id=operator.id,
            operator_credential_version=operator.credential_version,
            token_hash=hash_token(session_token),
            csrf_token_hash=hash_token(csrf_token),
            # Session metadata remains provenance from the original Department
            # login; the Audit row below records this request's current client.
            ip=old_session.ip,
            user_agent=old_session.user_agent,
            expires_at=old_session.expires_at,
            revoked_at=None,
        )
        self.session.add(auth_session)
        self.audit.add(
            action=AuditAction.OPERATOR_AUTHENTICATED,
            result=AuditResult.SUCCESS,
            department_id=department.id,
            operator_id=old_session.operator_id,
            ip=ip,
            user_agent=user_agent,
            entity_type="operator",
            entity_id=operator.id,
        )
        await self.session.commit()
        updated = AuthContext(
            department=department,
            operator=operator,
            role=permission.role,
            auth_session=auth_session,
            department_role_ceiling=permission.role,
            effective_role=operator.role,
        )
        return OperatorAuthenticationResult(
            context=updated,
            auth_session=auth_session,
            session_token=session_token,
            csrf_token=csrf_token,
        )

    async def _record_failed_operator_auth(
        self,
        *,
        department_id: UUID,
        actor_operator_id: UUID | None,
        target_operator_id: UUID,
        throttle_identity: UUID,
        aggregate_throttle_identity: UUID,
        ip: str,
        user_agent: str,
    ) -> Never:
        await self._record_operator_auth_denial(
            department_id=department_id,
            actor_operator_id=actor_operator_id,
            target_operator_id=target_operator_id,
            throttle_identity=throttle_identity,
            aggregate_throttle_identity=aggregate_throttle_identity,
            ip=ip,
            user_agent=user_agent,
            reason="invalid_credentials",
            result=AuditResult.FAILED,
            status_code=401,
            code="INVALID_OPERATOR_CREDENTIALS",
            message="Invalid operator credentials",
        )

    async def _record_operator_auth_denial(
        self,
        *,
        department_id: UUID,
        actor_operator_id: UUID | None,
        target_operator_id: UUID,
        throttle_identity: UUID,
        aggregate_throttle_identity: UUID,
        ip: str,
        user_agent: str,
        reason: str,
        result: AuditResult,
        status_code: int,
        code: str,
        message: str,
    ) -> Never:
        aggregate_failure = await self.throttle.record_failure(
            aggregate_throttle_identity,
            ip,
        )
        target_failure = await self.throttle.record_failure(throttle_identity, ip)
        locked = aggregate_failure.locked or target_failure.locked
        self.audit.add(
            action=AuditAction.OPERATOR_AUTH_FAILED,
            result=AuditResult.DENIED if locked else result,
            department_id=department_id,
            operator_id=actor_operator_id,
            ip=ip,
            user_agent=user_agent,
            entity_type="operator",
            entity_id=target_operator_id,
            after={
                "reason": reason,
                "failure_count": target_failure.count,
                "aggregate_failure_count": aggregate_failure.count,
            },
        )
        await self.session.commit()
        if locked:
            raise AuthError(423, "LOGIN_LOCKED", "Too many failed attempts; try again later")
        raise AuthError(status_code, code, message)

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

    async def _lock_super_admin_mutation_authority(
        self,
        context: AuthContext | EffectiveAuthorizationContext,
        *,
        target_department_ids: tuple[UUID, ...] = (),
    ) -> tuple[dict[UUID, Department], Operator]:
        """Lock and revalidate the live actor at the auth mutation boundary."""

        if context.operator is None or context.auth_session.operator_id != context.operator.id:
            raise AuthError(401, "INVALID_SESSION", "Session is invalid")
        locked_departments = await self.repository.lock_departments_for_update(
            (context.department.id, *target_department_ids)
        )
        actor_department = locked_departments.get(context.department.id)
        current_session = await self.repository.get_auth_session(
            context.auth_session.id,
            for_update=True,
        )
        current_operator = await self.repository.get_operator_in_department(
            operator_id=context.operator.id,
            department_id=context.department.id,
            for_update=True,
        )
        now = self.clock.now()
        if (
            actor_department is None
            or actor_department.status is not DepartmentStatus.ACTIVE
            or current_session is None
            or current_session.department_id != context.department.id
            or current_session.operator_id != context.operator.id
            or current_session.revoked_at is not None
            or _as_utc(current_session.expires_at) <= now
            or current_operator is None
            or current_operator.status is not OperatorStatus.ACTIVE
            or current_operator.password_hash is None
            or current_operator.credential_version < 1
            or current_session.operator_credential_version != current_operator.credential_version
        ):
            raise AuthError(401, "INVALID_SESSION", "Session is invalid")
        permission = await self.repository.get_department_permission(actor_department.id)
        if permission is None or permission.role is not Role.SUPER_ADMIN:
            raise AuthError(403, "PERMISSION_DENIED", "Super admin permission required")
        if current_operator.role is not Role.SUPER_ADMIN:
            raise AuthError(403, "PERMISSION_DENIED", "Super admin permission required")
        return locked_departments, current_operator

    async def reset_department_password(
        self,
        context: AuthContext | EffectiveAuthorizationContext,
        *,
        department_id: UUID,
        new_password: str,
        ip: str,
        user_agent: str,
    ) -> int:
        try:
            locked_departments, actor = await self._lock_super_admin_mutation_authority(
                context,
                target_department_ids=(department_id,),
            )
            department = locked_departments.get(department_id)
            if department is None:
                raise AuthError(404, "DEPARTMENT_NOT_FOUND", "Department not found")

            _validate_new_password(new_password)
            operators = await self.repository.list_operators_in_department(department.id)
            if any(
                operator.password_hash is not None
                and verify_password(operator.password_hash, new_password)
                for operator in operators
            ):
                raise AuthError(
                    422,
                    "PASSWORD_REUSE_FORBIDDEN",
                    "Department and Operator passwords must differ",
                )
            department.password_hash = hash_password(new_password)
            now = self.clock.now()
            revoked_count = await self.repository.revoke_department_sessions(department.id, now)
            self.audit.add(
                action=AuditAction.PASSWORD_RESET,
                result=AuditResult.SUCCESS,
                department_id=department.id,
                operator_id=actor.id,
                ip=ip,
                user_agent=user_agent,
                entity_type="department",
                entity_id=department.id,
                after={"revoked_sessions": revoked_count},
            )
            await self.session.commit()
            return revoked_count
        except BaseException:
            await self.session.rollback()
            raise


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
        department_password: str,
        operator_password: str,
    ) -> tuple[Department, Operator]:
        if await self.repository.super_admin_count() > 0:
            raise AuthError(409, "ADMIN_ALREADY_BOOTSTRAPPED", "Admin is already bootstrapped")
        if await self.repository.get_department_by_name(department_name) is not None:
            raise AuthError(409, "DEPARTMENT_EXISTS", "Department name already exists")

        _validate_new_password(department_password)
        _validate_new_password(operator_password)
        department_password_hash = hash_password(department_password)
        if verify_password(department_password_hash, operator_password):
            raise AuthError(
                422,
                "PASSWORD_REUSE_FORBIDDEN",
                "Department and Operator passwords must differ",
            )
        department = Department(
            name=department_name,
            password_hash=department_password_hash,
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
            password_hash=hash_password(operator_password),
            credential_version=1,
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


class OperatorCredentialSetupService:
    """Exact-ID, out-of-band credential initialization for migrated Operators."""

    def __init__(self, session: AsyncSession, *, clock: Clock | None = None) -> None:
        self.session = session
        self.repository = AuthRepository(session)
        self.audit = AuditRepository(session)
        self.clock = clock or SystemClock()

    async def setup(
        self,
        *,
        department_id: UUID,
        operator_id: UUID,
        password: str,
        require_super_admin: bool = False,
    ) -> OperatorCredentialSetupResult:
        try:
            _validate_new_password(password)
            locked_departments = await self.repository.lock_departments_for_update((department_id,))
            department = locked_departments.get(department_id)
            if department is None:
                raise AuthError(404, "DEPARTMENT_NOT_FOUND", "Department not found")
            operator = await self.repository.get_operator_in_department(
                operator_id=operator_id,
                department_id=department.id,
                for_update=True,
            )
            if operator is None:
                raise AuthError(404, "OPERATOR_NOT_FOUND", "Operator not found")
            if operator.status is not OperatorStatus.ACTIVE:
                raise AuthError(409, "OPERATOR_DISABLED", "Operator is disabled")
            if require_super_admin:
                permission = await self.repository.get_department_permission(department.id)
                if (
                    department.status is not DepartmentStatus.ACTIVE
                    or permission is None
                    or permission.role is not Role.SUPER_ADMIN
                    or operator.role is not Role.SUPER_ADMIN
                ):
                    raise AuthError(
                        409,
                        "SUPER_ADMIN_REQUIRED",
                        "Target must be an active Super Admin in a Super Admin Department",
                    )
            if operator.password_hash is not None or operator.credential_version != 0:
                raise AuthError(
                    409,
                    "CREDENTIAL_ALREADY_INITIALIZED",
                    "Operator credential is already initialized",
                )
            if verify_password(department.password_hash, password):
                raise AuthError(
                    422,
                    "PASSWORD_REUSE_FORBIDDEN",
                    "Department and Operator passwords must differ",
                )

            now = self.clock.now()
            operator.password_hash = hash_password(password)
            operator.credential_version = 1
            operator.updated_at = now
            revoked_sessions = await self.repository.revoke_operator_sessions(
                operator_id=operator.id,
                department_id=department.id,
                now=now,
            )
            self.audit.add(
                action=AuditAction.OPERATOR_CREDENTIAL_SET,
                result=AuditResult.SUCCESS,
                department_id=department.id,
                operator_id=None,
                ip="cli",
                user_agent="setup-operator-credential",
                entity_type="operator",
                entity_id=operator.id,
                after={"revoked_sessions": revoked_sessions},
            )
            await self.session.commit()
            return OperatorCredentialSetupResult(
                department=department,
                operator=operator,
                revoked_sessions=revoked_sessions,
            )
        except BaseException:
            await self.session.rollback()
            raise

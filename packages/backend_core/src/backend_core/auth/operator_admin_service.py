"""Transactional, same-Department Operator administration for Permissions V1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.audit.repository import AuditRepository
from backend_core.auth import BusinessAuthorizationContext as AuthContext
from backend_core.auth.enums import (
    DepartmentStatus,
    ModuleKey,
    OperatorStatus,
    Role,
    canonical_module_keys,
    role_at_or_below,
)
from backend_core.auth.errors import AuthError
from backend_core.auth.models import Operator
from backend_core.auth.repository import AuthRepository
from backend_core.auth.schemas import (
    OperatorAdminCreateInput,
    OperatorAdminPublic,
    OperatorAdminUpdateInput,
    OperatorPasswordResetPublic,
)
from backend_core.auth.security import hash_password, verify_password


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class _Actor:
    department_id: UUID
    operator_id: UUID


def _as_utc(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class OperatorAdminService:
    """Own the closed Task 3 Operator administration invariants.

    Successful mutations add their audit rows before the one commit.  The
    rollback in every failure path therefore removes target, grant, session,
    and audit changes together.
    """

    def __init__(self, session: AsyncSession, *, clock: Clock | None = None) -> None:
        self.session = session
        self.repository = AuthRepository(session)
        self.audit = AuditRepository(session)
        self.clock = clock or SystemClock()

    async def list_operators(self, context: AuthContext) -> list[OperatorAdminPublic]:
        actor = await self._require_super_admin(context)
        operators = await self.repository.list_operators_in_department(actor.department_id)
        grants = await self.repository.list_operator_module_grants_for_operators(
            operator_ids=[operator.id for operator in operators],
            department_id=actor.department_id,
        )
        return [self._public(operator, grants[operator.id]) for operator in operators]

    async def get_operator(
        self,
        context: AuthContext,
        *,
        operator_id: UUID,
    ) -> OperatorAdminPublic:
        actor = await self._require_super_admin(context)
        operator = await self._scoped_operator(actor.department_id, operator_id)
        grants = await self.repository.list_operator_module_grants(
            operator_id=operator.id,
            department_id=actor.department_id,
        )
        return self._public(operator, grants)

    async def create_operator(
        self,
        context: AuthContext,
        payload: OperatorAdminCreateInput,
        *,
        ip: str,
        user_agent: str,
    ) -> OperatorAdminPublic:
        try:
            actor = await self._require_super_admin(context, for_mutation=True)
            ceiling = await self._department_ceiling(actor.department_id)
            role = self._role(payload.role)
            self._validate_role_within_ceiling(role, ceiling)
            grants = self._module_grants(payload.module_grants, role=role)
            password = payload.password.get_secret_value()
            await self._validate_independent_password(
                department_id=actor.department_id,
                password=password,
            )
            if await self.repository.get_operator_by_name_in_department(
                department_id=actor.department_id,
                name=payload.name,
            ):
                raise AuthError(409, "OPERATOR_NAME_CONFLICT", "Operator name already exists")
            operator = Operator(
                department_id=actor.department_id,
                name=payload.name,
                role=role,
                status=OperatorStatus.ACTIVE,
                password_hash=hash_password(password),
                credential_version=1,
            )
            self.session.add(operator)
            await self.session.flush()
            await self.repository.replace_operator_module_grants(
                operator_id=operator.id,
                department_id=actor.department_id,
                module_keys=grants,
            )
            await self.session.refresh(operator)
            public = self._public(operator, grants)
            self.audit.add(
                action=AuditAction.OPERATOR_CREATED,
                result=AuditResult.SUCCESS,
                department_id=actor.department_id,
                operator_id=actor.operator_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="operator",
                entity_id=operator.id,
                after=self._audit_state(public),
            )
            await self.session.commit()
            return public
        except IntegrityError as exc:
            await self.session.rollback()
            raise AuthError(409, "OPERATOR_NAME_CONFLICT", "Operator name already exists") from exc
        except BaseException:
            await self.session.rollback()
            raise

    async def reset_operator_password(
        self,
        context: AuthContext,
        *,
        operator_id: UUID,
        password: str,
        ip: str,
        user_agent: str,
    ) -> OperatorPasswordResetPublic:
        """Replace one exact Operator credential and revoke every bound session."""

        try:
            actor = await self._require_super_admin(context, for_mutation=True)
            operator = await self._scoped_operator(
                actor.department_id,
                operator_id,
                for_update=True,
            )
            await self._validate_independent_password(
                department_id=actor.department_id,
                password=password,
            )
            if operator.password_hash is not None and verify_password(
                operator.password_hash,
                password,
            ):
                raise AuthError(
                    409,
                    "OPERATOR_PASSWORD_UNCHANGED",
                    "New Operator password must differ from the current password",
                )

            now = _as_utc(self.clock.now(), name="clock")
            operator.password_hash = hash_password(password)
            operator.credential_version = max(operator.credential_version, 0) + 1
            operator.updated_at = now
            revoked_sessions = await self.repository.revoke_operator_sessions(
                operator_id=operator.id,
                department_id=actor.department_id,
                now=now,
            )
            self.audit.add(
                action=AuditAction.OPERATOR_PASSWORD_RESET,
                result=AuditResult.SUCCESS,
                department_id=actor.department_id,
                operator_id=actor.operator_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="operator",
                entity_id=operator.id,
                after={"revoked_sessions": revoked_sessions},
            )
            await self.session.commit()
            return OperatorPasswordResetPublic(
                operator_id=operator.id,
                revoked_sessions=revoked_sessions,
            )
        except BaseException:
            await self.session.rollback()
            raise

    async def update_operator(
        self,
        context: AuthContext,
        *,
        operator_id: UUID,
        payload: OperatorAdminUpdateInput,
        ip: str,
        user_agent: str,
    ) -> OperatorAdminPublic:
        if not ({"name", "role", "status", "module_grants"} & payload.model_fields_set):
            raise AuthError(422, "OPERATOR_UPDATE_EMPTY", "At least one operator field is required")
        try:
            actor = await self._require_super_admin(context, for_mutation=True)
            # Every mutation locks this Department's active Super Admin set in a
            # deterministic order before locking its target. That makes the
            # last-active-Super-Admin invariant safe under concurrent updates.
            active_super_admins = await self.repository.lock_active_super_admins(
                actor.department_id
            )
            operator = await self._scoped_operator(
                actor.department_id,
                operator_id,
                for_update=True,
            )
            expected_updated_at = self._expected_updated_at(payload.expected_updated_at)
            if _as_utc(operator.updated_at, name="operator updated_at") != expected_updated_at:
                raise AuthError(409, "VERSION_CONFLICT", "Operator version is stale")

            ceiling = await self._department_ceiling(actor.department_id)
            requested_role = operator.role if payload.role is None else self._role(payload.role)
            requested_status = (
                operator.status if payload.status is None else self._status(payload.status)
            )
            self._validate_role_within_ceiling(requested_role, ceiling)
            if operator.role is not Role.SUPER_ADMIN and requested_role is Role.SUPER_ADMIN:
                raise AuthError(
                    403,
                    "SUPER_ADMIN_PROMOTION_FORBIDDEN",
                    "Existing operators cannot be promoted to super_admin",
                )

            existing_grants = await self.repository.list_operator_module_grants(
                operator_id=operator.id,
                department_id=actor.department_id,
            )
            requested_grants = (
                existing_grants
                if payload.module_grants is None
                else self._module_grants(payload.module_grants, role=requested_role)
            )
            if requested_role is Role.SUPER_ADMIN and existing_grants:
                raise AuthError(
                    409,
                    "SUPER_ADMIN_GRANTS_INVALID",
                    "Super admin must not have persisted module grants",
                )
            self._validate_last_active_super_admin(
                operator=operator,
                requested_role=requested_role,
                requested_status=requested_status,
                active_super_admins=active_super_admins,
            )

            name_changed = payload.name is not None and payload.name != operator.name
            role_changed = requested_role is not operator.role
            status_changed = requested_status is not operator.status
            grants_changed = requested_grants != existing_grants
            if not any((name_changed, role_changed, status_changed, grants_changed)):
                raise AuthError(409, "OPERATOR_UPDATE_NO_CHANGE", "Operator update has no changes")

            before = self._public(operator, existing_grants)
            if payload.name is not None:
                operator.name = payload.name
            if role_changed:
                operator.role = requested_role
            if status_changed:
                operator.status = requested_status
            now = _as_utc(self.clock.now(), name="clock")
            operator.updated_at = now
            await self.session.flush()
            revoked_sessions = 0
            if grants_changed:
                await self.repository.replace_operator_module_grants(
                    operator_id=operator.id,
                    department_id=actor.department_id,
                    module_keys=requested_grants,
                )
            if status_changed and requested_status is OperatorStatus.DISABLED:
                revoked_sessions = await self.repository.revoke_operator_sessions(
                    operator_id=operator.id,
                    department_id=actor.department_id,
                    now=now,
                )
            await self.session.flush()
            await self.session.refresh(operator)
            public = self._public(operator, requested_grants)
            self._add_update_audit_events(
                actor=actor,
                operator=operator,
                before=before,
                after=public,
                name_changed=name_changed,
                role_changed=role_changed,
                status_changed=status_changed,
                grants_changed=grants_changed,
                revoked_sessions=revoked_sessions,
                ip=ip,
                user_agent=user_agent,
            )
            await self.session.commit()
            return public
        except IntegrityError as exc:
            await self.session.rollback()
            raise AuthError(409, "OPERATOR_NAME_CONFLICT", "Operator name already exists") from exc
        except BaseException:
            await self.session.rollback()
            raise

    async def _require_super_admin(
        self,
        context: AuthContext,
        *,
        for_mutation: bool = False,
    ) -> _Actor:
        if context.operator is None or context.effective_role is None:
            raise AuthError(409, "OPERATOR_REQUIRED", "Select an operator first")
        if context.auth_session.operator_id != context.operator.id:
            raise AuthError(401, "INVALID_SESSION", "Session is invalid")

        if for_mutation:
            locked_departments = await self.repository.lock_departments_for_update(
                (context.department.id,)
            )
            current_department = locked_departments.get(context.department.id)
        else:
            current_department = await self.repository.get_department(context.department.id)
        if current_department is None or current_department.status is not DepartmentStatus.ACTIVE:
            raise AuthError(401, "INVALID_SESSION", "Session is invalid")

        current_session = await self.repository.get_auth_session(
            context.auth_session.id,
            for_update=for_mutation,
        )
        current_operator = await self.repository.get_operator_in_department(
            operator_id=context.operator.id,
            department_id=context.department.id,
            for_update=for_mutation,
        )
        now = _as_utc(self.clock.now(), name="clock")
        if (
            current_session is None
            or current_session.department_id != current_department.id
            or current_session.operator_id != context.operator.id
            or current_session.revoked_at is not None
            or _as_utc(current_session.expires_at, name="session expires_at") <= now
            or current_operator is None
            or current_operator.status is not OperatorStatus.ACTIVE
            or current_operator.password_hash is None
            or current_operator.credential_version < 1
            or current_session.operator_credential_version != current_operator.credential_version
        ):
            raise AuthError(401, "INVALID_SESSION", "Session is invalid")
        permission = await self.repository.get_department_permission(current_department.id)
        if (
            permission is None
            or permission.role is not Role.SUPER_ADMIN
            or current_operator.role is not Role.SUPER_ADMIN
        ):
            raise AuthError(403, "PERMISSION_DENIED", "Super admin permission required")
        return _Actor(department_id=current_department.id, operator_id=current_operator.id)

    async def _department_ceiling(self, department_id: UUID) -> Role:
        permission = await self.repository.get_department_permission(department_id)
        if permission is None:
            raise AuthError(403, "DEPARTMENT_PERMISSION_MISSING", "Department permission missing")
        return permission.role

    async def _validate_independent_password(
        self,
        *,
        department_id: UUID,
        password: str,
    ) -> None:
        if len(password) < 12 or len(password) > 128:
            raise AuthError(
                422,
                "PASSWORD_POLICY_FAILED",
                "Password must contain 12 to 128 characters",
            )
        department = await self.repository.get_department(department_id)
        if department is None:
            raise AuthError(404, "DEPARTMENT_NOT_FOUND", "Department not found")
        if verify_password(department.password_hash, password):
            raise AuthError(
                422,
                "PASSWORD_REUSE_FORBIDDEN",
                "Department and Operator passwords must differ",
            )

    async def _scoped_operator(
        self,
        department_id: UUID,
        operator_id: UUID,
        *,
        for_update: bool = False,
    ) -> Operator:
        operator = await self.repository.get_operator_in_department(
            operator_id=operator_id,
            department_id=department_id,
            for_update=for_update,
        )
        if operator is None:
            raise AuthError(404, "OPERATOR_NOT_FOUND", "Operator not found")
        return operator

    @staticmethod
    def _role(value: str) -> Role:
        try:
            return Role(value)
        except ValueError as exc:
            raise AuthError(422, "INVALID_ROLE", "Unknown operator role") from exc

    @staticmethod
    def _status(value: str) -> OperatorStatus:
        try:
            return OperatorStatus(value)
        except ValueError as exc:
            raise AuthError(422, "INVALID_OPERATOR_STATUS", "Unknown operator status") from exc

    @staticmethod
    def _validate_role_within_ceiling(role: Role, ceiling: Role) -> None:
        if not role_at_or_below(role, ceiling):
            raise AuthError(
                403,
                "ROLE_CEILING_EXCEEDED",
                "Operator role exceeds Department role ceiling",
            )

    def _module_grants(self, values: list[str], *, role: Role) -> tuple[ModuleKey, ...]:
        module_keys: list[ModuleKey] = []
        for value in values:
            try:
                module_key = ModuleKey(value)
            except ValueError as exc:
                raise AuthError(422, "INVALID_MODULE_KEY", "Unknown module key") from exc
            if module_key is ModuleKey.ADMIN:
                raise AuthError(
                    422,
                    "ADMIN_MODULE_NOT_GRANTABLE",
                    "The admin module cannot be explicitly granted",
                )
            module_keys.append(module_key)
        grants = canonical_module_keys(module_keys)
        if role is Role.SUPER_ADMIN and grants:
            raise AuthError(
                422,
                "SUPER_ADMIN_GRANTS_FORBIDDEN",
                "Super admin must not have persisted module grants",
            )
        return grants

    @staticmethod
    def _expected_updated_at(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise AuthError(
                422,
                "EXPECTED_UPDATED_AT_INVALID",
                "expected_updated_at must include a timezone",
            )
        return value.astimezone(UTC)

    @staticmethod
    def _validate_last_active_super_admin(
        *,
        operator: Operator,
        requested_role: Role,
        requested_status: OperatorStatus,
        active_super_admins: list[Operator],
    ) -> None:
        remains_active_super_admin = (
            requested_role is Role.SUPER_ADMIN and requested_status is OperatorStatus.ACTIVE
        )
        currently_usable_super_admin = (
            operator.role is Role.SUPER_ADMIN
            and operator.status is OperatorStatus.ACTIVE
            and operator.password_hash is not None
            and operator.credential_version >= 1
        )
        if (
            currently_usable_super_admin
            and not remains_active_super_admin
            and len(active_super_admins) == 1
        ):
            raise AuthError(
                409,
                "LAST_ACTIVE_SUPER_ADMIN",
                "Department must retain one active super_admin",
            )

    @staticmethod
    def _public(
        operator: Operator,
        module_grants: tuple[ModuleKey, ...],
    ) -> OperatorAdminPublic:
        return OperatorAdminPublic(
            id=operator.id,
            name=operator.name,
            role=operator.role,
            status=operator.status,
            module_grants=module_grants,
            created_at=_as_utc(operator.created_at, name="operator created_at"),
            updated_at=_as_utc(operator.updated_at, name="operator updated_at"),
        )

    @staticmethod
    def _audit_state(public: OperatorAdminPublic) -> dict[str, object]:
        return {
            "name": public.name,
            "role": public.role.value,
            "status": public.status.value,
            "module_grants": [module_key.value for module_key in public.module_grants],
        }

    def _add_update_audit_events(
        self,
        *,
        actor: _Actor,
        operator: Operator,
        before: OperatorAdminPublic,
        after: OperatorAdminPublic,
        name_changed: bool,
        role_changed: bool,
        status_changed: bool,
        grants_changed: bool,
        revoked_sessions: int,
        ip: str,
        user_agent: str,
    ) -> None:
        if name_changed:
            self.audit.add(
                action=AuditAction.OPERATOR_UPDATED,
                result=AuditResult.SUCCESS,
                department_id=actor.department_id,
                operator_id=actor.operator_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="operator",
                entity_id=operator.id,
                before={"name": before.name},
                after={"name": after.name},
            )
        if role_changed:
            self.audit.add(
                action=AuditAction.OPERATOR_ROLE_CHANGED,
                result=AuditResult.SUCCESS,
                department_id=actor.department_id,
                operator_id=actor.operator_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="operator",
                entity_id=operator.id,
                before={"role": before.role.value},
                after={"role": after.role.value},
            )
        if status_changed:
            self.audit.add(
                action=AuditAction.OPERATOR_STATUS_CHANGED,
                result=AuditResult.SUCCESS,
                department_id=actor.department_id,
                operator_id=actor.operator_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="operator",
                entity_id=operator.id,
                before={"status": before.status.value},
                after={
                    "status": after.status.value,
                    "revoked_sessions": revoked_sessions,
                },
            )
        if grants_changed:
            self.audit.add(
                action=AuditAction.OPERATOR_MODULE_GRANTS_CHANGED,
                result=AuditResult.SUCCESS,
                department_id=actor.department_id,
                operator_id=actor.operator_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="operator",
                entity_id=operator.id,
                before={"module_grants": [value.value for value in before.module_grants]},
                after={"module_grants": [value.value for value in after.module_grants]},
            )

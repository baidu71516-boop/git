"""Authentication persistence operations."""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.auth.enums import (
    DepartmentStatus,
    ModuleKey,
    OperatorStatus,
    Role,
    canonical_module_keys,
)
from backend_core.auth.models import (
    AuthSession,
    Department,
    DepartmentPermission,
    Operator,
    OperatorModulePermission,
)


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active_departments(self) -> list[Department]:
        result = await self.session.scalars(
            select(Department)
            .where(Department.status == DepartmentStatus.ACTIVE)
            .order_by(Department.name)
        )
        return list(result)

    async def get_department(self, department_id: UUID) -> Department | None:
        return await self.session.get(Department, department_id)

    async def get_department_by_name(self, name: str) -> Department | None:
        return cast(
            Department | None,
            await self.session.scalar(select(Department).where(Department.name == name)),
        )

    async def get_department_permission(
        self,
        department_id: UUID,
    ) -> DepartmentPermission | None:
        return cast(
            DepartmentPermission | None,
            await self.session.scalar(
                select(DepartmentPermission).where(
                    DepartmentPermission.department_id == department_id
                )
            ),
        )

    async def list_active_operators(self, department_id: UUID) -> list[Operator]:
        result = await self.session.scalars(
            select(Operator)
            .where(
                Operator.department_id == department_id,
                Operator.status == OperatorStatus.ACTIVE,
            )
            .order_by(Operator.name)
        )
        return list(result)

    async def get_operator(self, operator_id: UUID) -> Operator | None:
        return await self.session.get(Operator, operator_id)

    async def list_operator_module_grants(
        self,
        *,
        operator_id: UUID,
        department_id: UUID,
    ) -> tuple[ModuleKey, ...]:
        """Return one Operator's exact same-Department grants in frozen order."""

        await self._operator_in_department(operator_id=operator_id, department_id=department_id)
        result = await self.session.scalars(
            select(OperatorModulePermission.module_key).where(
                OperatorModulePermission.operator_id == operator_id,
                OperatorModulePermission.department_id == department_id,
            )
        )
        return canonical_module_keys(result.all())

    async def replace_operator_module_grants(
        self,
        *,
        operator_id: UUID,
        department_id: UUID,
        module_keys: object,
    ) -> tuple[ModuleKey, ...]:
        """Atomically replace the complete persisted grant set for one Operator."""

        grants = canonical_module_keys(module_keys)
        operator = await self._operator_in_department(
            operator_id=operator_id,
            department_id=department_id,
            for_update=True,
        )
        if operator.role is Role.SUPER_ADMIN and grants:
            raise ValueError("Super Admin access is implicit and cannot have persisted grants")
        if operator.role is not Role.SUPER_ADMIN and ModuleKey.ADMIN in grants:
            raise ValueError("The admin module is not grantable to non-Super-Admin Operators")

        await self.session.execute(
            delete(OperatorModulePermission).where(
                OperatorModulePermission.operator_id == operator_id,
                OperatorModulePermission.department_id == department_id,
            )
        )
        self.session.add_all(
            OperatorModulePermission(
                operator_id=operator_id,
                department_id=department_id,
                module_key=module_key,
            )
            for module_key in grants
        )
        await self.session.flush()
        return grants

    async def _operator_in_department(
        self,
        *,
        operator_id: UUID,
        department_id: UUID,
        for_update: bool = False,
    ) -> Operator:
        statement = select(Operator).where(
            Operator.id == operator_id,
            Operator.department_id == department_id,
        )
        if for_update:
            statement = statement.with_for_update()
        operator = await self.session.scalar(statement)
        if operator is None:
            raise ValueError("Operator does not belong to the supplied Department")
        return operator

    async def get_auth_session_by_token_hash(self, token_hash: str) -> AuthSession | None:
        return cast(
            AuthSession | None,
            await self.session.scalar(
                select(AuthSession).where(AuthSession.token_hash == token_hash)
            ),
        )

    async def revoke_department_sessions(self, department_id: UUID, now: datetime) -> int:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(AuthSession)
                .where(
                    AuthSession.department_id == department_id,
                    AuthSession.revoked_at.is_(None),
                )
                .values(revoked_at=now, updated_at=now)
            ),
        )
        return int(result.rowcount or 0)

    async def super_admin_count(self) -> int:
        count = await self.session.scalar(
            select(func.count())
            .select_from(DepartmentPermission)
            .where(DepartmentPermission.role == Role.SUPER_ADMIN)
        )
        return int(count or 0)

"""Focused Task 2 persistence contracts for closed Operator module grants."""

import asyncio

import pytest
from backend_core.audit.enums import AuditAction
from backend_core.auth.enums import (
    NON_ADMIN_MODULE_KEYS,
    DepartmentStatus,
    ModuleKey,
    OperatorStatus,
    Role,
    canonical_module_keys,
)
from backend_core.auth.models import Department, DepartmentPermission, Operator
from backend_core.auth.repository import AuthRepository
from backend_core.auth.security import hash_password
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fixtures.phase3a_auth_database import database_session


async def _add_operator(
    session: AsyncSession,
    *,
    name: str,
    role: Role,
    department_role: Role,
    status: OperatorStatus = OperatorStatus.ACTIVE,
) -> tuple[Department, Operator]:
    department = Department(
        name=name,
        password_hash=hash_password("permissions-persistence-test"),
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    session.add(department)
    await session.flush()
    operator = Operator(
        department_id=department.id,
        name=f"{name} Operator",
        role=role,
        status=status,
    )
    session.add_all(
        [
            DepartmentPermission(department_id=department.id, role=department_role),
            operator,
        ]
    )
    await session.commit()
    return department, operator


def test_closed_module_keys_are_canonical_and_reject_dynamic_values() -> None:
    assert canonical_module_keys(
        [ModuleKey.DATA_UPDATES, ModuleKey.CAMPAIGNS, ModuleKey.TODAY_OUTREACH]
    ) == (
        ModuleKey.TODAY_OUTREACH,
        ModuleKey.CAMPAIGNS,
        ModuleKey.DATA_UPDATES,
    )
    assert len(NON_ADMIN_MODULE_KEYS) == 7
    assert ModuleKey.ADMIN not in NON_ADMIN_MODULE_KEYS
    with pytest.raises(TypeError, match="closed ModuleKey"):
        canonical_module_keys(["campaigns"])


def test_repository_replaces_complete_grant_sets_in_canonical_order() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            department, operator = await _add_operator(
                session,
                name="Repository",
                role=Role.OPERATOR,
                department_role=Role.OPERATOR,
            )
            repository = AuthRepository(session)

            first = await repository.replace_operator_module_grants(
                operator_id=operator.id,
                department_id=department.id,
                module_keys=[ModuleKey.DATA_UPDATES, ModuleKey.CAMPAIGNS],
            )
            await session.commit()
            assert first == (ModuleKey.CAMPAIGNS, ModuleKey.DATA_UPDATES)
            assert await repository.list_operator_module_grants(
                operator_id=operator.id,
                department_id=department.id,
            ) == first

            replacement = await repository.replace_operator_module_grants(
                operator_id=operator.id,
                department_id=department.id,
                module_keys=[ModuleKey.INFLUENCER_LIBRARY],
            )
            await session.commit()
            assert replacement == (ModuleKey.INFLUENCER_LIBRARY,)
            assert await repository.list_operator_module_grants(
                operator_id=operator.id,
                department_id=department.id,
            ) == replacement

    asyncio.run(scenario())


def test_repository_rejects_cross_department_replacement_and_admin_grants() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            department, operator = await _add_operator(
                session,
                name="Primary",
                role=Role.OPERATOR,
                department_role=Role.OPERATOR,
            )
            other_department, _ = await _add_operator(
                session,
                name="Other",
                role=Role.OPERATOR,
                department_role=Role.OPERATOR,
            )
            repository = AuthRepository(session)

            with pytest.raises(ValueError, match="does not belong"):
                await repository.replace_operator_module_grants(
                    operator_id=operator.id,
                    department_id=other_department.id,
                    module_keys=[ModuleKey.CAMPAIGNS],
                )
            with pytest.raises(ValueError, match="admin module"):
                await repository.replace_operator_module_grants(
                    operator_id=operator.id,
                    department_id=department.id,
                    module_keys=[ModuleKey.ADMIN],
                )

    asyncio.run(scenario())


def test_super_admin_requires_no_persisted_grants() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            department, operator = await _add_operator(
                session,
                name="Implicit",
                role=Role.SUPER_ADMIN,
                department_role=Role.SUPER_ADMIN,
            )
            repository = AuthRepository(session)

            assert await repository.replace_operator_module_grants(
                operator_id=operator.id,
                department_id=department.id,
                module_keys=[],
            ) == ()
            await session.commit()
            assert await repository.list_operator_module_grants(
                operator_id=operator.id,
                department_id=department.id,
            ) == ()
            with pytest.raises(ValueError, match="implicit"):
                await repository.replace_operator_module_grants(
                    operator_id=operator.id,
                    department_id=department.id,
                    module_keys=[ModuleKey.CAMPAIGNS],
                )

    asyncio.run(scenario())


def test_permissions_audit_actions_are_closed() -> None:
    expected = {
        AuditAction.OPERATOR_CREATED,
        AuditAction.OPERATOR_UPDATED,
        AuditAction.OPERATOR_ROLE_CHANGED,
        AuditAction.OPERATOR_STATUS_CHANGED,
        AuditAction.OPERATOR_MODULE_GRANTS_CHANGED,
    }
    assert expected <= set(AuditAction)
    assert all(action.value.startswith("OPERATOR_") for action in expected)

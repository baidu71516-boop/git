"""Focused Permissions V1 Task 3 service contracts."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from backend_core.audit.enums import AuditAction
from backend_core.audit.models import AuditLog
from backend_core.auth.enums import DepartmentStatus, ModuleKey, OperatorStatus, Role
from backend_core.auth.errors import AuthError
from backend_core.auth.models import AuthSession, Department, DepartmentPermission, Operator
from backend_core.auth.operator_admin_service import OperatorAdminService
from backend_core.auth.schemas import OperatorAdminCreateInput, OperatorAdminUpdateInput
from backend_core.auth.security import hash_password, verify_password
from backend_core.auth.service import AuthContext
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fixtures.phase3a_auth_database import database_session


class FixedClock:
    def __init__(self) -> None:
        self.value = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)

    def now(self) -> datetime:
        self.value += timedelta(seconds=1)
        return self.value


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _seed_department(
    session: AsyncSession,
    *,
    name: str,
    ceiling: Role,
    actor_role: Role = Role.SUPER_ADMIN,
    add_second_super_admin: bool = False,
) -> tuple[AuthContext, Operator, Operator | None]:
    department = Department(
        name=name,
        password_hash=hash_password("operator-admin-test-password"),
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    session.add(department)
    await session.flush()
    actor = Operator(
        department_id=department.id,
        name=f"{name} Actor",
        role=actor_role,
        status=OperatorStatus.ACTIVE,
        password_hash=hash_password(f"{name}-actor-password"),
        credential_version=1,
    )
    second = (
        Operator(
            department_id=department.id,
            name=f"{name} Second SA",
            role=Role.SUPER_ADMIN,
            status=OperatorStatus.ACTIVE,
            password_hash=hash_password(f"{name}-second-password"),
            credential_version=1,
        )
        if add_second_super_admin
        else None
    )
    session.add_all((actor, *(() if second is None else (second,))))
    await session.flush()
    auth_session = AuthSession(
        department_id=department.id,
        operator_id=actor.id,
        operator_credential_version=actor.credential_version,
        token_hash=f"token-{name}",
        csrf_token_hash=f"csrf-{name}",
        ip="127.0.0.1",
        user_agent="operator-admin-test",
        expires_at=datetime(2031, 1, 1, tzinfo=UTC),
        revoked_at=None,
    )
    session.add_all([DepartmentPermission(department_id=department.id, role=ceiling), auth_session])
    await session.commit()
    context = AuthContext(
        department=department,
        operator=actor,
        role=ceiling,
        auth_session=auth_session,
        department_role_ceiling=ceiling,
        effective_role=actor_role,
    )
    return context, actor, second


async def _refresh_context(session: AsyncSession, context: AuthContext) -> None:
    assert context.operator is not None
    await session.refresh(context.department)
    await session.refresh(context.operator)
    await session.refresh(context.auth_session)


def test_operator_admin_create_and_grant_invariants() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            context, _actor, _second = await _seed_department(
                session,
                name="Create Invariants",
                ceiling=Role.SUPER_ADMIN,
            )
            service = OperatorAdminService(session, clock=FixedClock())

            normal = await service.create_operator(
                context,
                OperatorAdminCreateInput(
                    name="Normal",
                    password="normal-created-password",
                    role="operator",
                    module_grants=["data_updates", "campaigns", "campaigns"],
                ),
                ip="127.0.0.1",
                user_agent="operator-admin-test",
            )
            assert normal.module_grants == (ModuleKey.CAMPAIGNS, ModuleKey.DATA_UPDATES)
            stored_normal = await service.repository.get_operator_in_department(
                operator_id=normal.id,
                department_id=context.department.id,
            )
            assert stored_normal is not None
            assert stored_normal.password_hash is not None
            assert stored_normal.password_hash != "normal-created-password"
            assert verify_password(stored_normal.password_hash, "normal-created-password")
            assert stored_normal.credential_version == 1
            assert "password" not in normal.model_dump()

            super_admin = await service.create_operator(
                context,
                OperatorAdminCreateInput(
                    name="Direct SA",
                    password="direct-super-admin-password",
                    role="super_admin",
                    module_grants=[],
                ),
                ip="127.0.0.1",
                user_agent="operator-admin-test",
            )
            assert super_admin.role is Role.SUPER_ADMIN
            assert super_admin.module_grants == ()
            assert (
                await service.repository.list_operator_module_grants(
                    operator_id=super_admin.id,
                    department_id=context.department.id,
                )
                == ()
            )

            with pytest.raises(AuthError) as reuse:
                await service.create_operator(
                    context,
                    OperatorAdminCreateInput(
                        name="Department Password Reuse",
                        password="operator-admin-test-password",
                        role="operator",
                        module_grants=[],
                    ),
                    ip="127.0.0.1",
                    user_agent="operator-admin-test",
                )
            assert reuse.value.code == "PASSWORD_REUSE_FORBIDDEN"
            await _refresh_context(session, context)

            for payload, code in (
                (
                    OperatorAdminCreateInput(
                        name="Bad role",
                        password="rejected-payload-password",
                        role="made_up",
                        module_grants=[],
                    ),
                    "INVALID_ROLE",
                ),
                (
                    OperatorAdminCreateInput(
                        name="Bad module",
                        password="rejected-payload-password",
                        role="operator",
                        module_grants=["made_up"],
                    ),
                    "INVALID_MODULE_KEY",
                ),
                (
                    OperatorAdminCreateInput(
                        name="Admin grant",
                        password="rejected-payload-password",
                        role="operator",
                        module_grants=["admin"],
                    ),
                    "ADMIN_MODULE_NOT_GRANTABLE",
                ),
                (
                    OperatorAdminCreateInput(
                        name="SA grant",
                        password="rejected-payload-password",
                        role="super_admin",
                        module_grants=["campaigns"],
                    ),
                    "SUPER_ADMIN_GRANTS_FORBIDDEN",
                ),
            ):
                with pytest.raises(AuthError) as caught:
                    await service.create_operator(
                        context,
                        payload,
                        ip="127.0.0.1",
                        user_agent="operator-admin-test",
                    )
                assert caught.value.code == code
                await _refresh_context(session, context)

            audit_payloads = repr(
                [
                    (audit.before, audit.after)
                    for audit in await session.scalars(
                        select(AuditLog).where(AuditLog.department_id == context.department.id)
                    )
                ]
            )
            assert "normal-created-password" not in audit_payloads
            assert "direct-super-admin-password" not in audit_payloads

        async with database_session() as session:
            context, _actor, _second = await _seed_department(
                session,
                name="Ceiling Invariants",
                ceiling=Role.MANAGER,
            )
            service = OperatorAdminService(session, clock=FixedClock())
            with pytest.raises(AuthError) as caught:
                await service.create_operator(
                    context,
                    OperatorAdminCreateInput(
                        name="Forbidden SA",
                        password="forbidden-super-admin-password",
                        role="super_admin",
                        module_grants=[],
                    ),
                    ip="127.0.0.1",
                    user_agent="operator-admin-test",
                )
            assert caught.value.code == "PERMISSION_DENIED"

    asyncio.run(scenario())


def test_operator_admin_update_revocation_concurrency_and_audit() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            context, actor, second_super_admin = await _seed_department(
                session,
                name="Update Invariants",
                ceiling=Role.SUPER_ADMIN,
                add_second_super_admin=True,
            )
            assert second_super_admin is not None
            target = Operator(
                department_id=context.department.id,
                name="Target",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
                password_hash=hash_password("update-target-password"),
                credential_version=1,
            )
            session.add(target)
            await session.flush()
            target_session = AuthSession(
                department_id=context.department.id,
                operator_id=target.id,
                operator_credential_version=target.credential_version,
                token_hash="target-token",
                csrf_token_hash="target-csrf",
                ip="127.0.0.1",
                user_agent="target-session",
                expires_at=datetime(2031, 1, 1, tzinfo=UTC),
                revoked_at=None,
            )
            target_session_2 = AuthSession(
                department_id=context.department.id,
                operator_id=target.id,
                operator_credential_version=target.credential_version,
                token_hash="target-token-2",
                csrf_token_hash="target-csrf-2",
                ip="127.0.0.1",
                user_agent="target-session-2",
                expires_at=datetime(2031, 1, 1, tzinfo=UTC),
                revoked_at=None,
            )
            session.add_all((target_session, target_session_2))
            await session.commit()
            service = OperatorAdminService(session, clock=FixedClock())

            initial = _aware(target.updated_at)
            commits = 0

            def count_commit(_session: object) -> None:
                nonlocal commits
                commits += 1

            event.listen(session.sync_session, "after_commit", count_commit)
            try:
                changed = await service.update_operator(
                    context,
                    operator_id=target.id,
                    payload=OperatorAdminUpdateInput(
                        expected_updated_at=initial,
                        name="Renamed Target",
                        status="disabled",
                        module_grants=["data_updates", "campaigns", "campaigns"],
                    ),
                    ip="127.0.0.1",
                    user_agent="operator-admin-test",
                )
            finally:
                event.remove(session.sync_session, "after_commit", count_commit)
            assert commits == 1
            assert changed.name == "Renamed Target"
            assert changed.status is OperatorStatus.DISABLED
            assert changed.module_grants == (ModuleKey.CAMPAIGNS, ModuleKey.DATA_UPDATES)
            await session.refresh(target_session)
            assert target_session.revoked_at is not None
            await session.refresh(target_session_2)
            assert target_session_2.revoked_at is not None
            audit_count_after_change = int(
                await session.scalar(select(func.count()).select_from(AuditLog)) or 0
            )
            with pytest.raises(AuthError) as stale:
                await service.update_operator(
                    context,
                    operator_id=target.id,
                    payload=OperatorAdminUpdateInput(
                        expected_updated_at=initial,
                        name="Should not persist",
                    ),
                    ip="127.0.0.1",
                    user_agent="operator-admin-test",
                )
            assert stale.value.code == "VERSION_CONFLICT"
            await _refresh_context(session, context)
            await session.refresh(target)
            assert target.name == "Renamed Target"
            assert await service.repository.list_operator_module_grants(
                operator_id=target.id,
                department_id=context.department.id,
            ) == (ModuleKey.CAMPAIGNS, ModuleKey.DATA_UPDATES)
            assert int(await session.scalar(select(func.count()).select_from(AuditLog)) or 0) == (
                audit_count_after_change
            )

            with pytest.raises(AuthError) as promotion:
                await service.update_operator(
                    context,
                    operator_id=target.id,
                    payload=OperatorAdminUpdateInput(
                        expected_updated_at=_aware(target.updated_at),
                        role="super_admin",
                        module_grants=[],
                    ),
                    ip="127.0.0.1",
                    user_agent="operator-admin-test",
                )
            assert promotion.value.code == "SUPER_ADMIN_PROMOTION_FORBIDDEN"
            await _refresh_context(session, context)
            await session.refresh(target)

            third_super_admin = await service.create_operator(
                context,
                OperatorAdminCreateInput(
                    name="Third Super Admin",
                    password="third-super-admin-password",
                    role="super_admin",
                    module_grants=[],
                ),
                ip="127.0.0.1",
                user_agent="operator-admin-test",
            )
            await session.refresh(second_super_admin)
            safely_disabled = await service.update_operator(
                context,
                operator_id=second_super_admin.id,
                payload=OperatorAdminUpdateInput(
                    expected_updated_at=_aware(second_super_admin.updated_at),
                    status="disabled",
                ),
                ip="127.0.0.1",
                user_agent="operator-admin-test",
            )
            assert safely_disabled.status is OperatorStatus.DISABLED

            await session.refresh(actor)
            demoted = await service.update_operator(
                context,
                operator_id=actor.id,
                payload=OperatorAdminUpdateInput(
                    expected_updated_at=_aware(actor.updated_at),
                    role="manager",
                ),
                ip="127.0.0.1",
                user_agent="operator-admin-test",
            )
            assert demoted.role is Role.MANAGER

            audit_actions = set(
                await session.scalars(
                    select(AuditLog.action).where(AuditLog.department_id == context.department.id)
                )
            )
            assert AuditAction.OPERATOR_MODULE_GRANTS_CHANGED in audit_actions
            assert AuditAction.OPERATOR_STATUS_CHANGED in audit_actions
            assert AuditAction.OPERATOR_ROLE_CHANGED in audit_actions
            assert AuditAction.OPERATOR_UPDATED in audit_actions
            assert AuditAction.OPERATOR_CREATED in audit_actions
            assert (
                int(
                    await session.scalar(
                        select(func.count())
                        .select_from(AuditLog)
                        .where(AuditLog.department_id == context.department.id)
                    )
                    or 0
                )
                == 6
            )

            # The remaining active Super Admin is protected from both disable
            # and demotion, and rejections add no audit rows.
            third_operator = await service.repository.get_operator_in_department(
                operator_id=third_super_admin.id,
                department_id=context.department.id,
            )
            assert third_operator is not None
            # A migrated, credentialless Super Admin is not an authenticatable
            # fallback and must not permit removal of the last usable admin.
            session.add(
                Operator(
                    department_id=context.department.id,
                    name="Legacy Credentialless Super Admin",
                    role=Role.SUPER_ADMIN,
                    status=OperatorStatus.ACTIVE,
                    password_hash=None,
                    credential_version=0,
                )
            )
            await session.commit()
            second_auth_session = AuthSession(
                department_id=context.department.id,
                operator_id=third_operator.id,
                operator_credential_version=third_operator.credential_version,
                token_hash="second-context-token",
                csrf_token_hash="second-context-csrf",
                ip="127.0.0.1",
                user_agent="operator-admin-test",
                expires_at=datetime(2031, 1, 1, tzinfo=UTC),
                revoked_at=None,
            )
            session.add(second_auth_session)
            await session.commit()
            second_context = AuthContext(
                department=context.department,
                operator=third_operator,
                role=Role.SUPER_ADMIN,
                auth_session=second_auth_session,
                department_role_ceiling=Role.SUPER_ADMIN,
                effective_role=Role.SUPER_ADMIN,
            )
            before_rejection_audits = int(
                await session.scalar(select(func.count()).select_from(AuditLog)) or 0
            )
            for change in ({"status": "disabled"}, {"role": "manager"}):
                with pytest.raises(AuthError) as last_admin:
                    await service.update_operator(
                        second_context,
                        operator_id=third_operator.id,
                        payload=OperatorAdminUpdateInput(
                            expected_updated_at=_aware(third_operator.updated_at),
                            **change,
                        ),
                        ip="127.0.0.1",
                        user_agent="operator-admin-test",
                    )
                assert last_admin.value.code == "LAST_ACTIVE_SUPER_ADMIN"
                await session.refresh(third_operator)
                await session.refresh(context.department)
                await session.refresh(second_auth_session)
            assert int(await session.scalar(select(func.count()).select_from(AuditLog)) or 0) == (
                before_rejection_audits
            )

    asyncio.run(scenario())


def test_operator_admin_self_disable_revokes_caller_session() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            context, actor, _second = await _seed_department(
                session,
                name="Self Disable",
                ceiling=Role.SUPER_ADMIN,
                add_second_super_admin=True,
            )
            assert context.auth_session.operator_id == actor.id
            service = OperatorAdminService(session, clock=FixedClock())
            result = await service.update_operator(
                context,
                operator_id=actor.id,
                payload=OperatorAdminUpdateInput(
                    expected_updated_at=_aware(actor.updated_at),
                    status="disabled",
                ),
                ip="127.0.0.1",
                user_agent="operator-admin-test",
            )
            assert result.status is OperatorStatus.DISABLED
            await session.refresh(context.auth_session)
            assert context.auth_session.revoked_at is not None

    asyncio.run(scenario())


def test_operator_password_reset_rotates_version_revokes_sessions_and_preserves_profile() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            context, _actor, _second = await _seed_department(
                session,
                name="Password Reset",
                ceiling=Role.SUPER_ADMIN,
            )
            service = OperatorAdminService(session, clock=FixedClock())
            created = await service.create_operator(
                context,
                OperatorAdminCreateInput(
                    name="Reset Target",
                    password="initial-target-password",
                    role="operator",
                    module_grants=["campaigns", "data_updates"],
                ),
                ip="127.0.0.1",
                user_agent="operator-admin-test",
            )
            target = await service.repository.get_operator_in_department(
                operator_id=created.id,
                department_id=context.department.id,
            )
            assert target is not None
            before = {
                "department_id": target.department_id,
                "name": target.name,
                "role": target.role,
                "status": target.status,
                "created_at": target.created_at,
            }
            original_hash = target.password_hash
            original_version = target.credential_version
            active_sessions = [
                AuthSession(
                    department_id=context.department.id,
                    operator_id=target.id,
                    operator_credential_version=target.credential_version,
                    token_hash=f"reset-target-token-{index}",
                    csrf_token_hash=f"reset-target-csrf-{index}",
                    ip="127.0.0.1",
                    user_agent="reset-target",
                    expires_at=datetime(2031, 1, 1, tzinfo=UTC),
                    revoked_at=None,
                )
                for index in range(2)
            ]
            already_revoked = AuthSession(
                department_id=context.department.id,
                operator_id=target.id,
                operator_credential_version=target.credential_version,
                token_hash="reset-target-token-revoked",
                csrf_token_hash="reset-target-csrf-revoked",
                ip="127.0.0.1",
                user_agent="reset-target",
                expires_at=datetime(2031, 1, 1, tzinfo=UTC),
                revoked_at=datetime(2029, 1, 1, tzinfo=UTC),
            )
            session.add_all((*active_sessions, already_revoked))
            await session.commit()

            result = await service.reset_operator_password(
                context,
                operator_id=target.id,
                password="replacement-target-password",
                ip="127.0.0.1",
                user_agent="operator-admin-test",
            )
            assert result.operator_id == target.id
            assert result.revoked_sessions == 2
            assert set(result.model_dump()) == {"operator_id", "revoked_sessions"}
            await session.refresh(target)
            assert target.password_hash is not None
            assert target.password_hash != original_hash
            assert target.password_hash != "replacement-target-password"
            assert verify_password(target.password_hash, "replacement-target-password")
            assert target.credential_version == original_version + 1
            assert target.department_id == before["department_id"]
            assert target.name == before["name"]
            assert target.role is before["role"]
            assert target.status is before["status"]
            assert target.created_at == before["created_at"]
            assert await service.repository.list_operator_module_grants(
                operator_id=target.id,
                department_id=context.department.id,
            ) == (ModuleKey.CAMPAIGNS, ModuleKey.DATA_UPDATES)
            for auth_session in active_sessions:
                await session.refresh(auth_session)
                assert auth_session.revoked_at is not None
            await session.refresh(already_revoked)
            assert _aware(already_revoked.revoked_at) == datetime(2029, 1, 1, tzinfo=UTC)

            reset_audit = await session.scalar(
                select(AuditLog).where(AuditLog.action == AuditAction.OPERATOR_PASSWORD_RESET)
            )
            assert reset_audit is not None
            assert reset_audit.operator_id == context.operator.id
            assert reset_audit.entity_id == target.id
            assert reset_audit.before is None
            assert reset_audit.after == {"revoked_sessions": 2}
            assert "replacement-target-password" not in repr(
                (reset_audit.before, reset_audit.after)
            )

    asyncio.run(scenario())


def test_operator_admin_rolls_back_mutation_when_audit_insert_fails() -> None:
    class InjectedAuditFailure(RuntimeError):
        pass

    async def scenario() -> None:
        async with database_session() as session:
            context, _actor, _second = await _seed_department(
                session,
                name="Audit Rollback",
                ceiling=Role.SUPER_ADMIN,
            )
            service = OperatorAdminService(session, clock=FixedClock())
            department_id = context.department.id

            def fail_audit_insert(
                _connection: object,
                _cursor: object,
                statement: str,
                _parameters: object,
                _context: object,
                _executemany: bool,
            ) -> None:
                if statement.lstrip().upper().startswith("INSERT INTO AUDIT_LOGS"):
                    raise InjectedAuditFailure("synthetic audit failure")

            engine = session.bind
            assert engine is not None
            event.listen(engine.sync_engine, "before_cursor_execute", fail_audit_insert)
            try:
                with pytest.raises(InjectedAuditFailure):
                    await service.create_operator(
                        context,
                        OperatorAdminCreateInput(
                            name="Must Roll Back",
                            password="must-roll-back-password",
                            role="operator",
                            module_grants=["campaigns"],
                        ),
                        ip="127.0.0.1",
                        user_agent="operator-admin-test",
                    )
            finally:
                event.remove(engine.sync_engine, "before_cursor_execute", fail_audit_insert)

            assert (
                await service.repository.get_operator_by_name_in_department(
                    department_id=department_id,
                    name="Must Roll Back",
                )
                is None
            )
            assert int(await session.scalar(select(func.count()).select_from(AuditLog)) or 0) == 0

            await _refresh_context(session, context)
            target = Operator(
                department_id=department_id,
                name="Audit Rollback Target",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
                password_hash=hash_password("audit-rollback-target-password"),
                credential_version=1,
            )
            session.add(target)
            await session.flush()
            target_session = AuthSession(
                department_id=department_id,
                operator_id=target.id,
                operator_credential_version=target.credential_version,
                token_hash="audit-target-token",
                csrf_token_hash="audit-target-csrf",
                ip="127.0.0.1",
                user_agent="audit-target",
                expires_at=datetime(2031, 1, 1, tzinfo=UTC),
                revoked_at=None,
            )
            session.add(target_session)
            await session.commit()
            target_updated_at = _aware(target.updated_at)
            event.listen(engine.sync_engine, "before_cursor_execute", fail_audit_insert)
            try:
                with pytest.raises(InjectedAuditFailure):
                    await service.update_operator(
                        context,
                        operator_id=target.id,
                        payload=OperatorAdminUpdateInput(
                            expected_updated_at=target_updated_at,
                            status="disabled",
                            module_grants=["campaigns"],
                        ),
                        ip="127.0.0.1",
                        user_agent="operator-admin-test",
                    )
            finally:
                event.remove(engine.sync_engine, "before_cursor_execute", fail_audit_insert)
            await session.refresh(target)
            await session.refresh(target_session)
            assert target.status is OperatorStatus.ACTIVE
            assert target_session.revoked_at is None
            assert (
                await service.repository.list_operator_module_grants(
                    operator_id=target.id,
                    department_id=department_id,
                )
                == ()
            )
            assert int(await session.scalar(select(func.count()).select_from(AuditLog)) or 0) == 0

            await _refresh_context(session, context)
            original_password_hash = target.password_hash
            original_credential_version = target.credential_version
            event.listen(engine.sync_engine, "before_cursor_execute", fail_audit_insert)
            try:
                with pytest.raises(InjectedAuditFailure):
                    await service.reset_operator_password(
                        context,
                        operator_id=target.id,
                        password="audit-reset-replacement-password",
                        ip="127.0.0.1",
                        user_agent="operator-admin-test",
                    )
            finally:
                event.remove(engine.sync_engine, "before_cursor_execute", fail_audit_insert)
            await session.refresh(target)
            await session.refresh(target_session)
            assert target.password_hash == original_password_hash
            assert target.credential_version == original_credential_version
            assert target_session.revoked_at is None
            assert int(await session.scalar(select(func.count()).select_from(AuditLog)) or 0) == 0

    asyncio.run(scenario())

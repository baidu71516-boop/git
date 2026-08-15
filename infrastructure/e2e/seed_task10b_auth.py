"""Seed synthetic role fixtures for the isolated Task 10B browser E2E project."""

from __future__ import annotations

import asyncio
import os

from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import Department, DepartmentPermission, Operator
from backend_core.auth.repository import AuthRepository
from backend_core.auth.security import hash_password
from backend_core.db import Database

FIXTURES = (
    ("Task10B Manager", "Task10B Manager Operator", Role.MANAGER, "Task10B-E2E-Manager!"),
    ("Task10B Operator", "Task10B Operator User", Role.OPERATOR, "Task10B-E2E-Operator!"),
    ("Task10B Viewer", "Task10B Viewer User", Role.VIEWER, "Task10B-E2E-Viewer!"),
)


async def seed() -> None:
    database_url = os.environ["TASK10B_DATABASE_URL"]
    database = Database(database_url)
    try:
        async with database.session_factory() as session:
            repository = AuthRepository(session)
            for department_name, operator_name, role, password in FIXTURES:
                existing = await repository.get_department_by_name(department_name)
                if existing is not None:
                    raise RuntimeError(f"refusing to overwrite fixture: {department_name}")

                department = Department(
                    name=department_name,
                    password_hash=hash_password(password),
                    status=DepartmentStatus.ACTIVE,
                    session_days=30,
                )
                session.add(department)
                await session.flush()
                session.add_all(
                    [
                        DepartmentPermission(department_id=department.id, role=role),
                        Operator(
                            department_id=department.id,
                            name=operator_name,
                            role=role,
                            status=OperatorStatus.ACTIVE,
                        ),
                    ]
                )
                print(f"seeded {role.value}: {department.name} ({department.id})")
            await session.commit()
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(seed())

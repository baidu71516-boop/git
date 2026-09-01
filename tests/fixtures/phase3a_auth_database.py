"""Database fixtures for auth tests against SQLite or isolated PostgreSQL."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from backend_core.db.base import Base
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.schema import CreateSchema, DropSchema

_SQLITE_AUTH_TABLES = (
    "departments",
    "department_permissions",
    "operators",
    "operator_module_permissions",
    "sessions",
    "audit_logs",
)


@asynccontextmanager
async def database_session() -> AsyncIterator[AsyncSession]:
    """Yield a fresh auth schema without weakening production metadata."""

    raw_url = os.environ.get("TEST_DATABASE_URL")
    admin_engine = None
    async_engine: AsyncEngine
    schema_name: str | None = None
    schema_created = False

    if raw_url:
        url = make_url(raw_url)
        database_name = (url.database or "").lower()
        if url.get_backend_name() != "postgresql" or "phase1b_test" not in database_name:
            raise RuntimeError(
                "TEST_DATABASE_URL must be PostgreSQL and its database name must contain "
                "'phase1b_test'"
            )
        url = url.set(drivername="postgresql+psycopg")
        schema_name = f"phase3a_auth_{uuid4().hex}"
        admin_engine = create_engine(url, pool_pre_ping=True)
        with admin_engine.begin() as connection:
            connection.execute(CreateSchema(schema_name))
        schema_created = True
        scoped_url = url.update_query_dict({"options": f"-csearch_path={schema_name}"})
        async_engine = create_async_engine(scoped_url, pool_pre_ping=True)
    else:
        async_engine = create_async_engine("sqlite+aiosqlite://")

    try:
        async with async_engine.begin() as connection:
            if raw_url:
                await connection.run_sync(Base.metadata.create_all)
            else:
                tables = [Base.metadata.tables[name] for name in _SQLITE_AUTH_TABLES]
                await connection.run_sync(Base.metadata.create_all, tables=tables)
        factory = async_sessionmaker(async_engine, expire_on_commit=False)
        async with factory() as session:
            yield session
    finally:
        await async_engine.dispose()
        if admin_engine is not None:
            try:
                if schema_created and schema_name is not None:
                    with admin_engine.begin() as connection:
                        connection.execute(DropSchema(schema_name, cascade=True, if_exists=True))
            finally:
                admin_engine.dispose()

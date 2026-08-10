"""Shared SQLAlchemy engine and session lifecycle."""

from importlib import import_module

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class Database:
    """Own the process database engine and typed session factory."""

    def __init__(self, database_url: str) -> None:
        # Import lazily: audit/auth model modules import db.base while they are
        # still initializing, but every database-using process needs the full
        # metadata graph before an ORM flush can order cross-domain FKs.
        import_module("backend_core.db.models")
        self.engine = create_async_engine(database_url, pool_pre_ping=True)
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def close(self) -> None:
        await self.engine.dispose()

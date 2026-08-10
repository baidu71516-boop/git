"""Dependency readiness checks shared by entrypoints."""

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from backend_core.config import Settings


@dataclass(frozen=True)
class DependencyStatus:
    """Health state for a single dependency."""

    status: str
    detail: str | None = None


class HealthDependencies:
    """Own database and Redis clients used by readiness checks."""

    def __init__(self, settings: Settings) -> None:
        self._engine: AsyncEngine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
        )
        self._redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)

    async def check_database(self) -> DependencyStatus:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return DependencyStatus(status="ok")
        except Exception as exc:  # noqa: BLE001 - health endpoint must report dependency failure
            return DependencyStatus(status="error", detail=type(exc).__name__)

    async def check_redis(self) -> DependencyStatus:
        try:
            await self._redis.ping()
            return DependencyStatus(status="ok")
        except Exception as exc:  # noqa: BLE001 - health endpoint must report dependency failure
            return DependencyStatus(status="error", detail=type(exc).__name__)

    async def close(self) -> None:
        await self._redis.aclose()
        await self._engine.dispose()

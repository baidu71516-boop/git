"""FastAPI process entrypoint with no domain business logic."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from backend_core.common.health import HealthDependencies
from backend_core.common.logging import configure_logging
from backend_core.config import get_settings
from backend_core.db import Database
from fastapi import FastAPI
from redis.asyncio import Redis

from app.http.admin import router as admin_router
from app.http.auth import router as auth_router
from app.http.departments import router as departments_router
from app.http.errors import register_exception_handlers
from app.http.health import router as health_router
from app.http.middleware import RequestIdMiddleware
from app.http.operators import router as operators_router

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    dependencies = HealthDependencies(settings)
    database = Database(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.health_dependencies = dependencies
    app.state.database = database
    app.state.redis = redis
    try:
        yield
    finally:
        await redis.aclose()
        await database.close()
        await dependencies.close()


app = FastAPI(
    title="Influencer Outreach API",
    version=settings.app_version,
    lifespan=lifespan,
)
app.add_middleware(RequestIdMiddleware)
app.include_router(health_router)
app.include_router(departments_router)
app.include_router(auth_router)
app.include_router(operators_router)
app.include_router(admin_router)
register_exception_handlers(app)

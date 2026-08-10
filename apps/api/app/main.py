"""FastAPI process entrypoint with no domain business logic."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from backend_core.common.health import HealthDependencies
from backend_core.common.logging import configure_logging
from backend_core.config import get_settings
from fastapi import FastAPI

from app.http.errors import register_exception_handlers
from app.http.health import router as health_router
from app.http.middleware import RequestIdMiddleware

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    dependencies = HealthDependencies(settings)
    app.state.health_dependencies = dependencies
    try:
        yield
    finally:
        await dependencies.close()


app = FastAPI(
    title="Influencer Outreach API",
    version=settings.app_version,
    lifespan=lifespan,
)
app.add_middleware(RequestIdMiddleware)
app.include_router(health_router)
register_exception_handlers(app)

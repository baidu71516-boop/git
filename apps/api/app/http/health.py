"""Infrastructure liveness and readiness endpoints."""

import asyncio
from typing import Any

from backend_core.common.health import HealthDependencies
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.http.responses import envelope

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live(request: Request) -> dict[str, Any]:
    return envelope(request, data={"status": "ok"})


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    dependencies: HealthDependencies = request.app.state.health_dependencies
    database, redis = await asyncio.gather(
        dependencies.check_database(),
        dependencies.check_redis(),
    )
    checks = {
        "database": {"status": database.status, "detail": database.detail},
        "redis": {"status": redis.status, "detail": redis.detail},
    }
    is_ready = all(check["status"] == "ok" for check in checks.values())
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content=envelope(
            request,
            data={"status": "ok" if is_ready else "not_ready", "checks": checks},
        ),
    )

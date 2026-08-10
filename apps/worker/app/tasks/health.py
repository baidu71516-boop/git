"""Phase 0 smoke task; contains no product business logic."""

from typing import TypedDict

from app.celery_app import celery_app


class PingResult(TypedDict):
    status: str


@celery_app.task(name="phase0.ping", bind=False)  # type: ignore[untyped-decorator]
def ping() -> PingResult:
    """Verify that a worker can import and execute a task."""

    return {"status": "ok"}

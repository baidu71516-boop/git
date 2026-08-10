"""Request-scoped identifiers without coupling the core package to FastAPI."""

from contextvars import ContextVar

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)

"""Uniform API response envelope."""

from typing import Any

from fastapi import Request


def envelope(
    request: Request,
    *,
    data: Any = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the response shape required by API_SPEC.md."""

    return {
        "success": error is None,
        "data": data,
        "error": error,
        "request_id": request.state.request_id,
    }

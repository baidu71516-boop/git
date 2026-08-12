"""Uniform API response envelope."""

from typing import Any, Literal

from fastapi import Request
from pydantic import BaseModel


class SuccessEnvelope[DataT](BaseModel):
    """Typed OpenAPI contract for the existing successful response envelope."""

    success: Literal[True] = True
    data: DataT
    error: None = None
    request_id: str


class ErrorBody(BaseModel):
    """HTTP-safe error fields emitted by the registered exception handlers."""

    code: str
    message: str
    details: Any = None


class ErrorEnvelope(BaseModel):
    """Typed OpenAPI contract for the existing failed response envelope."""

    success: Literal[False] = False
    data: None = None
    error: ErrorBody
    request_id: str


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

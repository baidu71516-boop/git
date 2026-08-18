"""Small HTTP-only helpers shared by the closed Phase 3A routers."""

from __future__ import annotations

import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any
from uuid import UUID

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, ValidationError

from app.http.errors import ApiError
from app.http.responses import ErrorEnvelope


class Phase3AHttpWrite(BaseModel):
    """Strict public write shape; resolved Department is never a body field."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Describe the existing typed error envelope for OpenAPI."""

    return {
        status_code: {"model": ErrorEnvelope, "description": "Error response"}
        for status_code in status_codes
    }


def closed_query_validation_error(name: str, message: str) -> RequestValidationError:
    return RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": ("query", name),
                "msg": message,
                "input": None,
                "ctx": {"error": ValueError(message)},
            }
        ]
    )


def reject_closed_query_parameters(request: Request, allowed: frozenset[str]) -> None:
    """Reject unknown and repeated scalar query parameters before service work."""

    unexpected = sorted(set(request.query_params) - allowed)
    if unexpected:
        name = unexpected[0]
        raise closed_query_validation_error(name, "Extra inputs are not permitted")
    for name in allowed:
        if len(request.query_params.getlist(name)) > 1:
            raise closed_query_validation_error(name, "query parameter must not be repeated")


def _cursor_validation_error() -> RequestValidationError:
    return closed_query_validation_error("cursor", "cursor is invalid")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate cursor key")
        result[key] = value
    return result


def decode_keyset_cursor[CursorT: BaseModel](
    value: str | None, model_type: type[CursorT]
) -> CursorT | None:
    """Decode the non-Today tuple cursor into its closed core DTO."""

    if value is None:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(
            urlsafe_b64decode((value + padding).encode("ascii")).decode("utf-8"),
            object_pairs_hook=_unique_json_object,
        )
        if not isinstance(payload, dict):
            raise ValueError("cursor payload must be an object")
        return model_type.model_validate(payload)
    except (UnicodeError, ValueError, ValidationError, json.JSONDecodeError) as error:
        raise _cursor_validation_error() from error


def encode_keyset_cursor(cursor: BaseModel | None) -> str | None:
    """Encode the final returned keyset tuple without exposing a mutable ORM object."""

    if cursor is None:
        return None
    payload = json.dumps(
        cursor.model_dump(mode="json"),
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def domain_input_with_scope[DomainInputT: BaseModel](
    payload: BaseModel,
    model_type: type[DomainInputT],
    *,
    department_id: UUID,
) -> DomainInputT:
    """Inject the resolved scope into a strict API-independent domain input."""

    try:
        return model_type.model_validate(
            {**payload.model_dump(mode="python"), "department_id": department_id}
        )
    except ValidationError as error:
        raise RequestValidationError(error.errors()) from error


def require_single_idempotency_key(request: Request, idempotency_key: str | None) -> str:
    """Require exactly one non-empty opaque Idempotency-Key header."""

    if len(request.headers.getlist("idempotency-key")) != 1 or not idempotency_key:
        raise ApiError(422, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key is invalid")
    return idempotency_key


__all__ = [
    "Phase3AHttpWrite",
    "closed_query_validation_error",
    "decode_keyset_cursor",
    "domain_input_with_scope",
    "encode_keyset_cursor",
    "error_responses",
    "reject_closed_query_parameters",
    "require_single_idempotency_key",
]

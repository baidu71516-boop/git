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

CSRF_HEADER = "X-CSRF-Token"
IDEMPOTENCY_HEADER = "Idempotency-Key"

# The runtime guards deliberately bind these headers as optional so missing
# values retain the established domain error envelopes. FastAPI would otherwise
# document them as optional, so the API process applies this exact matrix after
# generating its schema.
PHASE3A_MUTATION_HEADER_REQUIREMENTS: dict[tuple[str, str], frozenset[str]] = {
    ("/api/v1/admin/content-activity/xiaohongshu/identities/resolve", "post"): frozenset(
        {CSRF_HEADER, IDEMPOTENCY_HEADER}
    ),
    ("/api/v1/admin/content-activity/xiaohongshu/refreshes", "post"): frozenset(
        {CSRF_HEADER, IDEMPOTENCY_HEADER}
    ),
    ("/api/v1/admin/content-activity/douyin/runtime-captures", "post"): frozenset(
        {CSRF_HEADER, IDEMPOTENCY_HEADER}
    ),
    ("/api/v1/candidate-pools", "post"): frozenset({CSRF_HEADER, IDEMPOTENCY_HEADER}),
    ("/api/v1/candidate-pools/{pool_id}/policies", "post"): frozenset(
        {CSRF_HEADER, IDEMPOTENCY_HEADER}
    ),
    ("/api/v1/candidate-pools/{pool_id}/runs", "post"): frozenset(
        {CSRF_HEADER, IDEMPOTENCY_HEADER}
    ),
    ("/api/v1/buyer-prospects", "post"): frozenset({CSRF_HEADER, IDEMPOTENCY_HEADER}),
    ("/api/v1/buyer-prospects/{pool_id}", "put"): frozenset({CSRF_HEADER}),
    ("/api/v1/buyer-prospects/{pool_id}/lifecycle", "post"): frozenset({CSRF_HEADER}),
    ("/api/v1/buyer-prospects/{pool_id}/runs", "post"): frozenset(
        {CSRF_HEADER, IDEMPOTENCY_HEADER}
    ),
    ("/api/v1/collection-jobs/{collection_job_id}/buyer-screening", "post"):  frozenset(
        {CSRF_HEADER, IDEMPOTENCY_HEADER}
    ),
    ("/api/v1/campaigns", "post"): frozenset({CSRF_HEADER, IDEMPOTENCY_HEADER}),
    ("/api/v1/campaigns/{campaign_id}", "put"): frozenset({CSRF_HEADER}),
    ("/api/v1/campaigns/{campaign_id}/lifecycle", "post"): frozenset({CSRF_HEADER}),
    ("/api/v1/campaigns/{campaign_id}/members/bulk-add", "post"): frozenset(
        {CSRF_HEADER, IDEMPOTENCY_HEADER}
    ),
    ("/api/v1/campaigns/{campaign_id}/members/from-candidate-run", "post"): frozenset(
        {CSRF_HEADER, IDEMPOTENCY_HEADER}
    ),
    ("/api/v1/campaigns/{campaign_id}/members/{member_id}/remove", "post"): frozenset(
        {CSRF_HEADER}
    ),
    ("/api/v1/campaigns/{campaign_id}/outreach-targets", "post"): frozenset(
        {CSRF_HEADER, IDEMPOTENCY_HEADER}
    ),
    ("/api/v1/outreach-targets/{target_id}", "put"): frozenset({CSRF_HEADER}),
    ("/api/v1/outreach-targets/{target_id}/tasks", "post"): frozenset(
        {CSRF_HEADER, IDEMPOTENCY_HEADER}
    ),
    ("/api/v1/outreach-tasks/{task_id}/transitions", "post"): frozenset(
        {CSRF_HEADER, IDEMPOTENCY_HEADER}
    ),
}


class Phase3AHttpWrite(BaseModel):
    """Strict public write shape; resolved Department is never a body field."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Describe the existing typed error envelope for OpenAPI."""

    return {
        status_code: {"model": ErrorEnvelope, "description": "Error response"}
        for status_code in status_codes
    }


def apply_phase3a_mutation_openapi_header_requirements(document: dict[str, Any]) -> None:
    """Mark the existing runtime-mandatory Phase 3A headers required in OpenAPI."""

    paths = document["paths"]
    for (path, method), required_headers in PHASE3A_MUTATION_HEADER_REQUIREMENTS.items():
        parameters = paths[path][method].get("parameters", [])
        headers = {
            parameter["name"]: parameter for parameter in parameters if parameter["in"] == "header"
        }
        for header in required_headers:
            try:
                headers[header]["required"] = True
            except KeyError as error:
                raise RuntimeError(
                    f"Phase 3A OpenAPI is missing {header} for {method.upper()} {path}"
                ) from error


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
    "apply_phase3a_mutation_openapi_header_requirements",
    "Phase3AHttpWrite",
    "closed_query_validation_error",
    "decode_keyset_cursor",
    "domain_input_with_scope",
    "encode_keyset_cursor",
    "error_responses",
    "reject_closed_query_parameters",
    "require_single_idempotency_key",
]

"""Shared persistence primitives for Phase 3A mutation idempotency."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.growth.enums import Phase3AOperationScope
from backend_core.growth.models import Phase3AIdempotencyRecord
from backend_core.imports.hashing import hash_document

MAX_IDEMPOTENCY_KEY_LENGTH = 255


def canonical_request_hash(value: object) -> str:
    """Return the stable SHA-256 hash for a semantic mutation request."""

    return hash_document(value)


def validate_idempotency_key(key: str) -> str:
    """Validate an opaque key without normalizing its exact stored value."""

    if not key or key.isspace():
        raise ValueError("Idempotency key must be non-empty")
    if len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise ValueError("Idempotency key must be at most 255 characters")
    return key


class Phase3AIdempotencyRepository:
    """Read and stage immutable Phase 3A replay records without committing."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self,
        department_id: UUID,
        operation_scope: Phase3AOperationScope,
        idempotency_key: str,
        *,
        for_update: bool = False,
    ) -> Phase3AIdempotencyRecord | None:
        statement = (
            select(Phase3AIdempotencyRecord)
            .where(
                Phase3AIdempotencyRecord.department_id == department_id,
                Phase3AIdempotencyRecord.operation_scope == operation_scope,
                Phase3AIdempotencyRecord.idempotency_key == idempotency_key,
            )
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(Phase3AIdempotencyRecord | None, await self.session.scalar(statement))

    def add(
        self,
        *,
        department_id: UUID,
        operation_scope: Phase3AOperationScope,
        idempotency_key: str,
        request_hash: str,
        result_entity_id: UUID,
        result_payload: Mapping[str, Any],
        result_schema_version: int = 1,
    ) -> Phase3AIdempotencyRecord:
        """Stage one successful result for durable replay in the caller's transaction."""

        record = Phase3AIdempotencyRecord(
            department_id=department_id,
            operation_scope=operation_scope,
            idempotency_key=validate_idempotency_key(idempotency_key),
            request_hash=request_hash,
            result_entity_id=result_entity_id,
            result_schema_version=result_schema_version,
            result_payload=dict(result_payload),
        )
        self.session.add(record)
        return record

"""Write-only audit repository for Phase 1A services."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.audit.models import AuditLog


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(
        self,
        *,
        action: AuditAction,
        result: AuditResult,
        department_id: UUID | None,
        operator_id: UUID | None,
        ip: str,
        user_agent: str,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> AuditLog:
        record = AuditLog(
            action=action,
            result=result,
            department_id=department_id,
            operator_id=operator_id,
            ip=ip,
            user_agent=user_agent,
            entity_type=entity_type,
            entity_id=entity_id,
            before=before,
            after=after,
        )
        self.session.add(record)
        return record

"""Immutable security and business audit records."""

from typing import Any
from uuid import UUID

from sqlalchemy import JSON, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.db.base import Base
from backend_core.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin


def audit_enum_values(enum_type: type[AuditAction] | type[AuditResult]) -> list[str]:
    return [item.value for item in enum_type]


class AuditLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_department_created", "department_id", "created_at"),
        Index("ix_audit_logs_operator_created", "operator_id", "created_at"),
        Index("ix_audit_logs_action_created", "action", "created_at"),
    )

    department_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
    )
    operator_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("operators.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[AuditAction] = mapped_column(
        Enum(AuditAction, name="audit_action", values_callable=audit_enum_values),
        nullable=False,
    )
    result: Mapped[AuditResult] = mapped_column(
        Enum(AuditResult, name="audit_result", values_callable=audit_enum_values),
        nullable=False,
    )
    entity_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    entity_id: Mapped[UUID | None] = mapped_column(nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str] = mapped_column(String(64), nullable=False)
    user_agent: Mapped[str] = mapped_column(String(512), nullable=False)

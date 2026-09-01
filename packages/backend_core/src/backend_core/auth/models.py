"""Department, operator, authorization, and session persistence."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend_core.auth.enums import (
    CANONICAL_MODULE_KEY_ORDER,
    DepartmentStatus,
    ModuleKey,
    OperatorStatus,
    Role,
)
from backend_core.db.base import Base
from backend_core.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin


def enum_values(
    enum_type: type[Role] | type[DepartmentStatus] | type[ModuleKey] | type[OperatorStatus],
) -> list[str]:
    return [item.value for item in enum_type]


class Department(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "departments"

    name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[DepartmentStatus] = mapped_column(
        Enum(
            DepartmentStatus,
            name="department_status",
            values_callable=enum_values,
        ),
        nullable=False,
        default=DepartmentStatus.ACTIVE,
    )
    session_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

    operators: Mapped[list["Operator"]] = relationship(back_populates="department")
    permission: Mapped["DepartmentPermission"] = relationship(
        back_populates="department",
        uselist=False,
    )


class DepartmentPermission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "department_permissions"
    __table_args__ = (
        UniqueConstraint("department_id", name="uq_department_permissions_department"),
    )

    department_id: Mapped[UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="role_enum", values_callable=enum_values),
        nullable=False,
    )

    department: Mapped[Department] = relationship(back_populates="permission")


class Operator(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "operators"
    __table_args__ = (
        UniqueConstraint("department_id", "name", name="uq_operators_department_name"),
        UniqueConstraint("id", "department_id", name="uq_operators_id_department"),
        Index("ix_operators_department_status", "department_id", "status"),
    )

    department_id: Mapped[UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="role_enum", values_callable=enum_values),
        nullable=False,
    )
    status: Mapped[OperatorStatus] = mapped_column(
        Enum(
            OperatorStatus,
            name="operator_status",
            values_callable=enum_values,
        ),
        nullable=False,
        default=OperatorStatus.ACTIVE,
    )

    department: Mapped[Department] = relationship(back_populates="operators")


_MODULE_KEY_CHECK_VALUES = ", ".join(
    f"'{module_key.value}'" for module_key in CANONICAL_MODULE_KEY_ORDER
)


class OperatorModulePermission(TimestampMixin, Base):
    """One positive, same-Department module grant for one Operator.

    The table deliberately has no surrogate ID, policy blob, allow/deny flag,
    wildcard, action, or resource scope.  A row is the grant; its absence is a
    denial.
    """

    __tablename__ = "operator_module_permissions"
    __table_args__ = (
        PrimaryKeyConstraint(
            "operator_id",
            "module_key",
            name="pk_operator_module_permissions",
        ),
        ForeignKeyConstraint(
            ["operator_id", "department_id"],
            ["operators.id", "operators.department_id"],
            name="fk_operator_module_permissions_operator_department",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            f"module_key IN ({_MODULE_KEY_CHECK_VALUES})",
            name="ck_operator_module_permissions_module_key",
        ),
        Index(
            "ix_operator_module_permissions_department_operator",
            "department_id",
            "operator_id",
        ),
    )

    operator_id: Mapped[UUID] = mapped_column(nullable=False)
    department_id: Mapped[UUID] = mapped_column(nullable=False)
    module_key: Mapped[ModuleKey] = mapped_column(
        Enum(
            ModuleKey,
            name="operator_module_key",
            native_enum=False,
            create_constraint=False,
            length=18,
            values_callable=enum_values,
        ),
        nullable=False,
    )


class AuthSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_department_active", "department_id", "revoked_at", "expires_at"),
        Index("ix_sessions_operator", "operator_id"),
    )

    department_id: Mapped[UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
    )
    operator_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("operators.id", ondelete="SET NULL"),
        nullable=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ip: Mapped[str] = mapped_column(String(64), nullable=False)
    user_agent: Mapped[str] = mapped_column(String(512), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


ModelValue = str | int | bool | UUID | datetime | None | dict[str, Any]

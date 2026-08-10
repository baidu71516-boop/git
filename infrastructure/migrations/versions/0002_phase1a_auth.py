"""Create Phase 1A department authentication, session, RBAC, and audit tables.

Revision ID: 0002_phase1a
Revises: 0001_phase0
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_phase1a"
down_revision: str | None = "0001_phase0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

department_status = postgresql.ENUM("active", "disabled", name="department_status")
operator_status = postgresql.ENUM("active", "disabled", name="operator_status")
role_enum = postgresql.ENUM(
    "super_admin",
    "manager",
    "operator",
    "viewer",
    name="role_enum",
)
audit_action = postgresql.ENUM(
    "LOGIN_SUCCESS",
    "LOGIN_FAILED",
    "LOGIN_LOCKED",
    "LOGOUT",
    "OPERATOR_SELECTED",
    "PASSWORD_RESET",
    "BOOTSTRAP_ADMIN",
    name="audit_action",
)
audit_result = postgresql.ENUM("success", "failed", "denied", name="audit_result")


def timestamp_columns() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def upgrade() -> None:
    bind = op.get_bind()
    department_status.create(bind, checkfirst=True)
    operator_status.create(bind, checkfirst=True)
    role_enum.create(bind, checkfirst=True)
    audit_action.create(bind, checkfirst=True)
    audit_result.create(bind, checkfirst=True)

    op.create_table(
        "departments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="department_status", create_type=False),
            nullable=False,
        ),
        sa.Column("session_days", sa.Integer(), nullable=False, server_default="30"),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "department_permissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(name="role_enum", create_type=False),
            nullable=False,
        ),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("department_id", name="uq_department_permissions_department"),
    )

    op.create_table(
        "operators",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(name="role_enum", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="operator_status", create_type=False),
            nullable=False,
        ),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("department_id", "name", name="uq_operators_department_name"),
    )
    op.create_index(
        "ix_operators_department_status",
        "operators",
        ["department_id", "status"],
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["operator_id"], ["operators.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_sessions_department_active",
        "sessions",
        ["department_id", "revoked_at", "expires_at"],
    )
    op.create_index("ix_sessions_operator", "sessions", ["operator_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=True),
        sa.Column("operator_id", sa.Uuid(), nullable=True),
        sa.Column(
            "action",
            postgresql.ENUM(name="audit_action", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "result",
            postgresql.ENUM(name="audit_result", create_type=False),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=80), nullable=True),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["operator_id"], ["operators.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_logs_department_created",
        "audit_logs",
        ["department_id", "created_at"],
    )
    op.create_index(
        "ix_audit_logs_operator_created",
        "audit_logs",
        ["operator_id", "created_at"],
    )
    op.create_index(
        "ix_audit_logs_action_created",
        "audit_logs",
        ["action", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("sessions")
    op.drop_table("operators")
    op.drop_table("department_permissions")
    op.drop_table("departments")

    bind = op.get_bind()
    audit_result.drop(bind, checkfirst=True)
    audit_action.drop(bind, checkfirst=True)
    role_enum.drop(bind, checkfirst=True)
    operator_status.drop(bind, checkfirst=True)
    department_status.drop(bind, checkfirst=True)

"""Add independent Operator credentials and session binding versions.

Revision ID: 0011_operator_auth_p0
Revises: 0010_permissions_v1_persistence
Create Date: 2026-09-01

Existing Operators intentionally remain uninitialized: ``password_hash`` is
NULL and ``credential_version`` is zero.  Existing bound sessions likewise
receive no credential-version snapshot, allowing the application to reject
legacy passwordless bindings fail closed.

Downgrade is permitted only while every Operator credential is still
uninitialized.  Once any password hash exists, dropping the credential columns
would destroy authentication material, so the migration refuses the operation.
PostgreSQL audit enum labels remain append-only on downgrade.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_operator_auth_p0"
down_revision: str | None = "0010_permissions_v1_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUDIT_ACTIONS = (
    "OPERATOR_AUTHENTICATED",
    "OPERATOR_AUTH_FAILED",
    "OPERATOR_CREDENTIAL_SET",
    "OPERATOR_PASSWORD_RESET",
)


def _add_audit_actions() -> None:
    """Append the P0 audit labels without attempting unsafe enum rewrites."""

    with op.get_context().autocommit_block():
        for action in AUDIT_ACTIONS:
            op.execute(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{action}'")


def upgrade() -> None:
    _add_audit_actions()
    op.add_column("operators", sa.Column("password_hash", sa.Text(), nullable=True))
    op.add_column(
        "operators",
        sa.Column(
            "credential_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "sessions",
        sa.Column("operator_credential_version", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    # Take the destructive DDL lock before evaluating the guard. Once granted,
    # prior credential writers are visible to this READ COMMITTED transaction
    # and no new writer can commit between the count and the column drops.
    op.execute("LOCK TABLE operators IN ACCESS EXCLUSIVE MODE")
    bind = op.get_bind()
    initialized_operator_count = int(
        bind.execute(
            sa.text("SELECT count(*) FROM operators WHERE password_hash IS NOT NULL")
        ).scalar_one()
    )
    if initialized_operator_count:
        raise RuntimeError(
            "Operator Auth P0 downgrade blocked: initialized Operator credentials exist"
        )

    op.drop_column("sessions", "operator_credential_version")
    op.drop_column("operators", "credential_version")
    op.drop_column("operators", "password_hash")
    # PostgreSQL audit_action labels are append-only.  Retaining the four P0
    # labels is fail-safe and preserves any audit history already written.

"""Persist Buyer V1 lead tiers after Operator Auth P0.

Revision ID: 0012_buyer_lead_tiers_v1
Revises: 0011_operator_auth_p0
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_buyer_lead_tiers_v1"
down_revision: str | None = "0011_operator_auth_p0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BUYER_LEAD_TIER_VALUES = ("HIGH", "CHANGED", "RELATED", "SAME_CATEGORY", "UNKNOWN")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # PostgreSQL enum labels are append-only.  Keeping NOT_MATCH on a later
        # downgrade is harmless and avoids destructive enum recreation.
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE candidate_result ADD VALUE IF NOT EXISTS 'NOT_MATCH'")
        buyer_tier_type = postgresql.ENUM(
            *BUYER_LEAD_TIER_VALUES, name="buyer_lead_tier", create_type=False
        )
        postgresql.ENUM(*BUYER_LEAD_TIER_VALUES, name="buyer_lead_tier").create(
            bind, checkfirst=True
        )
    else:
        buyer_tier_type = sa.Enum(*BUYER_LEAD_TIER_VALUES, name="buyer_lead_tier")

    op.add_column(
        "candidate_pool_members",
        sa.Column("buyer_lead_tier", buyer_tier_type, nullable=True),
    )
    op.add_column(
        "candidate_pool_members",
        sa.Column("buyer_relation_summary", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_candidate_pool_members_run_buyer_tier",
        "candidate_pool_members",
        ["run_id", "buyer_lead_tier", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_pool_members_run_buyer_tier", table_name="candidate_pool_members")
    op.drop_column("candidate_pool_members", "buyer_relation_summary")
    op.drop_column("candidate_pool_members", "buyer_lead_tier")
    if op.get_bind().dialect.name == "postgresql":
        postgresql.ENUM(name="buyer_lead_tier").drop(op.get_bind(), checkfirst=True)

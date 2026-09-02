"""Add the Market Prospect Rule V1 lifecycle enum value.

Revision ID: 0013_market_prospect_rules_v1_provisional
Revises: 0012_buyer_lead_tiers_v1
Create Date: 2026-09-02

This branch-local revision is intentionally provisional until composed with
concurrent production migration work.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013_market_prospect_rules_v1_provisional"
down_revision: str | None = "0012_buyer_lead_tiers_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Required handoff marker; final production composition owns the permanent ID.
FINAL_PRODUCTION_MIGRATION_REVISION_PENDING_COMPOSITION = "YES"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # PostgreSQL enum labels are append-only.  The new lifecycle state is
        # used only by Market Prospect Rules, while existing pools retain their
        # current values unchanged.
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE candidate_pool_status ADD VALUE IF NOT EXISTS 'DISABLED'")


def downgrade() -> None:
    # PostgreSQL cannot safely remove an enum label without rebuilding every
    # dependent column.  Leaving the additive label is safe for prior code.
    pass

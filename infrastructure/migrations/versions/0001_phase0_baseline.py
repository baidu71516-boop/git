"""Create the Phase 0 migration baseline without business tables.

Revision ID: 0001_phase0
Revises:
Create Date: 2026-08-10
"""

revision: str = "0001_phase0"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Phase 0 intentionally introduces no domain schema."""


def downgrade() -> None:
    """The empty baseline has nothing to remove."""

"""Add durable Phase 3A idempotency persistence and Audit actions.

Revision ID: 0007_phase3a_persistence_amendment
Revises: 0006_phase3a_persistence
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_phase3a_persistence_amendment"
down_revision: str | None = "0006_phase3a_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

phase3a_operation_scope = postgresql.ENUM(
    "CANDIDATE_POOL_CREATE",
    "TARGETING_POLICY_CREATE",
    "CAMPAIGN_CREATE",
    "CAMPAIGN_MEMBER_BULK_ADD",
    "OUTREACH_TARGET_CREATE",
    name="phase3a_operation_scope",
)

AUDIT_ACTIONS = (
    "CANDIDATE_POOL_CREATED",
    "CANDIDATE_POOL_UPDATED",
    "TARGETING_POLICY_CREATED",
    "CANDIDATE_POOL_RUN_REQUESTED",
    "CANDIDATE_POOL_RUN_COMPLETED",
    "CANDIDATE_POOL_RUN_FAILED",
    "CAMPAIGN_CREATED",
    "CAMPAIGN_UPDATED",
    "CAMPAIGN_MEMBERS_ADDED",
    "CAMPAIGN_MEMBERS_REMOVED",
    "OUTREACH_TARGET_CREATED",
    "OUTREACH_TARGET_UPDATED",
    "OUTREACH_TASK_CREATED",
    "OUTREACH_TASK_TRANSITIONED",
)

BULK_ADD_RESULT_PAYLOAD_KEYS = (
    "campaign_id",
    "source_pool_run_id",
    "requested_count",
    "added_count",
    "restored_count",
    "already_active_count",
    "active_count_after",
)
UUID_PATTERN = "^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-" "[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"


def _payload_key_array(keys: tuple[str, ...]) -> str:
    return ", ".join(f"'{key}'" for key in keys)


def _bulk_add_result_payload_constraint(keys: str) -> str:
    """Return the fixed, redacted bulk-add replay shape for schema version 1."""

    return (
        "operation_scope <> 'CAMPAIGN_MEMBER_BULK_ADD'::phase3a_operation_scope OR "
        "result_schema_version <> 1 OR "
        "(result_payload ?& ARRAY["
        f"{keys}]::text[] AND (result_payload - ARRAY[{keys}]::text[]) = '{{}}'::jsonb AND "
        "jsonb_typeof(result_payload -> 'campaign_id') = 'string' AND "
        f"(result_payload ->> 'campaign_id') ~ '{UUID_PATTERN}' AND "
        "lower(result_payload ->> 'campaign_id') = result_entity_id::text AND "
        "(jsonb_typeof(result_payload -> 'source_pool_run_id') = 'null' OR "
        "(jsonb_typeof(result_payload -> 'source_pool_run_id') = 'string' AND "
        f"(result_payload ->> 'source_pool_run_id') ~ '{UUID_PATTERN}')) AND "
        "jsonb_typeof(result_payload -> 'requested_count') = 'number' AND "
        "(result_payload ->> 'requested_count') ~ '^[0-9]+$' AND "
        "jsonb_typeof(result_payload -> 'added_count') = 'number' AND "
        "(result_payload ->> 'added_count') ~ '^[0-9]+$' AND "
        "jsonb_typeof(result_payload -> 'restored_count') = 'number' AND "
        "(result_payload ->> 'restored_count') ~ '^[0-9]+$' AND "
        "jsonb_typeof(result_payload -> 'already_active_count') = 'number' AND "
        "(result_payload ->> 'already_active_count') ~ '^[0-9]+$' AND "
        "jsonb_typeof(result_payload -> 'active_count_after') = 'number' AND "
        "(result_payload ->> 'active_count_after') ~ '^[0-9]+$' AND "
        "((result_payload ->> 'added_count')::numeric + "
        "(result_payload ->> 'restored_count')::numeric + "
        "(result_payload ->> 'already_active_count')::numeric = "
        "(result_payload ->> 'requested_count')::numeric))"
    )


def _preflight_downgrade() -> None:
    has_records = bool(
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM phase3a_idempotency_records)"))
        .scalar_one()
    )
    if has_records:
        raise RuntimeError(
            "0007 downgrade blocked: durable Phase 3A idempotency records cannot be destroyed"
        )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for action in AUDIT_ACTIONS:
            op.execute(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{action}'")

    # Alembic's default version column is varchar(32), but this required revision
    # identifier is longer. Widen it before Alembic writes this revision ID.
    op.alter_column(
        "alembic_version",
        "version_num",
        type_=sa.String(length=64),
        existing_type=sa.String(length=32),
    )
    bind = op.get_bind()
    phase3a_operation_scope.create(bind, checkfirst=True)
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    bulk_add_result_payload_keys = _payload_key_array(BULK_ADD_RESULT_PAYLOAD_KEYS)

    op.create_table(
        "phase3a_idempotency_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column(
            "operation_scope",
            postgresql.ENUM(name="phase3a_operation_scope", create_type=False),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("result_entity_id", sa.Uuid(), nullable=False),
        sa.Column("result_schema_version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("result_payload", jsonb, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 255",
            name="ck_phase3a_idempotency_record_key_length",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_phase3a_idempotency_record_request_hash",
        ),
        sa.CheckConstraint(
            "result_schema_version >= 1",
            name="ck_phase3a_idempotency_record_result_schema_version",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_payload) = 'object'",
            name="ck_phase3a_idempotency_record_payload_object",
        ),
        sa.CheckConstraint(
            "octet_length(result_payload::text) <= 16384",
            name="ck_phase3a_idempotency_record_payload_size",
        ),
        sa.CheckConstraint(
            _bulk_add_result_payload_constraint(bulk_add_result_payload_keys),
            name="ck_phase3a_idempotency_record_bulk_add_payload",
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="fk_phase3a_idempotency_record_department",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "department_id",
            "operation_scope",
            "idempotency_key",
            name="uq_phase3a_idempotency_record_department_scope_key",
        ),
    )
    op.execute(
        """
        CREATE FUNCTION prevent_phase3a_idempotency_record_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'phase3a_idempotency_records is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_phase3a_idempotency_records_append_only BEFORE UPDATE OR DELETE "
        "ON phase3a_idempotency_records FOR EACH ROW "
        "EXECUTE FUNCTION prevent_phase3a_idempotency_record_mutation()"
    )


def downgrade() -> None:
    op.execute("LOCK TABLE phase3a_idempotency_records IN ACCESS EXCLUSIVE MODE")
    _preflight_downgrade()
    op.execute(
        "DROP TRIGGER trg_phase3a_idempotency_records_append_only " "ON phase3a_idempotency_records"
    )
    op.execute("DROP FUNCTION prevent_phase3a_idempotency_record_mutation()")
    op.drop_table("phase3a_idempotency_records")
    phase3a_operation_scope.drop(op.get_bind(), checkfirst=True)

    # The wider Alembic version column and shared Audit enum values are retained.

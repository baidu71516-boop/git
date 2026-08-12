"""Add the Phase 2 bulk-import aggregate and legacy single-file bridge.

Revision ID: 0004_phase2_bulk_import
Revises: 0003_phase1b
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_phase2_bulk_import"
down_revision: str | None = "0003_phase1b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_SCREENING_RULES = '{"schema_version":1,"platforms":[],"source_tags_exact_any":[]}'

import_job_file_status = postgresql.ENUM(
    "uploaded",
    "parsing",
    "mapping_required",
    "ready",
    "failed",
    "excluded",
    name="import_job_file_status",
)
source_acquired_at_origin = postgresql.ENUM(
    "server_default",
    "user_confirmed",
    "legacy_unknown",
    name="source_acquired_at_origin",
)
import_job_failed_stage = postgresql.ENUM("preview", "confirm", name="import_job_failed_stage")

AUDIT_ACTIONS = (
    "IMPORT_BATCH_CREATED",
    "IMPORT_BATCH_PREVIEW_CREATED",
    "IMPORT_BATCH_CONFIRM_REQUESTED",
    "IMPORT_BATCH_COMPLETED",
    "IMPORT_BATCH_FAILED",
    "IMPORT_BATCH_RETRIED",
    "IMPORT_BATCH_CANCELLED",
    "IMPORT_FILE_REPLACED",
    "IMPORT_FILE_EXCLUDED",
    "IMPORT_FILE_RETRIED",
    "IMPORT_FILE_MAPPING_UPDATED",
    "IMPORT_FILE_SOURCE_ACQUIRED_AT_UPDATED",
    "COLLECTION_SCREENING_RULES_UPDATED",
)

ACTIVE_LEGACY_STATUSES = (
    "uploaded",
    "parsing",
    "previewing",
    "confirm_queued",
    "importing",
)


def _count(statement: str) -> int:
    return int(op.get_bind().execute(sa.text(statement)).scalar_one())


def _preflight_upgrade() -> None:
    active = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM import_jobs "
                "WHERE status::text = ANY(CAST(:statuses AS text[]))"
            ),
            {"statuses": list(ACTIVE_LEGACY_STATUSES)},
        )
        .scalar_one()
    )
    if int(active) != 0:
        raise RuntimeError(
            "0004 upgrade blocked: active legacy import jobs must finish or be resolved"
        )


def _preflight_downgrade() -> None:
    non_default_rules = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM collection_jobs "
                "WHERE screening_rules <> CAST(:rules AS jsonb) "
                "OR screening_rules_revision <> 1"
            ),
            {"rules": DEFAULT_SCREENING_RULES},
        )
        .scalar_one()
    )
    if int(non_default_rules) != 0:
        raise RuntimeError(
            "0004 downgrade blocked: non-default screening rules cannot be represented in 0003"
        )

    if _count("SELECT count(*) FROM import_job_file_client_ids") != 0:
        raise RuntimeError(
            "0004 downgrade blocked: upload idempotency aliases cannot be represented in 0003"
        )

    unsafe_jobs = _count(
        """
        SELECT count(*)
        FROM import_jobs AS job
        WHERE job.status::text = 'draft'
           OR job.failed_stage IS NOT NULL
           OR job.stored_file_id IS NULL
           OR job.original_filename IS NULL
           OR job.mime_type IS NULL
           OR job.file_size IS NULL
           OR job.sha256 IS NULL
           OR (SELECT count(*) FROM import_job_files AS counted
               WHERE counted.import_job_id = job.id) <> 1
           OR NOT EXISTS (
                SELECT 1
                FROM import_job_files AS file
                JOIN stored_import_files AS stored ON stored.id = file.stored_file_id
                WHERE file.import_job_id = job.id
                  AND file.position = 1
                  AND file.stored_file_id = job.stored_file_id
                  AND stored.sha256 = job.sha256
                  AND stored.size = job.file_size
                  AND file.original_filename = job.original_filename
                  AND file.declared_mime IS NOT DISTINCT FROM job.mime_type
                  AND file.detected_fields IS NOT DISTINCT FROM job.detected_fields
                  AND file.field_mapping IS NOT DISTINCT FROM job.field_mapping
                  AND file.mapping_hash IS NOT DISTINCT FROM job.mapping_hash
                  AND file.raw_rows = job.total_rows
                  AND file.warning_rows = job.warning_rows
                  AND file.error_rows = job.error_rows
                  AND file.error_code IS NOT DISTINCT FROM job.error_code
                  AND file.error_message IS NOT DISTINCT FROM job.error_message
                  AND file.parse_task_id IS NOT DISTINCT FROM job.parse_task_id
                  AND file.parse_attempts = 0
                  AND file.parse_started_at IS NULL
                  AND file.parse_completed_at IS NULL
                  AND file.excluded_at IS NULL
                  AND file.source_acquired_at IS NULL
                  AND file.source_acquired_at_origin::text = 'legacy_unknown'
                  AND file.status::text = CASE
                        WHEN job.status::text IN (
                            'completed', 'preview_ready', 'preview_stale',
                            'confirm_queued', 'importing'
                        ) THEN 'ready'
                        WHEN job.status::text = 'mapping_required' THEN 'mapping_required'
                        WHEN job.status::text = 'failed' AND job.preview_revision > 0
                            THEN file.status::text
                        WHEN job.status::text = 'failed' THEN 'failed'
                        WHEN job.status::text = 'cancelled' THEN 'excluded'
                        WHEN job.status::text IN ('uploaded', 'parsing', 'previewing')
                            THEN CASE
                                WHEN job.status::text = 'uploaded' THEN 'uploaded'
                                ELSE 'parsing'
                            END
                        ELSE '__unsafe__'
                    END
                  AND NOT (
                      job.status::text = 'failed'
                      AND job.preview_revision > 0
                      AND file.status::text NOT IN ('ready', 'failed')
                  )
           )
        """
    )
    if unsafe_jobs != 0:
        raise RuntimeError("0004 downgrade blocked: bulk or non-projectable file occurrences exist")


def upgrade() -> None:
    _preflight_upgrade()

    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE import_job_status ADD VALUE IF NOT EXISTS 'draft'")
        for action in AUDIT_ACTIONS:
            op.execute(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{action}'")

    op.execute("LOCK TABLE collection_jobs, import_jobs, import_rows IN ACCESS EXCLUSIVE MODE")
    _preflight_upgrade()

    bind = op.get_bind()
    import_job_file_status.create(bind, checkfirst=True)
    source_acquired_at_origin.create(bind, checkfirst=True)
    import_job_failed_stage.create(bind, checkfirst=True)

    op.add_column(
        "collection_jobs",
        sa.Column(
            "screening_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text(
                '\'{"schema_version"\\:1,"platforms"\\:[],' '"source_tags_exact_any"\\:[]}\'::jsonb'
            ),
        ),
    )
    op.add_column(
        "collection_jobs",
        sa.Column(
            "screening_rules_revision",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.create_check_constraint(
        "ck_collection_job_screening_rules_revision",
        "collection_jobs",
        "screening_rules_revision >= 1",
    )

    op.add_column(
        "import_jobs",
        sa.Column(
            "failed_stage",
            postgresql.ENUM(name="import_job_failed_stage", create_type=False),
            nullable=True,
        ),
    )
    op.drop_constraint("ck_import_job_file_size", "import_jobs", type_="check")
    op.create_check_constraint(
        "ck_import_job_file_size",
        "import_jobs",
        "file_size IS NULL OR file_size > 0",
    )
    op.alter_column("import_jobs", "stored_file_id", existing_type=sa.Uuid(), nullable=True)
    op.alter_column(
        "import_jobs", "original_filename", existing_type=sa.String(length=255), nullable=True
    )
    op.alter_column("import_jobs", "mime_type", existing_type=sa.String(length=160), nullable=True)
    op.alter_column("import_jobs", "file_size", existing_type=sa.BigInteger(), nullable=True)
    op.alter_column("import_jobs", "sha256", existing_type=sa.String(length=64), nullable=True)

    op.create_table(
        "import_job_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("import_job_id", sa.Uuid(), nullable=False),
        sa.Column("stored_file_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("declared_mime", sa.String(length=160), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="import_job_file_status", create_type=False),
            nullable=False,
            server_default="uploaded",
        ),
        sa.Column("source_acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "source_acquired_at_origin",
            postgresql.ENUM(name="source_acquired_at_origin", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "source_acquired_at_confirmation_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("detected_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("field_mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("mapping_hash", sa.String(length=64), nullable=True),
        sa.Column("raw_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("parse_task_id", sa.String(length=64), nullable=True),
        sa.Column("parse_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parse_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parse_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("excluded_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("position >= 1", name="ck_import_job_file_position"),
        sa.CheckConstraint(
            "raw_rows >= 0 AND warning_rows >= 0 AND error_rows >= 0 " "AND parse_attempts >= 0",
            name="ck_import_job_file_counts_nonnegative",
        ),
        sa.CheckConstraint(
            "(source_acquired_at_origin = 'legacy_unknown' AND source_acquired_at IS NULL) "
            "OR (source_acquired_at_origin IN ('server_default', 'user_confirmed') "
            "AND source_acquired_at IS NOT NULL)",
            name="ck_import_job_file_acquisition_origin",
        ),
        sa.CheckConstraint(
            "NOT source_acquired_at_confirmation_required "
            "OR (source_acquired_at IS NOT NULL "
            "AND source_acquired_at_origin = 'server_default')",
            name="ck_import_job_file_acquisition_confirmation",
        ),
        sa.ForeignKeyConstraint(["import_job_id"], ["import_jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["stored_file_id"], ["stored_import_files.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("import_job_id", "position", name="uq_import_job_file_position"),
        sa.UniqueConstraint(
            "import_job_id", "stored_file_id", name="uq_import_job_file_stored_file"
        ),
        sa.UniqueConstraint("id", "import_job_id", name="uq_import_job_file_job_pair"),
    )

    op.execute(
        """
        INSERT INTO import_job_files (
            id, import_job_id, stored_file_id, position,
            original_filename, declared_mime, status,
            source_acquired_at, source_acquired_at_origin,
            source_acquired_at_confirmation_required,
            detected_fields, field_mapping, mapping_hash,
            raw_rows, warning_rows, error_rows, error_code, error_message,
            parse_task_id, parse_attempts, created_at, updated_at
        )
        SELECT
            md5('phase2-import-job-file:' || job.id::text)::uuid,
            job.id,
            job.stored_file_id,
            1,
            job.original_filename,
            job.mime_type,
            (CASE
                WHEN job.status::text IN ('completed', 'preview_ready', 'preview_stale')
                    THEN 'ready'
                WHEN job.status::text = 'mapping_required' THEN 'mapping_required'
                WHEN job.status::text = 'failed' THEN 'failed'
                WHEN job.status::text = 'cancelled' THEN 'excluded'
                ELSE '__invalid__'
            END)::import_job_file_status,
            NULL,
            'legacy_unknown'::source_acquired_at_origin,
            FALSE,
            job.detected_fields,
            job.field_mapping,
            job.mapping_hash,
            job.total_rows,
            job.warning_rows,
            job.error_rows,
            job.error_code,
            job.error_message,
            job.parse_task_id,
            0,
            job.created_at,
            job.updated_at
        FROM import_jobs AS job
        """
    )

    op.create_table(
        "import_job_file_client_ids",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("import_job_id", sa.Uuid(), nullable=False),
        sa.Column("import_job_file_id", sa.Uuid(), nullable=False),
        sa.Column("client_file_id", sa.String(length=160), nullable=False),
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
        sa.ForeignKeyConstraint(["import_job_id"], ["import_jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["import_job_file_id", "import_job_id"],
            ["import_job_files.id", "import_job_files.import_job_id"],
            name="fk_import_job_file_client_id_file_job",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "import_job_id",
            "client_file_id",
            name="uq_import_job_file_client_id_alias",
        ),
    )
    op.create_index(
        "ix_import_job_file_client_ids_file",
        "import_job_file_client_ids",
        ["import_job_file_id"],
    )

    if _count(
        """
        SELECT count(*) FROM import_jobs AS job
        WHERE (SELECT count(*) FROM import_job_files AS file
               WHERE file.import_job_id = job.id) <> 1
        """
    ):
        raise RuntimeError("0004 backfill failed: every legacy job must have one occurrence")

    op.add_column("import_rows", sa.Column("import_job_file_id", sa.Uuid(), nullable=True))
    op.execute(
        """
        UPDATE import_rows AS row
        SET import_job_file_id = file.id
        FROM import_job_files AS file
        WHERE file.import_job_id = row.import_job_id
        """
    )
    if _count(
        """
        SELECT count(*) FROM import_rows AS row
        LEFT JOIN import_job_files AS file
          ON file.id = row.import_job_file_id
         AND file.import_job_id = row.import_job_id
        WHERE row.import_job_file_id IS NULL OR file.id IS NULL
        """
    ):
        raise RuntimeError("0004 backfill failed: import row file lineage is incomplete")

    op.drop_constraint("uq_import_rows_job_number", "import_rows", type_="unique")
    op.create_unique_constraint(
        "uq_import_rows_file_number",
        "import_rows",
        ["import_job_file_id", "row_number"],
    )
    op.create_foreign_key(
        "fk_import_row_file_job",
        "import_rows",
        "import_job_files",
        ["import_job_file_id", "import_job_id"],
        ["id", "import_job_id"],
        ondelete="RESTRICT",
    )
    op.alter_column("import_rows", "import_job_file_id", existing_type=sa.Uuid(), nullable=False)
    op.create_index(
        "ix_import_rows_account_committed_job",
        "import_rows",
        ["matched_platform_account_id", sa.text("committed_at DESC"), "import_job_id"],
    )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE collection_jobs, import_jobs, import_job_files, "
        "import_job_file_client_ids, import_rows "
        "IN ACCESS EXCLUSIVE MODE"
    )
    _preflight_downgrade()

    op.drop_index("ix_import_rows_account_committed_job", table_name="import_rows")
    op.drop_constraint("fk_import_row_file_job", "import_rows", type_="foreignkey")
    op.drop_constraint("uq_import_rows_file_number", "import_rows", type_="unique")
    op.create_unique_constraint(
        "uq_import_rows_job_number", "import_rows", ["import_job_id", "row_number"]
    )
    op.drop_column("import_rows", "import_job_file_id")

    op.drop_index(
        "ix_import_job_file_client_ids_file",
        table_name="import_job_file_client_ids",
    )
    op.drop_table("import_job_file_client_ids")
    op.drop_table("import_job_files")

    op.drop_constraint(
        "ck_collection_job_screening_rules_revision", "collection_jobs", type_="check"
    )
    op.drop_column("collection_jobs", "screening_rules_revision")
    op.drop_column("collection_jobs", "screening_rules")

    op.drop_column("import_jobs", "failed_stage")
    op.drop_constraint("ck_import_job_file_size", "import_jobs", type_="check")
    op.create_check_constraint("ck_import_job_file_size", "import_jobs", "file_size > 0")
    op.alter_column("import_jobs", "sha256", existing_type=sa.String(length=64), nullable=False)
    op.alter_column("import_jobs", "file_size", existing_type=sa.BigInteger(), nullable=False)
    op.alter_column("import_jobs", "mime_type", existing_type=sa.String(length=160), nullable=False)
    op.alter_column(
        "import_jobs", "original_filename", existing_type=sa.String(length=255), nullable=False
    )
    op.alter_column("import_jobs", "stored_file_id", existing_type=sa.Uuid(), nullable=False)

    bind = op.get_bind()
    import_job_failed_stage.drop(bind, checkfirst=True)
    source_acquired_at_origin.drop(bind, checkfirst=True)
    import_job_file_status.drop(bind, checkfirst=True)

    # Values added to shared PostgreSQL enums are intentionally retained.
    # Rebuilding audit_action/import_job_status would risk historical rows.

"""Persist closed Permissions V1 grants after the frozen Douyin baseline.

Revision ID: 0010_permissions_v1_persistence
Revises: 0009_douyin_runtime_capture_v1
Create Date: 2026-09-01

The migration rejects any active Operator whose stored role would change the
legacy Department-role effective authority.  This deliberately strict
preflight is the explicit-approval boundary: it never copies, clamps, or
otherwise rewrites Operator roles to make a capability change appear safe.

Downgrade drops only the Task 2 grant table and leaves PostgreSQL audit enum
labels in place.  PostgreSQL cannot safely remove enum labels, and retaining
them neither changes nor deletes any 0009 Douyin identity, observation, or
capture data.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

import sqlalchemy as sa
from alembic import op

revision: str = "0010_permissions_v1_persistence"
down_revision: str | None = "0009_douyin_runtime_capture_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_RANK = {
    "viewer": 10,
    "operator": 20,
    "manager": 30,
    "super_admin": 40,
}
MODULE_KEYS = (
    "today_outreach",
    "campaigns",
    "candidate_pools",
    "influencer_library",
    "data_collection",
    "import_history",
    "data_updates",
    "admin",
)
NON_ADMIN_MODULE_KEYS = MODULE_KEYS[:-1]
AUDIT_ACTIONS = (
    "OPERATOR_CREATED",
    "OPERATOR_UPDATED",
    "OPERATOR_ROLE_CHANGED",
    "OPERATOR_STATUS_CHANGED",
    "OPERATOR_MODULE_GRANTS_CHANGED",
)


class PreflightReport(NamedTuple):
    """Count-verifiable authorization state observed before schema mutation."""

    active_non_super_admin_operators: int
    active_super_admin_operators: int
    disabled_operators: int


def _fail(message: str) -> None:
    raise RuntimeError(f"Permissions V1 preflight blocked: {message}")


def _preflight() -> PreflightReport:
    """Reject unsafe effective-role cutovers before irreversible DDL or enum work."""

    bind = op.get_bind()
    permission_rows = bind.execute(
        sa.text(
            """
            SELECT permission.department_id::text AS department_id,
                   permission.role::text AS role,
                   department.id IS NOT NULL AS department_exists
            FROM department_permissions AS permission
            LEFT JOIN departments AS department ON department.id = permission.department_id
            ORDER BY permission.department_id
            """
        )
    ).mappings()
    for row in permission_rows:
        if not row["department_exists"]:
            _fail(f"Department permission references missing Department {row['department_id']}")
        if row["role"] not in ROLE_RANK:
            _fail(
                f"Department permission for Department {row['department_id']} has unknown role "
                f"{row['role']!r}"
            )

    active_rows = bind.execute(
        sa.text(
            """
            SELECT operator.id::text AS operator_id,
                   operator.department_id::text AS department_id,
                   operator.role::text AS operator_role,
                   department.id IS NOT NULL AS department_exists,
                   department.status::text AS department_status,
                   COALESCE(permission.permission_count, 0) AS permission_count,
                   permission.ceiling_role AS ceiling_role
            FROM operators AS operator
            LEFT JOIN departments AS department ON department.id = operator.department_id
            LEFT JOIN (
                SELECT department_id,
                       count(*) AS permission_count,
                       max(role::text) AS ceiling_role
                FROM department_permissions
                GROUP BY department_id
            ) AS permission ON permission.department_id = operator.department_id
            WHERE operator.status = 'active'
            ORDER BY operator.department_id, operator.id
            """
        )
    ).mappings()

    active_non_super_admin = 0
    active_super_admin = 0
    for row in active_rows:
        operator_id = str(row["operator_id"])
        department_id = str(row["department_id"])
        operator_role = str(row["operator_role"])
        ceiling_role = row["ceiling_role"]
        if not row["department_exists"]:
            _fail(f"active Operator {operator_id} references missing Department {department_id}")
        if row["department_status"] != "active":
            _fail(f"active Operator {operator_id} belongs to inactive Department {department_id}")
        if int(row["permission_count"]) != 1:
            _fail(
                f"active Operator {operator_id} Department {department_id} has "
                f"{row['permission_count']} permission rows; expected exactly one"
            )
        if operator_role not in ROLE_RANK:
            _fail(f"active Operator {operator_id} has unknown role {operator_role!r}")
        if ceiling_role not in ROLE_RANK:
            _fail(
                f"active Operator {operator_id} Department {department_id} has unknown ceiling "
                f"{ceiling_role!r}"
            )
        if ROLE_RANK[operator_role] > ROLE_RANK[ceiling_role]:
            _fail(
                f"active Operator {operator_id} role {operator_role} exceeds Department "
                f"ceiling {ceiling_role}"
            )
        # Before this migration, the Department role was the effective role.  Any
        # unequal stored Operator role can change role-specific authority, even if
        # both roles happen to be writers in one current route family.
        if operator_role != ceiling_role:
            old_writes = ceiling_role != "viewer"
            new_writes = operator_role != "viewer"
            if old_writes and not new_writes:
                _fail(
                    f"active Operator {operator_id} would lose legacy write capability "
                    f"({ceiling_role} -> {operator_role})"
                )
            if not old_writes and new_writes:
                _fail(
                    f"active Operator {operator_id} would gain legacy write capability "
                    f"({ceiling_role} -> {operator_role})"
                )
            _fail(
                f"active Operator {operator_id} has an unapproved effective-role capability "
                f"change ({ceiling_role} -> {operator_role})"
            )
        if operator_role == "super_admin":
            active_super_admin += 1
        else:
            active_non_super_admin += 1

    disabled_operators = int(
        bind.execute(
            sa.text("SELECT count(*) FROM operators WHERE status = 'disabled'")
        ).scalar_one()
    )
    report = PreflightReport(
        active_non_super_admin_operators=active_non_super_admin,
        active_super_admin_operators=active_super_admin,
        disabled_operators=disabled_operators,
    )
    op.get_context().config.print_stdout(
        "Permissions V1 preflight PASS: "
        f"active_non_super_admin={report.active_non_super_admin_operators}, "
        f"active_super_admin={report.active_super_admin_operators}, "
        f"disabled={report.disabled_operators}"
    )
    return report


def _add_audit_actions() -> None:
    """Append-only PostgreSQL enum evolution, after preflight has passed."""

    with op.get_context().autocommit_block():
        for action in AUDIT_ACTIONS:
            op.execute(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{action}'")


def _create_grant_table() -> None:
    module_values = ", ".join(f"'{module_key}'" for module_key in MODULE_KEYS)
    op.create_table(
        "operator_module_permissions",
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("module_key", sa.String(length=18), nullable=False),
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
        sa.PrimaryKeyConstraint(
            "operator_id",
            "module_key",
            name="pk_operator_module_permissions",
        ),
        sa.ForeignKeyConstraint(
            ["operator_id", "department_id"],
            ["operators.id", "operators.department_id"],
            name="fk_operator_module_permissions_operator_department",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"module_key IN ({module_values})",
            name="ck_operator_module_permissions_module_key",
        ),
    )
    op.create_index(
        "ix_operator_module_permissions_department_operator",
        "operator_module_permissions",
        ["department_id", "operator_id"],
    )


def _backfill(report: PreflightReport) -> None:
    """Insert only positive, exact same-Department grants after a passing preflight."""

    bind = op.get_bind()
    module_values = ", ".join(f"('{module_key}')" for module_key in NON_ADMIN_MODULE_KEYS)
    bind.execute(
        sa.text(
            f"""
            INSERT INTO operator_module_permissions (operator_id, department_id, module_key)
            SELECT operator.id, operator.department_id, module.module_key
            FROM operators AS operator
            JOIN departments AS department ON department.id = operator.department_id
            JOIN department_permissions AS permission
                ON permission.department_id = operator.department_id
            CROSS JOIN (VALUES {module_values}) AS module(module_key)
            WHERE operator.status = 'active'
              AND operator.role <> 'super_admin'
            ON CONFLICT (operator_id, module_key) DO NOTHING
            """
        )
    )

    expected_grants = report.active_non_super_admin_operators * len(NON_ADMIN_MODULE_KEYS)
    actual_grants = int(
        bind.execute(sa.text("SELECT count(*) FROM operator_module_permissions")).scalar_one()
    )
    if actual_grants != expected_grants:
        raise RuntimeError(
            "Permissions V1 backfill verification failed: expected "
            f"{expected_grants} grants, found {actual_grants}"
        )
    wrong_counts = (
        bind.execute(
            sa.text(
                """
            SELECT operator.id::text
            FROM operators AS operator
            LEFT JOIN operator_module_permissions AS module_grant
                ON module_grant.operator_id = operator.id
                AND module_grant.department_id = operator.department_id
            WHERE operator.status = 'active'
              AND operator.role <> 'super_admin'
            GROUP BY operator.id
            HAVING count(module_grant.module_key) <> :expected_per_operator
            """
            ),
            {"expected_per_operator": len(NON_ADMIN_MODULE_KEYS)},
        )
        .scalars()
        .all()
    )
    if wrong_counts:
        raise RuntimeError(
            "Permissions V1 backfill verification failed: incomplete active non-Super-Admin "
            f"grants for {', '.join(str(operator_id) for operator_id in wrong_counts)}"
        )
    residual = int(
        bind.execute(
            sa.text(
                """
                SELECT count(*)
                FROM operator_module_permissions AS module_grant
                LEFT JOIN operators AS operator ON operator.id = module_grant.operator_id
                WHERE operator.id IS NULL
                   OR operator.department_id <> module_grant.department_id
                   OR operator.status = 'disabled'
                   OR operator.role = 'super_admin'
                   OR module_grant.module_key NOT IN (
                       'today_outreach', 'campaigns', 'candidate_pools',
                       'influencer_library', 'data_collection', 'import_history',
                       'data_updates', 'admin'
                   )
                """
            )
        ).scalar_one()
    )
    if residual:
        raise RuntimeError(
            "Permissions V1 backfill verification failed: cross-Department, disabled, "
            "Super-Admin, or unknown-module grant rows exist"
        )


def upgrade() -> None:
    report = _preflight()
    _add_audit_actions()
    _create_grant_table()
    _backfill(report)


def downgrade() -> None:
    # This is intentionally limited to the Task 2 table.  It does not touch
    # 0009 Douyin tables, enum labels, identities, observations, or captures.
    op.drop_index(
        "ix_operator_module_permissions_department_operator",
        table_name="operator_module_permissions",
    )
    op.drop_table("operator_module_permissions")
    # PostgreSQL audit_action labels are append-only.  Retaining the five labels
    # is fail-safe and keeps the restored 0009 schema valid.

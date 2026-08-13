"""Phase 2 bulk-domain ORM contract tests."""

from backend_core.db import models as database_models  # noqa: F401
from backend_core.imports.models import (
    CollectionJob,
    ImportJob,
    ImportJobFile,
    ImportJobFileClientId,
    ImportRow,
    ImportTaskRequest,
    StoredImportFile,
    default_screening_rules,
)
from backend_core.refresh.models import RefreshQueue, RefreshQueueItem
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.orm import configure_mappers


def _constraint_names(model: type[object], constraint_type: type[object]) -> set[str]:
    return {
        constraint.name
        for constraint in model.__table__.constraints  # type: ignore[attr-defined]
        if isinstance(constraint, constraint_type) and constraint.name is not None
    }


def test_import_job_file_occurrence_constraints_are_registered() -> None:
    configure_mappers()

    unique_names = _constraint_names(ImportJobFile, UniqueConstraint)
    assert unique_names == {
        "uq_import_job_file_job_pair",
        "uq_import_job_file_position",
        "uq_import_job_file_stored_file",
    }
    assert _constraint_names(ImportJobFile, CheckConstraint) == {
        "ck_import_job_file_acquisition_confirmation",
        "ck_import_job_file_acquisition_origin",
        "ck_import_job_file_counts_nonnegative",
        "ck_import_job_file_position",
    }

    confirmation_required = ImportJobFile.__table__.c.source_acquired_at_confirmation_required
    assert confirmation_required.nullable is False
    assert confirmation_required.default is not None
    assert confirmation_required.default.arg is False
    assert confirmation_required.server_default is not None
    assert str(confirmation_required.server_default.arg).lower() == "false"

    foreign_keys = {
        foreign_key.target_fullname for foreign_key in ImportJobFile.__table__.foreign_keys
    }
    assert foreign_keys == {"import_jobs.id", "stored_import_files.id"}
    assert ImportJob.files.property.order_by is not False
    assert ImportJobFile.import_job.property.back_populates == "files"
    assert ImportJobFile.stored_file.property.back_populates == "import_job_files"
    assert StoredImportFile.import_job_files.property.back_populates == "stored_file"


def test_import_job_file_client_id_alias_constraints_are_authoritative() -> None:
    configure_mappers()

    alias_unique_constraints = [
        constraint
        for constraint in ImportJobFileClientId.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert [constraint.name for constraint in alias_unique_constraints] == [
        "uq_import_job_file_client_id_alias"
    ]
    assert tuple(column.name for column in alias_unique_constraints[0].columns) == (
        "import_job_id",
        "client_file_id",
    )
    alias_file_job_foreign_key = next(
        constraint
        for constraint in ImportJobFileClientId.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_import_job_file_client_id_file_job"
    )
    assert tuple(element.parent.name for element in alias_file_job_foreign_key.elements) == (
        "import_job_file_id",
        "import_job_id",
    )
    assert tuple(element.target_fullname for element in alias_file_job_foreign_key.elements) == (
        "import_job_files.id",
        "import_job_files.import_job_id",
    )
    assert ImportJobFileClientId.__table__.c.import_job_id.nullable is False
    assert ImportJobFileClientId.__table__.c.import_job_file_id.nullable is False
    assert ImportJobFileClientId.__table__.c.client_file_id.nullable is False
    assert ImportJobFile.client_ids.property.back_populates == "import_job_file"
    assert ImportJobFileClientId.import_job_file.property.back_populates == "client_ids"


def test_import_row_locator_and_same_job_composite_fk_are_file_scoped() -> None:
    unique_names = _constraint_names(ImportRow, UniqueConstraint)
    assert "uq_import_rows_file_number" in unique_names
    assert "uq_import_rows_job_number" not in unique_names

    file_job_foreign_key = next(
        constraint
        for constraint in ImportRow.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_import_row_file_job"
    )
    assert tuple(element.parent.name for element in file_job_foreign_key.elements) == (
        "import_job_file_id",
        "import_job_id",
    )
    assert tuple(element.target_fullname for element in file_job_foreign_key.elements) == (
        "import_job_files.id",
        "import_job_files.import_job_id",
    )
    assert ImportRow.__table__.c.import_job_file_id.nullable is False
    assert ImportRow.import_job_file.property.back_populates == "rows"


def test_import_task_request_constraints_define_durable_task_identity() -> None:
    configure_mappers()

    assert _constraint_names(ImportTaskRequest, UniqueConstraint) == {
        "uq_import_task_request_token"
    }
    assert _constraint_names(ImportTaskRequest, CheckConstraint) == {
        "ck_import_task_request_attempts_nonnegative",
        "ck_import_task_request_state_timestamps",
        "ck_import_task_request_target",
    }
    file_job_foreign_key = next(
        constraint
        for constraint in ImportTaskRequest.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_import_task_request_file_job"
    )
    assert tuple(element.parent.name for element in file_job_foreign_key.elements) == (
        "import_job_file_id",
        "import_job_id",
    )
    assert tuple(element.target_fullname for element in file_job_foreign_key.elements) == (
        "import_job_files.id",
        "import_job_files.import_job_id",
    )

    indexes = {index.name: index for index in ImportTaskRequest.__table__.indexes}
    assert set(indexes) == {
        "ix_import_task_requests_due",
        "ix_import_task_requests_expired_lease",
        "uq_import_task_request_active_confirm",
        "uq_import_task_request_active_file_parse",
        "uq_import_task_request_active_legacy_parse",
        "uq_import_task_request_active_preview",
    }
    assert all(
        indexes[name].unique
        for name in (
            "uq_import_task_request_active_confirm",
            "uq_import_task_request_active_file_parse",
            "uq_import_task_request_active_legacy_parse",
            "uq_import_task_request_active_preview",
        )
    )
    assert indexes["ix_import_task_requests_due"].unique is False
    assert indexes["ix_import_task_requests_expired_lease"].unique is False
    assert tuple(
        column.name for column in indexes["uq_import_task_request_active_legacy_parse"].columns
    ) == ("import_job_id",)
    assert tuple(
        column.name for column in indexes["uq_import_task_request_active_file_parse"].columns
    ) == ("import_job_id", "import_job_file_id")
    assert tuple(
        column.name for column in indexes["uq_import_task_request_active_preview"].columns
    ) == ("import_job_id",)
    assert tuple(
        column.name for column in indexes["uq_import_task_request_active_confirm"].columns
    ) == ("import_job_id", "preview_revision")
    assert tuple(
        column.name for column in indexes["ix_import_task_requests_expired_lease"].columns
    ) == ("lease_expires_at", "id")

    table = ImportTaskRequest.__table__
    assert table.c.task_token.nullable is False
    assert table.c.import_job_id.nullable is False
    assert table.c.import_job_file_id.nullable is True
    assert table.c.preview_revision.nullable is True
    assert table.c.dispatch_attempts.server_default is not None
    assert table.c.run_attempts.server_default is not None
    assert table.c.requested_at.server_default is not None


def test_bulk_schema_defaults_and_required_index_are_frozen() -> None:
    first = default_screening_rules()
    second = default_screening_rules()
    first["platforms"].append("xiaohongshu")

    assert second == {
        "schema_version": 1,
        "platforms": [],
        "source_tags_exact_any": [],
    }
    assert CollectionJob.__table__.c.screening_rules.nullable is False
    assert CollectionJob.__table__.c.screening_rules_revision.nullable is False
    assert ImportJob.__table__.c.stored_file_id.nullable is True
    assert ImportJob.__table__.c.failed_stage.nullable is True

    indexes = {index.name: index for index in ImportRow.__table__.indexes}
    freshness_index = indexes["ix_import_rows_account_committed_job"]
    assert freshness_index.expressions[0].name == "matched_platform_account_id"
    assert str(freshness_index.expressions[1]) == "import_rows.committed_at DESC"
    assert freshness_index.expressions[2].name == "import_job_id"


def test_refresh_queue_schema_constraints_are_registered() -> None:
    configure_mappers()

    assert _constraint_names(RefreshQueue, UniqueConstraint) == {"uq_refresh_queue_department_pair"}
    assert _constraint_names(RefreshQueue, CheckConstraint) == {
        "ck_refresh_queue_limit_order",
        "ck_refresh_queue_limits_positive",
        "ck_refresh_queue_policy_version",
        "ck_refresh_queue_requested_limit_max",
        "ck_refresh_queue_status_timestamps",
    }
    assert _constraint_names(RefreshQueueItem, UniqueConstraint) == {
        "uq_refresh_queue_item_queue_account_source"
    }
    assert _constraint_names(RefreshQueueItem, CheckConstraint) == {
        "ck_refresh_queue_item_fulfillment_pair",
        "ck_refresh_queue_item_fulfillment_state",
        "ck_refresh_queue_item_last_return_pair",
        "ck_refresh_queue_item_priority_tier",
        "ck_refresh_queue_item_source_huitun",
    }

    foreign_keys = {
        constraint.name: tuple(element.target_fullname for element in constraint.elements)
        for constraint in RefreshQueueItem.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint) and constraint.name is not None
    }
    assert foreign_keys["fk_refresh_queue_item_queue_department"] == (
        "refresh_queues.id",
        "refresh_queues.department_id",
    )
    assert foreign_keys["fk_refresh_queue_item_account_influencer"] == (
        "influencer_platform_accounts.id",
        "influencer_platform_accounts.influencer_id",
    )
    assert foreign_keys["fk_refresh_queue_item_fulfilled_import"] == (
        "import_rows.id",
        "import_rows.import_job_id",
    )
    assert foreign_keys["fk_refresh_queue_item_last_return_import"] == (
        "import_rows.id",
        "import_rows.import_job_id",
    )

    indexes = {index.name: index for index in RefreshQueueItem.__table__.indexes}
    assert indexes["uq_refresh_queue_item_active_candidate"].unique is True
    assert tuple(
        column.name for column in indexes["uq_refresh_queue_item_active_candidate"].columns
    ) == ("department_id", "platform_account_id", "source")
    assert RefreshQueue.items.property.back_populates == "queue"
    assert RefreshQueueItem.queue.property.back_populates == "items"


def test_import_job_refresh_queue_link_is_nullable_and_department_scoped() -> None:
    assert ImportJob.__table__.c.refresh_queue_id.nullable is True
    foreign_key = next(
        constraint
        for constraint in ImportJob.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_import_job_refresh_queue_department"
    )
    assert tuple(element.parent.name for element in foreign_key.elements) == (
        "refresh_queue_id",
        "department_id",
    )
    assert tuple(element.target_fullname for element in foreign_key.elements) == (
        "refresh_queues.id",
        "refresh_queues.department_id",
    )

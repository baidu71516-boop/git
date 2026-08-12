"""Phase 2 bulk-domain ORM contract tests."""

from backend_core.imports.models import (
    CollectionJob,
    ImportJob,
    ImportJobFile,
    ImportJobFileClientId,
    ImportRow,
    StoredImportFile,
    default_screening_rules,
)
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

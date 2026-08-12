import json
from datetime import UTC, datetime, timedelta
from random import Random
from typing import Any
from uuid import UUID, uuid5

import pytest
from backend_core.imports.batch import (
    BatchDedupSummary,
    BatchRow,
    ComponentResolutionKind,
    DatabaseIdentityTarget,
    ManualReviewReason,
    RowLocator,
    build_identity_graph,
    duplicate_email_values,
    hard_identity_keys,
    resolve_identity_graph,
    summarize_resolutions,
)
from backend_core.imports.contracts import (
    CanonicalContact,
    CanonicalInfluencerRecord,
    PlatformIdentity,
)
from backend_core.influencers.enums import (
    ContactType,
    ContactValidationStatus,
    DataSource,
    Platform,
)

NAMESPACE = UUID("e73c8023-71c5-4f26-92bd-5ca6fdb9bac0")
BASE_TIME = datetime(2026, 8, 12, 4, 0, tzinfo=UTC)


def stable_uuid(value: str) -> UUID:
    return uuid5(NAMESPACE, value)


def make_record(
    *,
    account_id: str | None = None,
    external_id: str | None = None,
    profile_url: str | None = None,
    display_name: str | None = "达人",
    handle: str | None = None,
    source_updated_at: datetime | None = BASE_TIME,
    public_profile: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    email: str | None = None,
) -> CanonicalInfluencerRecord:
    contacts = (
        (
            CanonicalContact(
                type=ContactType.EMAIL,
                value=email,
                normalized_value=email.casefold(),
                validation_status=ContactValidationStatus.VALID,
            ),
        )
        if email is not None
        else ()
    )
    return CanonicalInfluencerRecord(
        display_name=display_name,
        platform_identity=PlatformIdentity(
            platform=Platform.XIAOHONGSHU,
            platform_account_id=account_id,
            account_handle=handle,
            profile_url=profile_url,
            normalized_profile_url=profile_url,
            external_source_id=external_id,
        ),
        source=DataSource.HUITUN,
        source_updated_at=source_updated_at,
        public_profile=public_profile or {},
        metrics=metrics or {},
        contacts=contacts,
    )


def make_row(
    *,
    file_position: int,
    row_number: int,
    record: CanonicalInfluencerRecord,
    file_name: str | None = None,
) -> BatchRow:
    occurrence_name = file_name or f"file-{file_position}"
    return BatchRow(
        locator=RowLocator(
            import_job_file_id=stable_uuid(occurrence_name),
            file_position=file_position,
            row_number=row_number,
            import_row_id=stable_uuid(f"row:{occurrence_name}:{row_number}"),
        ),
        record=record,
        normalized_data=record.as_dict(),
    )


def test_row_locator_is_file_aware_and_orders_by_position_then_physical_row() -> None:
    later_file = make_row(
        file_position=2,
        row_number=2,
        record=make_record(account_id="later"),
    ).locator
    later_row = make_row(
        file_position=1,
        row_number=3,
        record=make_record(account_id="later-row"),
    ).locator
    first = make_row(
        file_position=1,
        row_number=2,
        record=make_record(account_id="first"),
    ).locator

    assert [item.identity for item in sorted((later_file, later_row, first))] == [
        (first.import_job_file_id, 2),
        (later_row.import_job_file_id, 3),
        (later_file.import_job_file_id, 2),
    ]
    assert first.as_dict() == {
        "import_job_file_id": str(first.import_job_file_id),
        "file_position": 1,
        "row_number": 2,
        "import_row_id": str(first.import_row_id),
    }


@pytest.mark.parametrize(
    ("position", "row_number"),
    [(0, 2), (1, 1)],
)
def test_row_locator_rejects_values_outside_database_contract(
    position: int, row_number: int
) -> None:
    with pytest.raises(ValueError):
        RowLocator(
            import_job_file_id=stable_uuid("invalid"),
            file_position=position,
            row_number=row_number,
            import_row_id=stable_uuid("invalid-row"),
        )


def test_hard_identity_extraction_uses_all_three_frozen_keys_and_no_soft_fields() -> None:
    record = make_record(
        account_id="account-1",
        external_id="external-1",
        profile_url="https://www.douyin.com/user/normalized-1",
        display_name="相同昵称不能合并",
        handle="same-handle-is-not-hard",
        email="private@example.invalid",
    )

    keys = hard_identity_keys(record)

    assert keys == (
        "platform:xiaohongshu:account:account-1",
        "source:huitun:platform:xiaohongshu:external:external-1",
        "platform:xiaohongshu:profile:https://www.douyin.com/user/normalized-1",
    )
    serialized = json.dumps(keys)
    assert "private@example.invalid" not in serialized
    assert "相同昵称不能合并" not in serialized
    assert "same-handle-is-not-hard" not in serialized


def test_all_key_graph_builds_transitive_components_across_files() -> None:
    rows = (
        make_row(
            file_position=1,
            row_number=2,
            record=make_record(account_id="account-a"),
        ),
        make_row(
            file_position=2,
            row_number=8,
            record=make_record(
                account_id="account-a",
                profile_url="https://www.douyin.com/user/bridge",
            ),
        ),
        make_row(
            file_position=3,
            row_number=4,
            record=make_record(profile_url="https://www.douyin.com/user/bridge"),
        ),
        make_row(
            file_position=4,
            row_number=2,
            record=make_record(account_id="unrelated"),
        ),
    )

    graph = build_identity_graph(rows)

    assert [len(component.rows) for component in graph.components] == [3, 1]
    assert [row.locator.position for row in graph.components[0].rows] == [1, 2, 3]
    assert graph.stats.row_count == 4
    assert graph.stats.identity_links == 5
    assert graph.stats.union_attempts == 2


def test_same_email_nickname_and_handle_never_connect_different_accounts() -> None:
    rows = tuple(
        make_row(
            file_position=index,
            row_number=2,
            record=make_record(
                account_id=f"account-{index}",
                display_name="同名",
                handle="same-handle",
                email="same@example.invalid",
            ),
        )
        for index in range(1, 4)
    )

    graph = build_identity_graph(rows)

    assert len(graph.components) == 3
    assert all(len(component.rows) == 1 for component in graph.components)
    assert duplicate_email_values(rows, graph.components) == {"same@example.invalid"}


def test_email_repeated_only_inside_one_hard_identity_component_is_not_flagged() -> None:
    rows = tuple(
        make_row(
            file_position=index,
            row_number=2,
            record=make_record(account_id="same", email="same@example.invalid"),
        )
        for index in range(1, 3)
    )
    graph = build_identity_graph(rows)

    assert duplicate_email_values(rows, graph.components) == set()


def test_component_and_group_order_are_stable_when_input_order_changes() -> None:
    rows = [
        make_row(
            file_position=index,
            row_number=2,
            record=make_record(account_id=f"account-{index // 2}"),
        )
        for index in range(1, 7)
    ]
    expected = build_identity_graph(rows)
    Random(20260812).shuffle(rows)

    actual = build_identity_graph(rows)

    assert actual == expected


def test_duplicate_locator_is_rejected_even_if_file_position_metadata_differs() -> None:
    occurrence_id = stable_uuid("same-occurrence")
    rows = (
        BatchRow(
            locator=RowLocator(occurrence_id, 1, 2, stable_uuid("same-row-a")),
            record=make_record(account_id="a"),
            normalized_data=make_record(account_id="a").as_dict(),
        ),
        BatchRow(
            locator=RowLocator(occurrence_id, 2, 2, stable_uuid("same-row-b")),
            record=make_record(account_id="b"),
            normalized_data=make_record(account_id="b").as_dict(),
        ),
    )

    with pytest.raises(ValueError, match=r"occurrence \+ row_number"):
        build_identity_graph(rows)


def test_identical_payload_selects_earliest_locator_as_owner() -> None:
    record = make_record(account_id="same")
    rows = (
        make_row(file_position=2, row_number=2, record=record),
        make_row(file_position=1, row_number=9, record=record),
        make_row(file_position=1, row_number=3, record=record),
    )

    resolution = resolve_identity_graph(build_identity_graph(rows))[0]

    assert resolution.kind == ComponentResolutionKind.OWNER
    assert resolution.owner is not None
    assert resolution.owner.locator.position == 1
    assert resolution.owner.locator.row_number == 3
    assert [(row.locator.position, row.locator.row_number) for row in resolution.duplicates] == [
        (1, 9),
        (2, 2),
    ]


def test_reliably_newer_source_record_wins_without_field_splicing() -> None:
    old = make_row(
        file_position=1,
        row_number=2,
        record=make_record(
            account_id="same",
            display_name="旧名称",
            source_updated_at=BASE_TIME,
            metrics={"followers_count": 10},
        ),
    )
    newer = make_row(
        file_position=2,
        row_number=2,
        record=make_record(
            account_id="same",
            display_name="新名称",
            source_updated_at=BASE_TIME + timedelta(hours=1),
            metrics={"followers_count": 20},
        ),
    )

    resolution = resolve_identity_graph(build_identity_graph((old, newer)))[0]

    assert resolution.kind == ComponentResolutionKind.OWNER
    assert resolution.owner == newer
    assert resolution.owner.record.display_name == "新名称"
    assert resolution.owner.record.metrics == {"followers_count": 20}


@pytest.mark.parametrize(
    "source_times",
    [
        (BASE_TIME, BASE_TIME),
        (None, None),
        (None, BASE_TIME),
    ],
    ids=["same-time", "unknown-time", "partially-unknown-time"],
)
def test_conflicting_same_or_unknown_time_marks_whole_component_manual_review(
    source_times: tuple[datetime | None, datetime | None],
) -> None:
    rows = tuple(
        make_row(
            file_position=index,
            row_number=2,
            record=make_record(
                account_id="same",
                display_name=f"冲突-{index}",
                source_updated_at=source_time,
            ),
        )
        for index, source_time in enumerate(source_times, start=1)
    )

    resolution = resolve_identity_graph(build_identity_graph(rows))[0]

    assert resolution.kind == ComponentResolutionKind.MANUAL_REVIEW
    assert resolution.owner is None
    assert resolution.duplicates == ()
    assert resolution.manual_review_reason == ManualReviewReason.MERGE_PAYLOAD_CONFLICT
    assert resolution.conflict_fields == ("display_name",)


def test_tied_newest_conflict_marks_whole_component_manual_review() -> None:
    rows = (
        make_row(
            file_position=1,
            row_number=2,
            record=make_record(
                account_id="same",
                display_name="older",
                source_updated_at=BASE_TIME,
            ),
        ),
        make_row(
            file_position=2,
            row_number=2,
            record=make_record(
                account_id="same",
                display_name="new-a",
                source_updated_at=BASE_TIME + timedelta(hours=1),
            ),
        ),
        make_row(
            file_position=3,
            row_number=2,
            record=make_record(
                account_id="same",
                display_name="new-b",
                source_updated_at=BASE_TIME + timedelta(hours=1),
            ),
        ),
    )

    resolution = resolve_identity_graph(build_identity_graph(rows))[0]

    assert resolution.kind == ComponentResolutionKind.MANUAL_REVIEW
    assert resolution.conflict_fields == ("display_name",)


def test_contact_conflict_affects_owner_but_manual_evidence_never_contains_values() -> None:
    rows = (
        make_row(
            file_position=1,
            row_number=2,
            record=make_record(account_id="same", email="first@example.invalid"),
        ),
        make_row(
            file_position=2,
            row_number=2,
            record=make_record(account_id="same", email="second@example.invalid"),
        ),
    )

    resolution = resolve_identity_graph(build_identity_graph(rows))[0]
    evidence = resolution.manual_review_merge_plan()

    assert resolution.kind == ComponentResolutionKind.MANUAL_REVIEW
    assert resolution.conflict_fields == ("contacts",)
    assert evidence is not None
    serialized = json.dumps(evidence, sort_keys=True)
    assert "first@example.invalid" not in serialized
    assert "second@example.invalid" not in serialized


def test_missing_values_are_not_field_conflicts_and_never_cause_field_splicing() -> None:
    earliest = make_row(
        file_position=1,
        row_number=2,
        record=make_record(account_id="same", display_name=None, source_updated_at=None),
    )
    later = make_row(
        file_position=2,
        row_number=2,
        record=make_record(account_id="same", display_name="later", source_updated_at=None),
    )

    resolution = resolve_identity_graph(build_identity_graph((earliest, later)))[0]

    assert resolution.kind == ComponentResolutionKind.OWNER
    assert resolution.owner == earliest
    assert resolution.owner.record.display_name is None


def test_database_hard_keys_resolving_to_different_accounts_make_entire_group_manual() -> None:
    record = make_record(
        account_id="account-key",
        profile_url="https://www.douyin.com/user/profile-key",
        email="never-in-evidence@example.invalid",
    )
    rows = (
        make_row(file_position=1, row_number=2, record=record),
        make_row(file_position=2, row_number=3, record=record),
    )
    graph = build_identity_graph(rows)
    account_key, profile_key = hard_identity_keys(record)
    database_matches = {
        account_key: (
            DatabaseIdentityTarget(stable_uuid("account-a"), stable_uuid("influencer-a")),
        ),
        profile_key: (
            DatabaseIdentityTarget(stable_uuid("account-b"), stable_uuid("influencer-b")),
        ),
    }

    resolution = resolve_identity_graph(graph, database_matches=database_matches)[0]
    evidence = resolution.manual_review_merge_plan()

    assert resolution.kind == ComponentResolutionKind.MANUAL_REVIEW
    assert resolution.manual_review_reason == ManualReviewReason.DATABASE_IDENTITY_CONFLICT
    assert resolution.owner is None
    assert resolution.duplicates == ()
    assert evidence is not None
    serialized = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    assert str(stable_uuid("account-a")) in serialized
    assert str(stable_uuid("account-b")) in serialized
    assert "never-in-evidence@example.invalid" not in serialized
    assert "contacts" not in serialized


def test_duplicate_annotation_has_stable_owner_provenance_and_no_contact_values() -> None:
    record = make_record(account_id="same", email="private@example.invalid")
    rows = (
        make_row(file_position=1, row_number=2, record=record),
        make_row(file_position=2, row_number=7, record=record),
    )
    resolution = resolve_identity_graph(build_identity_graph(rows))[0]

    annotation = resolution.duplicate_annotations()[0]
    merge_plan = annotation.merge_plan()

    assert annotation.warning()["code"] == "BATCH_DUPLICATE"
    assert merge_plan == {
        "batch_duplicate": {
            "group_id": resolution.component.group_id,
            "owner_import_job_file_id": str(rows[0].locator.import_job_file_id),
            "owner_file_position": 1,
            "owner_row_number": 2,
            "owner_import_row_id": str(rows[0].locator.import_row_id),
            "hard_identity_keys": ["platform:xiaohongshu:account:same"],
        }
    }
    assert "private@example.invalid" not in json.dumps(merge_plan)


def test_summary_partitions_actions_and_keeps_warning_dimension_separate() -> None:
    duplicate_record = make_record(account_id="duplicate")
    manual_time = BASE_TIME
    rows = (
        make_row(file_position=1, row_number=2, record=duplicate_record),
        make_row(file_position=2, row_number=2, record=duplicate_record),
        make_row(
            file_position=3,
            row_number=2,
            record=make_record(
                account_id="manual",
                display_name="a",
                source_updated_at=manual_time,
            ),
        ),
        make_row(
            file_position=4,
            row_number=2,
            record=make_record(
                account_id="manual",
                display_name="b",
                source_updated_at=manual_time,
            ),
        ),
        make_row(
            file_position=5,
            row_number=2,
            record=make_record(account_id="unique"),
        ),
    )
    resolutions = resolve_identity_graph(build_identity_graph(rows))

    summary = summarize_resolutions(resolutions, parse_error_rows=2)

    assert summary == BatchDedupSummary(
        raw_rows=7,
        owner_rows=2,
        internal_duplicate_rows=1,
        unique_rows=6,
        manual_review_rows=2,
        parse_error_rows=2,
    )


def test_summary_rejects_broken_invariants() -> None:
    with pytest.raises(ValueError, match="unique_rows"):
        BatchDedupSummary(
            raw_rows=3,
            owner_rows=1,
            internal_duplicate_rows=1,
            unique_rows=3,
            manual_review_rows=0,
            parse_error_rows=1,
        )


@pytest.mark.parametrize(
    ("row_count", "component_size"),
    [(2_000, 2), (10_000, 5)],
)
def test_large_graph_connectivity_work_is_linear_in_identity_links(
    row_count: int, component_size: int
) -> None:
    rows = tuple(
        make_row(
            file_position=(index % 4) + 1,
            row_number=(index // 4) + 2,
            file_name=f"large-file-{(index % 4) + 1}",
            record=make_record(account_id=f"component-{index // component_size}"),
        )
        for index in range(row_count)
    )

    graph = build_identity_graph(rows)

    expected_components = row_count // component_size
    assert len(graph.components) == expected_components
    assert graph.stats == graph.stats.__class__(
        row_count=row_count,
        distinct_identity_keys=expected_components,
        identity_links=row_count,
        union_attempts=row_count - expected_components,
    )
    assert graph.stats.union_attempts <= graph.stats.identity_links
    assert sum(len(component.rows) for component in graph.components) == row_count

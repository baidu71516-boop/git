from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

import pytest
from backend_core.imports.contracts import (
    CanonicalInfluencerRecord,
    PlatformIdentity,
)
from backend_core.imports.enums import (
    ImportJobFileStatus,
    ImportMatchType,
    ImportRowAction,
    ImportSourceType,
    SourceAcquiredAtOrigin,
)
from backend_core.imports.preview_domain import (
    BatchPlanHashEntry,
    ChangeEffect,
    ChangeScope,
    ChangeSummary,
    ContactChange,
    ContactOperation,
    FieldChange,
    PreviewFileManifestEntry,
    PreviewRevisionContext,
    PreviewRowCategory,
    PreviewRowFact,
    PreviewRowHashInput,
    PreviewRowLocator,
    ScreeningResult,
    ScreeningRulePayload,
    ScreeningRuleSnapshot,
    build_change_summary,
    build_tag_change,
    change_summary_hash,
    evaluate_screening,
    has_effective_changes,
    hash_batch_plan,
    hash_preview_row,
    row_category_priority,
    row_matches_category,
    summarize_preview,
)
from backend_core.influencers.enums import (
    ContactType,
    ContactValidationStatus,
    DataSource,
    Platform,
)
from pydantic import ValidationError

NAMESPACE = UUID("67adf35d-df2c-4ac3-a632-df61dbb27ef5")
BASE_TIME = datetime(2026, 8, 12, 4, 0, tzinfo=UTC)


def stable_uuid(value: str) -> UUID:
    return uuid5(NAMESPACE, value)


def make_record(
    *,
    tags: object = ("Beauty美妆",),
    followers: object = 100,
    public_profile: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> CanonicalInfluencerRecord:
    profile = dict(public_profile or {})
    if tags is not _MISSING:
        profile["creator_tags"] = tags
    metric_values: dict[str, Any] = dict(metrics or {})
    if followers is not _MISSING and "followers_count" not in metric_values:
        metric_values["followers_count"] = followers
    return CanonicalInfluencerRecord(
        display_name="达人",
        platform_identity=PlatformIdentity(
            platform=Platform.XIAOHONGSHU,
            platform_account_id="account-1",
            account_handle=None,
            profile_url=None,
            normalized_profile_url=None,
            external_source_id="external-1",
        ),
        source=DataSource.HUITUN,
        source_updated_at=BASE_TIME,
        public_profile=profile,
        metrics=metric_values,
    )


_MISSING = object()


def make_screening_snapshot(
    *,
    platforms: tuple[Platform, ...] = (Platform.XIAOHONGSHU,),
    tags: tuple[str, ...] = ("Beauty美妆",),
    follower_min: int | None = 10,
    follower_max: int | None = 200,
    revision: int = 3,
) -> ScreeningRuleSnapshot:
    return ScreeningRuleSnapshot(
        rules=ScreeningRulePayload(
            platforms=platforms,
            source_tags_exact_any=tags,
        ),
        rule_revision=revision,
        follower_min=follower_min,
        follower_max=follower_max,
    )


def make_file(
    position: int,
    *,
    sha256: str | None = None,
    mapping_hash: str | None = None,
    acquired_at: datetime | None = None,
    status: ImportJobFileStatus = ImportJobFileStatus.READY,
    included: bool = True,
    confirmation_required: bool = False,
    origin: SourceAcquiredAtOrigin = SourceAcquiredAtOrigin.SERVER_DEFAULT,
    file_id: UUID | None = None,
) -> PreviewFileManifestEntry:
    return PreviewFileManifestEntry(
        import_job_file_id=file_id or stable_uuid(f"file-{position}"),
        position=position,
        stored_file_sha256=sha256 or (f"{position:x}" * 64)[:64],
        mapping_hash=mapping_hash or (f"{position + 2:x}" * 64)[:64],
        status=status,
        included=included,
        source_acquired_at=acquired_at or BASE_TIME,
        source_acquired_at_origin=origin,
        source_acquired_at_confirmation_required=confirmation_required,
    )


def make_context(
    *,
    files: tuple[PreviewFileManifestEntry, ...] | None = None,
    screening: ScreeningRuleSnapshot | None = None,
    import_job_id: UUID | None = None,
    collection_job_id: UUID | None = None,
    source_type: ImportSourceType = ImportSourceType.MANUAL_HUITUN_EXPORT,
    planner_version: str = "phase1b-v1",
    relevant_config: dict[str, object] | None = None,
) -> PreviewRevisionContext:
    return PreviewRevisionContext(
        import_job_id=import_job_id or stable_uuid("job"),
        preview_revision=1,
        collection_job_id=collection_job_id or stable_uuid("collection"),
        source_type=source_type,
        files=files or (make_file(1), make_file(2)),
        screening=screening or make_screening_snapshot(),
        planner_version=planner_version,
        relevant_config=relevant_config or {"max_batch_rows": 10_000, "enabled": False},
    )


def make_locator(position: int, row_number: int) -> PreviewRowLocator:
    return PreviewRowLocator(
        import_job_file_id=stable_uuid(f"file-{position}"),
        file_position=position,
        row_number=row_number,
        import_row_id=stable_uuid(f"row-{position}-{row_number}"),
    )


def make_row_hash_input(
    *,
    normalized_data: dict[str, object] | None = None,
    action: ImportRowAction = ImportRowAction.CREATE,
    warnings: tuple[dict[str, object], ...] = (),
    errors: tuple[dict[str, object], ...] = (),
    locator: PreviewRowLocator | None = None,
) -> PreviewRowHashInput:
    return PreviewRowHashInput(
        context_hash=make_context().context_hash,
        locator=locator or make_locator(1, 2),
        normalized_data=normalized_data or {"name": "达人"},
        action=action,
        match_type=ImportMatchType.NONE,
        matched_influencer_id=None,
        matched_platform_account_id=None,
        duplicate_owner_locator=None,
        merge_plan={"account": {"operation": "create"}},
        preconditions={},
        screening=None,
        change_summary=ChangeSummary(),
        warnings=warnings,
        errors=errors,
        manual_review=None,
    )


def test_screening_matches_all_configured_exact_rules_and_trims_tags() -> None:
    evaluation = evaluate_screening(
        make_record(tags=(" Other ", " Beauty美妆 "), followers=10),
        make_screening_snapshot(),
    )

    assert evaluation.result is ScreeningResult.MATCH
    assert [item.rule for item in evaluation.evidence] == [
        "platforms",
        "source_tags_exact_any",
        "followers",
    ]
    assert all(item.result is ScreeningResult.MATCH for item in evaluation.evidence)
    assert evaluation.rule_hash == make_screening_snapshot().rule_hash


def test_screening_not_match_takes_precedence_over_unknown() -> None:
    evaluation = evaluate_screening(
        make_record(tags=_MISSING, followers=9),
        make_screening_snapshot(),
    )

    assert evaluation.result is ScreeningResult.NOT_MATCH
    assert {item.rule: item.result for item in evaluation.evidence} == {
        "platforms": ScreeningResult.MATCH,
        "source_tags_exact_any": ScreeningResult.UNKNOWN,
        "followers": ScreeningResult.NOT_MATCH,
    }


@pytest.mark.parametrize("followers", [_MISSING, None, True, 1.0, "1", -1])
def test_screening_invalid_or_missing_followers_are_unknown(followers: object) -> None:
    evaluation = evaluate_screening(
        make_record(followers=followers),
        make_screening_snapshot(platforms=(), tags=(), follower_min=0, follower_max=None),
    )

    assert evaluation.result is ScreeningResult.UNKNOWN
    assert evaluation.evidence[0].reason == "FOLLOWERS_MISSING_OR_INVALID"


def test_screening_accepts_zero_as_real_follower_count() -> None:
    evaluation = evaluate_screening(
        make_record(followers=0),
        make_screening_snapshot(platforms=(), tags=(), follower_min=0, follower_max=0),
    )

    assert evaluation.result is ScreeningResult.MATCH
    assert evaluation.evidence[0].observed == 0


@pytest.mark.parametrize("tags", [_MISSING, None, (), [], ("   ",), [1]])
def test_screening_missing_or_invalid_source_tags_are_unknown(tags: object) -> None:
    evaluation = evaluate_screening(
        make_record(tags=tags),
        make_screening_snapshot(platforms=(), follower_min=None, follower_max=None),
    )

    assert evaluation.result is ScreeningResult.UNKNOWN
    assert evaluation.evidence[0].reason == "SOURCE_TAGS_MISSING_OR_INVALID"


def test_screening_tags_are_case_sensitive_exact_and_not_substring_matches() -> None:
    snapshot = make_screening_snapshot(
        platforms=(), tags=("Beauty美妆",), follower_min=None, follower_max=None
    )

    assert evaluate_screening(make_record(tags=("beauty美妆",)), snapshot).result is (
        ScreeningResult.NOT_MATCH
    )
    assert evaluate_screening(make_record(tags=("Beauty美",)), snapshot).result is (
        ScreeningResult.NOT_MATCH
    )


def test_empty_screening_rules_are_unknown_and_ignore_free_text() -> None:
    record = make_record(
        tags=_MISSING,
        followers=_MISSING,
        public_profile={
            "industry": "Beauty美妆",
            "subdirection": "Beauty美妆",
            "purpose": "Beauty美妆",
            "notes": "Beauty美妆",
        },
    )

    evaluation = evaluate_screening(
        record,
        make_screening_snapshot(platforms=(), tags=(), follower_min=None, follower_max=None),
    )

    assert evaluation.result is ScreeningResult.UNKNOWN
    assert evaluation.evidence == ()


def test_screening_rule_schema_trims_tags_and_rejects_empty_long_or_duplicates() -> None:
    assert ScreeningRulePayload(
        source_tags_exact_any=("  Beauty美妆  ",)
    ).source_tags_exact_any == ("Beauty美妆",)
    for tags in (("   ",), ("a" * 161,), ("Beauty美妆", " Beauty美妆 ")):
        with pytest.raises(ValidationError):
            ScreeningRulePayload(source_tags_exact_any=tags)


@pytest.mark.parametrize("value", [True, 1.0, "1", -1])
def test_screening_rule_follower_bounds_are_strict_nonnegative_integers(value: object) -> None:
    with pytest.raises(ValidationError):
        ScreeningRuleSnapshot(
            rules=ScreeningRulePayload(),
            rule_revision=1,
            follower_min=value,
        )


def test_screening_rule_snapshot_rejects_inverted_range() -> None:
    with pytest.raises(ValidationError, match="follower_min"):
        make_screening_snapshot(follower_min=2, follower_max=1)


def test_change_summary_preserves_json_scalars_and_canonicalizes_decimal() -> None:
    summary = ChangeSummary(
        effective_changes=(
            FieldChange(
                scope=ChangeScope.ACCOUNT,
                field="zero",
                before=None,
                incoming=0,
                after=0,
                effect=ChangeEffect.APPLY,
                reason="INCOMING_VALUE_APPLIED",
            ),
            FieldChange(
                scope=ChangeScope.CURRENT_METRICS,
                field="ratio",
                before=False,
                incoming=Decimal("98.1234567890"),
                after="",
                effect=ChangeEffect.APPLY,
                reason="NEWER_METRICS",
            ),
        ),
        ignored_changes=(
            FieldChange(
                scope=ChangeScope.ACCOUNT,
                field="display_name",
                before="manual",
                incoming="source",
                after="manual",
                effect=ChangeEffect.IGNORE,
                reason="MANUAL_VALUE_PROTECTED",
            ),
        ),
        freshness_changes=(
            FieldChange(
                scope=ChangeScope.FRESHNESS,
                field="source_acquired_at",
                before=None,
                incoming=BASE_TIME,
                after=BASE_TIME,
                effect=ChangeEffect.OBSERVE,
                reason="OBSERVATION_ADVANCED",
            ),
        ),
        historical_observations=(
            FieldChange(
                scope=ChangeScope.METRIC_SNAPSHOT,
                field="metrics",
                before=None,
                incoming={"followers_count": 1},
                after=None,
                effect=ChangeEffect.HISTORY,
                reason="SNAPSHOT_ONLY",
            ),
        ),
    )

    dumped = summary.model_dump(mode="json")
    assert dumped["effective_changes"][0]["before"] is None
    assert dumped["effective_changes"][0]["incoming"] == 0
    assert dumped["effective_changes"][1]["before"] is False
    assert dumped["effective_changes"][1]["incoming"] == "98.123456789"
    assert dumped["effective_changes"][1]["after"] == ""
    assert set(dumped) == {
        "effective_changes",
        "ignored_changes",
        "freshness_changes",
        "historical_observations",
    }


def test_tag_change_reports_exact_case_sensitive_sorted_added_removed() -> None:
    change = build_tag_change(
        scope=ChangeScope.SOURCE_STATE,
        field="source_tags",
        before=("Beauty美妆", "Old", "Case"),
        incoming=("Beauty美妆", "New", "case"),
        effect=ChangeEffect.APPLY,
        reason="SOURCE_TAGS_REPLACED",
    )

    assert change.added == ("New", "case")
    assert change.removed == ("Case", "Old")
    assert change.before is None
    assert change.incoming is None
    assert change.after is None


def test_contact_change_contract_has_no_contact_value_surface() -> None:
    change = ContactChange(
        contact_type=ContactType.EMAIL,
        operation=ContactOperation.CREATE,
        validation_status=ContactValidationStatus.VALID,
        count=2,
        possible_duplicate=True,
    )

    dumped = change.model_dump(mode="json")
    serialized = json.dumps(dumped)
    assert dumped == {
        "scope": "contact",
        "contact_type": "email",
        "operation": "create",
        "validation_status": "valid",
        "count": 2,
        "possible_duplicate": True,
    }
    assert "value" not in dumped
    assert "normalized" not in serialized


@pytest.mark.parametrize(
    "field",
    ["email", "normalized_value", "phone", "contact.value", "access_token", "secret"],
)
def test_general_field_changes_reject_sensitive_contact_or_secret_fields(field: str) -> None:
    with pytest.raises(ValidationError, match="sensitive"):
        FieldChange(
            scope=ChangeScope.ACCOUNT,
            field=field,
            before=None,
            incoming="private@example.invalid",
            after="private@example.invalid",
            effect=ChangeEffect.APPLY,
            reason="CONTACT_CHANGED",
        )


def test_change_summary_rejects_accidental_sensitive_values_recursively() -> None:
    with pytest.raises(ValidationError, match="sensitive"):
        FieldChange(
            scope=ChangeScope.ACCOUNT,
            field="profile",
            before=None,
            incoming={"nested": ["private@example.invalid"]},
            after=None,
            effect=ChangeEffect.APPLY,
            reason="PROFILE_CHANGED",
        )

    with pytest.raises(ValidationError, match="sensitive"):
        FieldChange(
            scope=ChangeScope.SOURCE_STATE,
            field="source_tags",
            effect=ChangeEffect.APPLY,
            reason="SOURCE_TAGS_REPLACED",
            added=("private@example.invalid",),
        )


def test_change_summary_is_deterministically_ordered_and_hashed() -> None:
    left = FieldChange(
        scope=ChangeScope.ACCOUNT,
        field="display_name",
        before="a",
        incoming="b",
        after="b",
        effect=ChangeEffect.APPLY,
        reason="APPLY",
    )
    right = FieldChange(
        scope=ChangeScope.CURRENT_METRICS,
        field="followers_count",
        before=1,
        incoming=2,
        after=2,
        effect=ChangeEffect.APPLY,
        reason="APPLY",
    )

    first = ChangeSummary(effective_changes=(right, left))
    second = ChangeSummary(effective_changes=(left, right))

    assert first.effective_changes == second.effective_changes == (left, right)
    assert change_summary_hash(first) == change_summary_hash(second)


def test_change_summary_rejects_contact_operations_in_the_wrong_bucket() -> None:
    contact = ContactChange(
        contact_type=ContactType.EMAIL,
        operation=ContactOperation.CREATE,
        validation_status=ContactValidationStatus.VALID,
        count=1,
    )
    with pytest.raises(ValidationError, match="contact operation"):
        ChangeSummary(ignored_changes=(contact,))


def test_change_summary_builder_uses_explicit_before_projections_and_redacts_contacts() -> None:
    record = make_record(
        tags=("Beauty美妆", "New"),
        followers=200,
        public_profile={"bio": "new bio", "creator_tags": ("Beauty美妆", "New")},
        metrics={"followers_count": 200, "ratio": 2},
    )
    merge_plan = {
        "account_create": None,
        "account_updates": {"bio": "new bio", "source_tags": ["Beauty美妆", "New"]},
        "source_state": {
            "operation": "update",
            "source_updated_at": BASE_TIME.isoformat(),
            "source_data": {
                "bio": "new bio",
                "creator_tags": ["Beauty美妆", "New"],
                "retained": "database",
            },
            "source_data_hash": "a" * 64,
        },
        "source_identity": None,
        "metrics": {
            "current": {
                "operation": "update",
                "source_updated_at": BASE_TIME.isoformat(),
                "metrics": {"followers_count": 200, "ratio": 1},
                "metrics_hash": "b" * 64,
            },
            "snapshot": {
                "snapshot_key": "c" * 64,
                "source_updated_at": BASE_TIME.isoformat(),
                "metrics": {"followers_count": 200, "ratio": 2},
                "metrics_hash": "d" * 64,
            },
        },
        "contacts": {
            "create": [
                {
                    "type": "email",
                    "value": "private@example.invalid",
                    "normalized_value": "private@example.invalid",
                    "validation_status": "valid",
                    "is_current": True,
                    "possible_duplicate_contact": True,
                }
            ],
            "deactivate_ids": [str(stable_uuid("old-contact"))],
            "mark_duplicate_ids": [],
            "observe_ids": [],
        },
    }
    preconditions = {
        "account": {"bio": "old bio", "source_tags": ["Beauty美妆", "Old"]},
        "source_state": {
            "source_updated_at": (BASE_TIME - timedelta(days=1)).isoformat(),
            "source_data_hash": "e" * 64,
        },
        "metrics": {
            "current": {
                "source_updated_at": (BASE_TIME - timedelta(days=1)).isoformat(),
                "metrics_hash": "f" * 64,
            },
            "snapshot_exists": False,
        },
        "contacts": [],
    }

    summary = build_change_summary(
        record,
        merge_plan,
        preconditions,
        source_acquired_at=BASE_TIME,
        previous_source_acquired_at=BASE_TIME - timedelta(days=2),
        existing_source_data={
            "bio": "old bio",
            "creator_tags": ["Beauty美妆", "Old"],
            "retained": "database",
        },
        existing_metrics={"followers_count": 100, "ratio": 1},
    )

    serialized = json.dumps(summary.model_dump(mode="json"), ensure_ascii=False)
    assert "private@example.invalid" not in serialized
    assert "normalized_value" not in serialized
    assert any(
        isinstance(item, ContactChange)
        and item.operation is ContactOperation.CREATE
        and item.possible_duplicate
        for item in summary.effective_changes
    )
    assert any(
        isinstance(item, FieldChange)
        and item.scope is ChangeScope.CURRENT_METRICS
        and item.field == "followers_count"
        and item.before == 100
        and item.incoming == 200
        and item.after == 200
        for item in summary.effective_changes
    )
    assert any(
        isinstance(item, FieldChange)
        and item.scope is ChangeScope.CURRENT_METRICS
        and item.field == "ratio"
        and item.before == 1
        and item.incoming == 2
        and item.after == 1
        for item in summary.ignored_changes
    )
    tag_changes = [
        item
        for item in summary.effective_changes
        if isinstance(item, FieldChange) and item.field in {"source_tags", "creator_tags"}
    ]
    assert tag_changes
    assert all(item.added == ("New",) and item.removed == ("Old",) for item in tag_changes)
    assert len(summary.freshness_changes) == 1
    assert any(
        isinstance(item, FieldChange) and item.scope is ChangeScope.METRIC_SNAPSHOT
        for item in summary.historical_observations
    )
    assert has_effective_changes(summary)


def test_change_summary_builder_keeps_snapshot_only_out_of_effective_changes() -> None:
    record = make_record(followers=100)
    summary = build_change_summary(
        record,
        {
            "account_create": None,
            "account_updates": {},
            "source_state": None,
            "source_identity": None,
            "metrics": {
                "current": None,
                "snapshot": {
                    "snapshot_key": "a" * 64,
                    "source_updated_at": BASE_TIME.isoformat(),
                    "metrics": {"followers_count": 100},
                    "metrics_hash": "b" * 64,
                },
            },
            "contacts": {
                "create": [],
                "deactivate_ids": [],
                "mark_duplicate_ids": [],
                "observe_ids": [],
            },
        },
        {"account": {}, "source_state": None, "metrics": {}, "contacts": []},
        source_acquired_at=BASE_TIME,
        previous_source_acquired_at=BASE_TIME,
        existing_source_data=None,
        existing_metrics={"followers_count": 100},
    )

    assert summary.effective_changes == ()
    assert summary.ignored_changes == ()
    assert len(summary.historical_observations) == 1
    assert not has_effective_changes(summary)


def test_change_summary_builder_never_fabricates_missing_before_projection() -> None:
    record = make_record(followers=200)
    summary = build_change_summary(
        record,
        {
            "account_create": None,
            "account_updates": {},
            "source_state": {
                "operation": "update",
                "source_updated_at": BASE_TIME.isoformat(),
                "source_data": {"bio": "new"},
                "source_data_hash": "a" * 64,
            },
            "source_identity": None,
            "metrics": {
                "current": {
                    "operation": "update",
                    "source_updated_at": BASE_TIME.isoformat(),
                    "metrics": {"followers_count": 200},
                    "metrics_hash": "b" * 64,
                },
                "snapshot": None,
            },
            "contacts": {},
        },
        {
            "account": {},
            "source_state": {"source_data_hash": "c" * 64},
            "metrics": {"current": {"metrics_hash": "d" * 64}},
            "contacts": [],
        },
        source_acquired_at=None,
        previous_source_acquired_at=None,
    )

    effective = [item for item in summary.effective_changes if isinstance(item, FieldChange)]
    assert {(item.scope, item.field) for item in effective} == {
        (ChangeScope.SOURCE_STATE, "source_data_hash"),
        (ChangeScope.CURRENT_METRICS, "metrics_hash"),
    }
    assert {item.before for item in effective} == {"c" * 64, "d" * 64}


def test_change_summary_builder_records_incoming_only_older_metric_as_ignored() -> None:
    summary = build_change_summary(
        make_record(metrics={"followers_count": 100, "new_metric": 0}),
        {
            "account_create": None,
            "account_updates": {},
            "source_state": None,
            "source_identity": None,
            "metrics": {"current": None, "snapshot": None},
            "contacts": {},
        },
        {"account": {}, "source_state": {}, "metrics": {}, "contacts": []},
        source_acquired_at=None,
        previous_source_acquired_at=None,
        existing_source_data={},
        existing_metrics={"followers_count": 100},
    )

    assert any(
        isinstance(item, FieldChange)
        and item.field == "new_metric"
        and item.before is None
        and item.incoming == 0
        and item.after is None
        for item in summary.ignored_changes
    )


def test_change_summary_builder_rejects_naive_acquisition_times() -> None:
    with pytest.raises(ValueError, match="timezone"):
        build_change_summary(
            make_record(),
            {},
            {},
            source_acquired_at=datetime(2026, 8, 12, 4, 0),
            previous_source_acquired_at=None,
        )


def test_change_summary_builder_redacts_contact_like_values_in_public_fields() -> None:
    secret = "请联系embedded-contact@example.invalid获取详情"
    phone_secret = "联系电话13800138000"
    plain_phone_secret = "13800138000"
    prefixed_phone_secrets = ("wx13800138000", "Tel13800138000")
    record = make_record(
        public_profile={
            "bio": secret,
            "creator_tags": (secret, phone_secret, *prefixed_phone_secrets),
        },
        metrics={"note": plain_phone_secret, "support": prefixed_phone_secrets[0]},
    )
    summary = build_change_summary(
        record,
        {
            "account_create": {"display_name": "Safe", "public_profile": record.public_profile},
            "account_updates": {},
            "source_state": {
                "operation": "create",
                "source_data": {
                    "bio": secret,
                    "creator_tags": [secret, phone_secret, *prefixed_phone_secrets],
                },
                "source_data_hash": "a" * 64,
            },
            "source_identity": {"external_account_id": secret},
            "metrics": {
                "current": None,
                "snapshot": {
                    "metrics": {
                        "note": plain_phone_secret,
                        "support": prefixed_phone_secrets[0],
                    }
                },
            },
            "contacts": {},
        },
        {"account": {}, "source_state": None, "metrics": {}, "contacts": []},
        source_acquired_at=None,
        previous_source_acquired_at=None,
        existing_source_data={},
        existing_metrics={},
    )

    serialized = json.dumps(summary.model_dump(mode="json"), ensure_ascii=False)
    assert secret not in serialized
    assert phone_secret not in serialized
    assert plain_phone_secret not in serialized
    assert not any(value in serialized for value in prefixed_phone_secrets)
    assert "[REDACTED]" in serialized


def make_fact(
    action: ImportRowAction,
    *,
    warning: bool = False,
    duplicate: bool = False,
    possible_duplicate: bool = False,
    screening: ScreeningResult | None = ScreeningResult.MATCH,
) -> PreviewRowFact:
    return PreviewRowFact(
        action=action,
        has_warning=warning,
        is_batch_duplicate=duplicate,
        possible_duplicate_contact=possible_duplicate,
        screening_result=screening,
    )


def test_unified_summary_computes_partition_overlay_and_screening_invariants() -> None:
    rows = (
        make_fact(ImportRowAction.CREATE, warning=True, possible_duplicate=True),
        make_fact(ImportRowAction.UPDATE, screening=ScreeningResult.NOT_MATCH),
        make_fact(ImportRowAction.NO_CHANGE, screening=ScreeningResult.UNKNOWN),
        make_fact(
            ImportRowAction.SKIP,
            duplicate=True,
            screening=None,
        ),
        make_fact(ImportRowAction.MANUAL_REVIEW, screening=ScreeningResult.MATCH),
        make_fact(ImportRowAction.ERROR, screening=None),
    )

    summary = summarize_preview(
        occurrence_count=3,
        included_file_count=2,
        excluded_file_count=1,
        rows=rows,
    )

    assert summary.model_dump(mode="json") == {
        "file_count": 2,
        "occurrence_count": 3,
        "excluded_file_count": 1,
        "raw_rows": 6,
        "unique_rows": 5,
        "internal_duplicate_rows": 1,
        "existing_rows": 2,
        "new_rows": 1,
        "changed_rows": 1,
        "no_change_rows": 1,
        "created_rows": 1,
        "updated_rows": 1,
        "skipped_rows": 1,
        "error_rows": 1,
        "manual_review_rows": 1,
        "warning_rows": 1,
        "possible_duplicate_contact_rows": 1,
        "screened_rows": 4,
        "screening_match_rows": 2,
        "screening_not_match_rows": 1,
        "screening_unknown_rows": 1,
    }


def test_unified_summary_rejects_broken_partition_or_screening_invariant() -> None:
    valid = summarize_preview(
        occurrence_count=1,
        included_file_count=1,
        excluded_file_count=0,
        rows=(make_fact(ImportRowAction.CREATE),),
    ).model_dump()
    with pytest.raises(ValidationError, match="raw_rows"):
        type(
            summarize_preview(
                occurrence_count=1,
                included_file_count=1,
                excluded_file_count=0,
                rows=(make_fact(ImportRowAction.CREATE),),
            )
        )(**{**valid, "raw_rows": 2})
    with pytest.raises(ValidationError, match="screened_rows"):
        type(
            summarize_preview(
                occurrence_count=1,
                included_file_count=1,
                excluded_file_count=0,
                rows=(make_fact(ImportRowAction.CREATE),),
            )
        )(**{**valid, "screened_rows": 0})


def test_row_fact_rejects_invalid_duplicate_and_screening_shapes() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        make_fact(ImportRowAction.CREATE, duplicate=True)
    with pytest.raises(ValidationError, match="screening"):
        make_fact(ImportRowAction.ERROR, screening=ScreeningResult.UNKNOWN)
    with pytest.raises(ValidationError, match="screening"):
        make_fact(ImportRowAction.CREATE, screening=None)
    with pytest.raises(ValidationError, match="skip"):
        make_fact(ImportRowAction.SKIP, duplicate=False)


def test_category_predicates_keep_overlay_separate_and_attention_deterministic() -> None:
    warning_new = make_fact(ImportRowAction.CREATE, warning=True)
    possible_duplicate_only = make_fact(
        ImportRowAction.CREATE,
        possible_duplicate=True,
    )
    duplicate = make_fact(ImportRowAction.SKIP, duplicate=True, screening=None)

    assert row_matches_category(warning_new, PreviewRowCategory.ATTENTION)
    assert row_matches_category(warning_new, PreviewRowCategory.WARNING)
    assert row_matches_category(warning_new, PreviewRowCategory.NEW)
    assert not row_matches_category(possible_duplicate_only, PreviewRowCategory.ATTENTION)
    assert row_matches_category(possible_duplicate_only, PreviewRowCategory.NEW)
    assert row_matches_category(duplicate, PreviewRowCategory.DUPLICATE)
    assert row_matches_category(duplicate, PreviewRowCategory.ALL)


def test_category_priority_uses_frozen_order() -> None:
    rows = (
        make_fact(ImportRowAction.SKIP, duplicate=True, screening=None),
        make_fact(ImportRowAction.NO_CHANGE),
        make_fact(ImportRowAction.CREATE),
        make_fact(ImportRowAction.UPDATE),
        make_fact(ImportRowAction.CREATE, warning=True),
        make_fact(ImportRowAction.MANUAL_REVIEW),
        make_fact(ImportRowAction.ERROR, screening=None),
    )

    assert [row_category_priority(row) for row in rows] == [6, 5, 4, 3, 2, 1, 0]


def test_manifest_is_sorted_by_position_and_context_hash_is_order_independent() -> None:
    first = make_context(files=(make_file(2), make_file(1)))
    second = make_context(files=(make_file(1), make_file(2)))

    assert [file.position for file in first.files] == [1, 2]
    assert first.context_hash == second.context_hash
    assert first.hash_payload()["screening_rule_hash"] == first.screening.rule_hash


def test_manifest_rejects_naive_acquisition_time() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        make_file(1, acquired_at=datetime(2026, 8, 12, 4, 0))


def test_manifest_rejects_confirmation_flag_outside_server_default_origin() -> None:
    with pytest.raises(ValidationError, match="confirmation"):
        PreviewFileManifestEntry(
            import_job_file_id=stable_uuid("excluded"),
            position=1,
            stored_file_sha256="a" * 64,
            mapping_hash=None,
            status=ImportJobFileStatus.EXCLUDED,
            included=False,
            source_acquired_at=BASE_TIME,
            source_acquired_at_origin=SourceAcquiredAtOrigin.USER_CONFIRMED,
            source_acquired_at_confirmation_required=True,
        )


def test_manifest_rejects_duplicate_positions() -> None:
    with pytest.raises(ValidationError):
        make_context(files=(make_file(1), make_file(1)))


def test_manifest_rejects_included_blocking_files() -> None:
    for changes in (
        {"status": ImportJobFileStatus.EXCLUDED},
        {"confirmation_required": True},
    ):
        with pytest.raises(ValidationError):
            make_file(1, **changes)


def test_context_hash_changes_for_every_frozen_manifest_or_rule_input() -> None:
    base = make_context()
    excluded = make_file(
        2,
        status=ImportJobFileStatus.EXCLUDED,
        included=False,
    )
    excluded_confirmation = make_file(
        2,
        status=ImportJobFileStatus.EXCLUDED,
        included=False,
        confirmation_required=True,
    )
    changes = (
        make_context(import_job_id=stable_uuid("other-job")),
        make_context(collection_job_id=stable_uuid("other-collection")),
        make_context(source_type=ImportSourceType.GENERIC_CSV),
        make_context(planner_version="phase1b-v2"),
        make_context(relevant_config={"max_batch_rows": 9_999, "enabled": False}),
        make_context(
            files=(
                make_file(1, file_id=stable_uuid("other-file")),
                make_file(2),
            )
        ),
        make_context(
            files=(
                make_file(2, file_id=stable_uuid("file-1")),
                make_file(1, file_id=stable_uuid("file-2")),
            )
        ),
        make_context(files=(make_file(1, sha256="a" * 64), make_file(2))),
        make_context(files=(make_file(1, mapping_hash="b" * 64), make_file(2))),
        make_context(
            files=(make_file(1, acquired_at=BASE_TIME + timedelta(seconds=1)), make_file(2))
        ),
        make_context(
            files=(
                make_file(1, origin=SourceAcquiredAtOrigin.USER_CONFIRMED),
                make_file(2),
            )
        ),
        make_context(files=(make_file(1), excluded)),
        make_context(files=(make_file(1), excluded_confirmation)),
        make_context(screening=make_screening_snapshot(revision=4)),
    )

    assert all(changed.context_hash != base.context_hash for changed in changes)


def test_preview_row_hash_canonicalizes_decimal_and_preserves_json_scalar_types() -> None:
    assert hash_preview_row(
        make_row_hash_input(normalized_data={"value": Decimal("98.1234567890")})
    ) == hash_preview_row(make_row_hash_input(normalized_data={"value": "98.123456789"}))

    scalar_hashes = {
        hash_preview_row(make_row_hash_input(normalized_data={"value": value}))
        for value in (None, 0, False, "")
    }
    assert len(scalar_hashes) == 4


def test_preview_row_hash_changes_with_action_warnings_errors_and_locator() -> None:
    base_input = make_row_hash_input()
    base = hash_preview_row(base_input)
    screening = evaluate_screening(make_record(), make_screening_snapshot())
    effective_summary = ChangeSummary(
        effective_changes=(
            FieldChange(
                scope=ChangeScope.ACCOUNT,
                field="display_name",
                before="old",
                incoming="new",
                after="new",
                effect=ChangeEffect.APPLY,
                reason="ACCOUNT_FIELD_APPLIED",
            ),
        )
    )
    changed = (
        hash_preview_row(make_row_hash_input(action=ImportRowAction.NO_CHANGE)),
        hash_preview_row(make_row_hash_input(normalized_data={"name": "另一达人"})),
        hash_preview_row(make_row_hash_input(warnings=({"code": "WARNING"},))),
        hash_preview_row(
            make_row_hash_input(
                action=ImportRowAction.ERROR,
                errors=({"code": "ERROR"},),
            )
        ),
        hash_preview_row(make_row_hash_input(locator=make_locator(2, 2))),
        hash_preview_row(
            base_input.model_copy(
                update={
                    "match_type": ImportMatchType.PLATFORM_ACCOUNT_ID,
                    "matched_influencer_id": stable_uuid("matched-influencer"),
                    "matched_platform_account_id": stable_uuid("matched-account"),
                }
            )
        ),
        hash_preview_row(
            base_input.model_copy(update={"duplicate_owner_locator": make_locator(2, 3)})
        ),
        hash_preview_row(base_input.model_copy(update={"merge_plan": {"operation": "update"}})),
        hash_preview_row(base_input.model_copy(update={"preconditions": {"version": 2}})),
        hash_preview_row(base_input.model_copy(update={"screening": screening})),
        hash_preview_row(base_input.model_copy(update={"change_summary": effective_summary})),
        hash_preview_row(
            base_input.model_copy(update={"manual_review": {"reason": "IDENTITY_CONFLICT"}})
        ),
    )

    assert all(item != base for item in changed)


def test_batch_plan_hash_sorts_rows_by_file_aware_locator() -> None:
    first = BatchPlanHashEntry(locator=make_locator(1, 2), row_plan_hash="a" * 64)
    second = BatchPlanHashEntry(locator=make_locator(2, 2), row_plan_hash="b" * 64)

    assert hash_batch_plan(make_context().context_hash, (second, first)) == hash_batch_plan(
        make_context().context_hash,
        (first, second),
    )


def test_batch_plan_hash_rejects_duplicate_row_locators() -> None:
    row = BatchPlanHashEntry(locator=make_locator(1, 2), row_plan_hash="a" * 64)
    with pytest.raises(ValueError, match="duplicate"):
        hash_batch_plan(make_context().context_hash, (row, row))

"""Focused validation tests for Phase 2 public import contracts."""

import pytest
from backend_core.imports.schemas import CollectionJobScreeningRulesUpdate, ImportPreviewInput
from pydantic import ValidationError


def test_screening_rules_update_is_strict_trimmed_and_versioned() -> None:
    payload = CollectionJobScreeningRulesUpdate.model_validate(
        {
            "screening_rules": {
                "schema_version": 1,
                "platforms": ["xiaohongshu"],
                "source_tags_exact_any": ["  Beauty美妆  "],
            },
            "follower_min": 0,
            "follower_max": 100,
            "expected_revision": 1,
        }
    )

    assert payload.screening_rules.source_tags_exact_any == ("Beauty美妆",)
    assert payload.follower_min == 0
    assert payload.follower_max == 100
    assert payload.expected_revision == 1


@pytest.mark.parametrize(
    "value",
    [True, 1.0, "1", -1],
)
def test_screening_rules_update_rejects_coerced_or_negative_follower_bounds(
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        CollectionJobScreeningRulesUpdate.model_validate(
            {
                "screening_rules": {"schema_version": 1},
                "follower_min": value,
                "expected_revision": 1,
            }
        )


@pytest.mark.parametrize(
    "tags",
    [
        ["   "],
        ["a" * 161],
        ["Beauty美妆", " Beauty美妆 "],
    ],
)
def test_screening_rules_update_rejects_invalid_exact_tags(tags: list[str]) -> None:
    with pytest.raises(ValidationError):
        CollectionJobScreeningRulesUpdate.model_validate(
            {
                "screening_rules": {
                    "schema_version": 1,
                    "source_tags_exact_any": tags,
                },
                "expected_revision": 1,
            }
        )


def test_screening_rules_update_rejects_unknown_schema_and_inverted_range() -> None:
    with pytest.raises(ValidationError):
        CollectionJobScreeningRulesUpdate.model_validate(
            {
                "screening_rules": {"schema_version": 2},
                "expected_revision": 1,
            }
        )
    with pytest.raises(ValidationError, match="follower_min"):
        CollectionJobScreeningRulesUpdate.model_validate(
            {
                "screening_rules": {"schema_version": 1},
                "follower_min": 2,
                "follower_max": 1,
                "expected_revision": 1,
            }
        )


@pytest.mark.parametrize("expected_revision", [True, 1.0, "1", 0])
def test_screening_rules_update_requires_strict_positive_expected_revision(
    expected_revision: object,
) -> None:
    with pytest.raises(ValidationError):
        CollectionJobScreeningRulesUpdate.model_validate(
            {
                "screening_rules": {"schema_version": 1},
                "expected_revision": expected_revision,
            }
        )


def test_screening_rules_update_rejects_duplicates_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CollectionJobScreeningRulesUpdate.model_validate(
            {
                "screening_rules": {
                    "schema_version": 1,
                    "platforms": ["xiaohongshu", "xiaohongshu"],
                },
                "expected_revision": 1,
            }
        )
    with pytest.raises(ValidationError):
        CollectionJobScreeningRulesUpdate.model_validate(
            {
                "screening_rules": {"schema_version": 1, "industry": "Beauty"},
                "expected_revision": 1,
                "notes": "must not be inferred",
            }
        )


def test_preview_rebuild_flag_is_explicit_strict_and_extra_forbidden() -> None:
    assert ImportPreviewInput().rebuild is False
    assert ImportPreviewInput.model_validate({"rebuild": True}).rebuild is True
    for payload in ({"rebuild": 1}, {"rebuild": "true"}, {"unknown": True}):
        with pytest.raises(ValidationError):
            ImportPreviewInput.model_validate(payload)

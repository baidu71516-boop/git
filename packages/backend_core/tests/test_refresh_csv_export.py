import csv
from datetime import UTC, datetime
from io import StringIO
from uuid import UUID

import pytest
from backend_core.influencers.enums import Platform
from backend_core.influencers.freshness import FreshnessStatus
from backend_core.refresh.csv_export import (
    REFRESH_QUEUE_CSV_COLUMNS,
    RefreshQueueCSVRow,
    escape_csv_text,
    export_refresh_queue_csv,
)
from backend_core.refresh.enums import RefreshPriorityReason
from backend_core.refresh.schemas import IdentitySnapshot
from pydantic import ValidationError

INFLUENCER_ID = UUID("11111111-1111-4111-8111-111111111111")
BASELINE = datetime(2026, 8, 1, 4, 5, 6, tzinfo=UTC)


def make_row(**identity_overrides: object) -> RefreshQueueCSVRow:
    identity: dict[str, object] = {
        "platform": Platform.XIAOHONGSHU,
        "account_name": "真实账号",
        "platform_account_id": "platform-1",
        "account_handle": "real-handle",
        "profile_url": "https://example.test/profile/1",
        "external_source_id": "source-1",
        "followers_count": 123,
    }
    identity.update(identity_overrides)
    return RefreshQueueCSVRow(
        influencer_id=INFLUENCER_ID,
        identity_snapshot=IdentitySnapshot.model_validate(identity),
        baseline_last_observed_at=BASELINE,
        freshness_status=FreshnessStatus.STALE,
        priority_tier=3,
        priority_reasons=(
            RefreshPriorityReason.STALE,
            RefreshPriorityReason.FOLLOWERS_MISSING,
        ),
    )


def parsed_rows(content: str) -> list[list[str]]:
    return list(csv.reader(StringIO(content, newline="")))


def test_csv_uses_fixed_public_columns_and_never_contact_columns() -> None:
    content = export_refresh_queue_csv([make_row()])
    rows = parsed_rows(content)

    assert tuple(rows[0]) == REFRESH_QUEUE_CSV_COLUMNS
    assert not {"contact", "email", "phone", "wechat", "metrics"} & set(rows[0])
    assert rows[1] == [
        str(INFLUENCER_ID),
        "xiaohongshu",
        "真实账号",
        "platform-1",
        "real-handle",
        "https://example.test/profile/1",
        "source-1",
        "123",
        "2026-08-01T04:05:06+00:00",
        "stale",
        "3",
        "STALE|FOLLOWERS_MISSING",
    ]


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("=SUM(A1:A2)", "'=SUM(A1:A2)"),
        ("+cmd", "'+cmd"),
        ("-1+2", "'-1+2"),
        ("@payload", "'@payload"),
        ("   =SUM(A1:A2)", "'   =SUM(A1:A2)"),
        ("\tcommand", "'\tcommand"),
        ("  \tcommand", "'  \tcommand"),
        ("\rcommand", "'\rcommand"),
        ("  \rcommand", "'  \rcommand"),
        ("\n @payload", "'\n @payload"),
        ("", ""),
        (" safe", " safe"),
        ("1+2", "1+2"),
        ("'=@already-text", "'=@already-text"),
    ],
)
def test_formula_escape_checks_first_or_first_after_leading_whitespace(
    original: str,
    expected: str,
) -> None:
    assert escape_csv_text(original) == expected


def test_csv_formula_protection_applies_to_every_identity_text_field() -> None:
    row = make_row(
        account_name="=account",
        platform_account_id="  +platform-id",
        account_handle="\thandle",
        profile_url="  @profile",
        external_source_id="\rsource-id",
    )
    original_snapshot = row.identity_snapshot.model_dump()

    exported = parsed_rows(export_refresh_queue_csv([row]))[1]

    assert exported[2] == "'=account"
    assert exported[3] == "'  +platform-id"
    assert exported[4] == "'\thandle"
    assert exported[5] == "'  @profile"
    assert exported[6] == "'\rsource-id"
    assert row.identity_snapshot.model_dump() == original_snapshot


def test_csv_uses_standard_quoting_for_commas_quotes_and_newlines() -> None:
    row = make_row(
        account_name='账号, "Quoted"',
        account_handle="line one\nline two",
    )

    content = export_refresh_queue_csv([row])
    parsed = parsed_rows(content)

    assert '"账号, ""Quoted"""' in content
    assert '"line one\nline two"' in content
    assert parsed[1][2] == '账号, "Quoted"'
    assert parsed[1][4] == "line one\nline two"


def test_csv_writes_empty_cells_for_missing_optional_values() -> None:
    row = RefreshQueueCSVRow(
        influencer_id=INFLUENCER_ID,
        identity_snapshot=IdentitySnapshot(
            platform=Platform.XIAOHONGSHU,
            account_name="真实账号",
        ),
        baseline_last_observed_at=None,
        freshness_status=FreshnessStatus.UNKNOWN,
        priority_tier=1,
        priority_reasons=(RefreshPriorityReason.FRESHNESS_UNKNOWN,),
    )

    exported = parsed_rows(export_refresh_queue_csv(iter([row])))[1]

    assert exported[3:7] == ["", "", "", ""]
    assert exported[7] == ""
    assert exported[8] == ""


def test_csv_row_is_closed_and_cannot_carry_contact() -> None:
    payload = make_row().model_dump()
    payload["contact"] = "forbidden"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RefreshQueueCSVRow.model_validate(payload)


def test_formula_escape_also_protects_future_reason_display_text() -> None:
    assert escape_csv_text("  =translated reason") == "'  =translated reason"

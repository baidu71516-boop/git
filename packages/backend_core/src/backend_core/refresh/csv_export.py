"""Stable, contact-free CSV rendering for refresh queue exports."""

from __future__ import annotations

import csv
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import Enum
from io import StringIO
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from backend_core.influencers.freshness import FreshnessStatus
from backend_core.refresh.enums import RefreshPriorityReason
from backend_core.refresh.schemas import IdentitySnapshot

REFRESH_QUEUE_CSV_COLUMNS = (
    "influencer_id",
    "platform",
    "account_name",
    "platform_account_id",
    "account_handle",
    "profile_url",
    "external_source_id",
    "followers_count",
    "baseline_last_observed_at",
    "freshness",
    "priority",
    "reasons",
)

type PriorityTier = Annotated[StrictInt, Field(ge=1, le=5)]


class RefreshQueueCSVRow(BaseModel):
    """One export projection built only from frozen queue-item fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    influencer_id: UUID
    identity_snapshot: IdentitySnapshot
    baseline_last_observed_at: datetime | None = None
    freshness_status: FreshnessStatus
    priority_tier: PriorityTier
    priority_reasons: tuple[RefreshPriorityReason, ...] = Field(min_length=1)

    @field_validator("baseline_last_observed_at")
    @classmethod
    def require_aware_baseline(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("baseline_last_observed_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_priority(self) -> RefreshQueueCSVRow:
        expected = {
            1: (FreshnessStatus.UNKNOWN, RefreshPriorityReason.FRESHNESS_UNKNOWN),
            2: (FreshnessStatus.VERY_STALE, RefreshPriorityReason.VERY_STALE),
            3: (FreshnessStatus.STALE, RefreshPriorityReason.STALE),
            4: (FreshnessStatus.AGING, RefreshPriorityReason.AGING),
            5: (FreshnessStatus.FRESH, RefreshPriorityReason.FOLLOWERS_MISSING),
        }[self.priority_tier]
        if self.freshness_status is not expected[0]:
            raise ValueError("freshness_status must match priority_tier")
        if self.priority_reasons[0] is not expected[1]:
            raise ValueError("priority_reasons must begin with the tier's primary reason")
        if len(set(self.priority_reasons)) != len(self.priority_reasons):
            raise ValueError("priority_reasons must not contain duplicates")
        if self.priority_tier == 5 and self.priority_reasons != (
            RefreshPriorityReason.FOLLOWERS_MISSING,
        ):
            raise ValueError("tier 5 must contain only FOLLOWERS_MISSING")
        if self.priority_tier != 5 and self.priority_reasons not in {
            (expected[1],),
            (expected[1], RefreshPriorityReason.FOLLOWERS_MISSING),
        }:
            raise ValueError("FOLLOWERS_MISSING may only follow the primary reason")
        return self


def escape_csv_text(value: str) -> str:
    """Prefix formula-like text while preserving every original character.

    A tab or carriage return anywhere in the leading whitespace is itself a
    dangerous prefix. Other leading whitespace is skipped when checking for
    ``=``, ``+``, ``-``, or ``@``.
    """

    for character in value:
        if character in {"\t", "\r"}:
            return f"'{value}"
        if character.isspace():
            continue
        if character in {"=", "+", "-", "@"}:
            return f"'{value}"
        break
    return value


def export_refresh_queue_csv(rows: Iterable[RefreshQueueCSVRow]) -> str:
    """Render frozen rows with fixed columns and standard RFC-style CSV quoting."""

    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(REFRESH_QUEUE_CSV_COLUMNS)
    for row in rows:
        identity = row.identity_snapshot
        writer.writerow(
            _serialize_cell(value)
            for value in (
                row.influencer_id,
                identity.platform,
                identity.account_name,
                identity.platform_account_id,
                identity.account_handle,
                identity.profile_url,
                identity.external_source_id,
                identity.followers_count,
                row.baseline_last_observed_at,
                row.freshness_status,
                row.priority_tier,
                "|".join(reason.value for reason in row.priority_reasons),
            )
        )
    return output.getvalue()


def _serialize_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        rendered = value.astimezone(UTC).isoformat()
    elif isinstance(value, Enum):
        rendered = str(value.value)
    else:
        rendered = str(value)
    return escape_csv_text(rendered)


__all__ = [
    "REFRESH_QUEUE_CSV_COLUMNS",
    "RefreshQueueCSVRow",
    "escape_csv_text",
    "export_refresh_queue_csv",
]

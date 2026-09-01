"""Platform-neutral contracts shared by import adapters and planners."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from backend_core.influencers.enums import (
    ContactType,
    ContactValidationStatus,
    DataSource,
    Platform,
)

if TYPE_CHECKING:
    from backend_core.imports.parsers import RawTabularRecord


class FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RowIssue(FrozenContract):
    """A non-sensitive row-level warning or error."""

    code: str
    message: str
    field: str | None = None

    def as_dict(self) -> dict[str, str]:
        return self.model_dump(mode="json", exclude_none=True)


class PlatformIdentity(FrozenContract):
    """A platform identity; contacts are deliberately excluded."""

    platform: Platform
    platform_account_id: str | None
    account_handle: str | None
    profile_url: str | None
    normalized_profile_url: str | None
    external_source_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class CanonicalContact(FrozenContract):
    type: ContactType
    value: str
    normalized_value: str
    validation_status: ContactValidationStatus

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class CanonicalInfluencerRecord(FrozenContract):
    """Canonical record consumed by platform-neutral matching and planning."""

    display_name: str | None
    platform_identity: PlatformIdentity
    source: DataSource
    source_updated_at: datetime | None
    public_profile: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    contacts: tuple[CanonicalContact, ...] = ()
    warnings: tuple[RowIssue, ...] = ()
    errors: tuple[RowIssue, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class AdaptedRow(FrozenContract):
    """One raw row plus its canonical interpretation and row-local issues."""

    row_number: int
    raw_data: dict[str, Any]
    record: CanonicalInfluencerRecord
    warnings: tuple[RowIssue, ...] = ()
    errors: tuple[RowIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def normalized_data(self) -> dict[str, Any]:
        return self.record.as_dict()

    def warning_dicts(self) -> list[dict[str, str]]:
        return [warning.as_dict() for warning in self.warnings]

    def error_dicts(self) -> list[dict[str, str]]:
        return [error.as_dict() for error in self.errors]


class SourceAdapter(Protocol):
    """Source-specific boundary that never leaks source headers to planners."""

    platform: Platform
    source: DataSource

    def mapping_for_headers(self, headers: list[str]) -> dict[str, str]: ...

    def adapt(self, raw_record: RawTabularRecord) -> AdaptedRow: ...

    def validate_table(self, rows: list[RawTabularRecord]) -> None: ...


__all__ = [
    "AdaptedRow",
    "CanonicalContact",
    "CanonicalInfluencerRecord",
    "PlatformIdentity",
    "RowIssue",
    "SourceAdapter",
]

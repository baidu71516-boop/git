"""Strict public-safe contracts for Content Activity commands and read models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class ContentActivityInput(BaseModel):
    """Closed request base; inputs must never retain provider bootstrap material."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class XhsIdentityResolutionInput(ContentActivityInput):
    """One ephemeral resolver input; it is deliberately a secret-like field."""

    platform_account_id: UUID
    bootstrap_input: SecretStr

    @field_validator("bootstrap_input")
    @classmethod
    def validate_bootstrap_input(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw.strip() or len(raw) > 2_048:
            raise ValueError("bootstrap_input is invalid")
        return value


class XhsIdentityResolutionPublic(BaseModel):
    """Secret-free result from an identity resolution/binding attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    platform_account_id: UUID
    identity_binding_id: UUID | None
    verification_event_id: UUID | None
    outcome: Literal["VERIFIED_CURRENT", "IDENTITY_CONFLICT", "IDENTITY_UNRESOLVED", "UNTRUSTED"]


class XhsRefreshCreateInput(ContentActivityInput):
    """A bounded set of account IDs.  Queue payloads contain only created UUIDs."""

    platform_account_ids: tuple[UUID, ...] = Field(min_length=1)

    @field_validator("platform_account_ids")
    @classmethod
    def validate_unique_account_ids(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(value) != len(set(value)):
            raise ValueError("platform_account_ids must not contain duplicates")
        return value


class ContentActivityRefreshRequestPublic(BaseModel):
    """Safe status projection of a durable provider refresh request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_token: UUID
    platform_account_id: UUID
    state: str
    attempt_count: int
    max_attempts: int
    requested_at: datetime


class XhsRefreshCreateResult(BaseModel):
    """Results are one-per-exact account; no raw provider field is exposed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requests: tuple[ContentActivityRefreshRequestPublic, ...]


class DouyinRuntimeCaptureCreateInput(ContentActivityInput):
    """Request one local-only Huitun runtime capture for an existing Douyin account."""

    platform_account_id: UUID


class DouyinRuntimePublicationInput(ContentActivityInput):
    """One already-normalized publication timestamp, never a provider response row."""

    published_at: datetime

    @field_validator("published_at")
    @classmethod
    def require_aware_publication_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must be timezone-aware")
        return value.astimezone(UTC)


class DouyinRuntimeCaptureIngestInput(ContentActivityInput):
    """Secret-free semantic result emitted by the local Chrome extension.

    The extension never sends the raw encrypted envelope, Huitun cookies,
    Authorization headers, or a browser session.  ``actual_uid`` is observed
    from the real awemeList request; ``semantic_uid`` comes from the decoded
    runtime layer and is compared server-side before any result is trusted.
    """

    capture_request_id: UUID
    runtime_request_id: str = Field(min_length=1, max_length=80)
    outcome: Literal[
        "SUCCESS",
        "EMPTY",
        "PARTIAL",
        "RAW_ENCRYPTED",
        "AUTH_FAILURE",
        "RUNTIME_ERROR",
        "INVALID_PAYLOAD",
    ]
    actual_uid: str | None = Field(default=None, max_length=160)
    semantic_uid: str | None = Field(default=None, max_length=160)
    publications: tuple[DouyinRuntimePublicationInput, ...] = Field(
        default=(), max_length=1_000
    )
    pagination_terminal: bool | None = None
    latest_page_proven: bool = False
    coverage_start_at: datetime | None = None
    coverage_end_at: datetime | None = None
    rejection_reason: Literal[
        "UID_MISMATCH",
        "REQUEST_BINDING_MISMATCH",
        "AMBIGUOUS_REQUEST",
        "PUBLICATION_UID_CONFLICT",
        "SCHEMA_REJECTED",
    ] | None = None

    @field_validator("runtime_request_id", "actual_uid", "semantic_uid")
    @classmethod
    def require_safe_identifier_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or any(ord(character) < 33 or ord(character) > 126 for character in value):
            raise ValueError("runtime capture identifier is invalid")
        return value

    @field_validator("coverage_start_at", "coverage_end_at")
    @classmethod
    def require_aware_coverage_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("coverage time must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_normalized_shape(self) -> DouyinRuntimeCaptureIngestInput:
        semantic_outcomes = {"SUCCESS", "EMPTY", "PARTIAL"}
        if self.outcome in semantic_outcomes and not self.actual_uid:
            raise ValueError("semantic capture requires actual_uid from awemeList")
        if self.outcome == "SUCCESS":
            if not self.publications:
                raise ValueError("SUCCESS requires publications")
            if self.pagination_terminal is not True and not (
                self.pagination_terminal is False and self.latest_page_proven
            ):
                raise ValueError(
                    "SUCCESS requires terminal pagination or proven newest first page"
                )
            if self.coverage_end_at is None:
                raise ValueError("SUCCESS requires coverage_end_at")
        elif self.outcome == "EMPTY":
            if (
                self.publications
                or self.pagination_terminal is not True
                or self.latest_page_proven
            ):
                raise ValueError("EMPTY requires no publications and terminal pagination")
            if self.coverage_start_at is None or self.coverage_end_at is None:
                raise ValueError("EMPTY requires an explicit coverage range")
        elif self.outcome == "PARTIAL":
            if self.pagination_terminal is True or self.latest_page_proven:
                raise ValueError("PARTIAL cannot claim terminal or latest-page proof")
        elif self.publications or self.latest_page_proven:
            raise ValueError("non-semantic runtime failures cannot include semantic evidence")
        if self.outcome == "INVALID_PAYLOAD" and self.rejection_reason is None:
            raise ValueError("INVALID_PAYLOAD requires a sanitized rejection_reason")
        if self.outcome != "INVALID_PAYLOAD" and self.rejection_reason is not None:
            raise ValueError("rejection_reason is only valid for INVALID_PAYLOAD")
        if (
            self.coverage_start_at is not None
            and self.coverage_end_at is not None
            and self.coverage_start_at > self.coverage_end_at
        ):
            raise ValueError("coverage_start_at must not exceed coverage_end_at")
        return self


class DouyinRuntimeCaptureLaunchPublic(BaseModel):
    """One-time bridge material returned only to the authenticated operator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capture_request_id: UUID
    capture_token: str = Field(min_length=32, max_length=256)
    expires_at: datetime
    ingest_path: Literal["/api/v1/admin/content-activity/douyin/runtime-captures/ingest"]


class DouyinRuntimeCaptureIngestPublic(BaseModel):
    """Secret-free terminal acknowledgment for the extension bridge."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capture_request_id: UUID
    observation_id: UUID
    outcome: Literal["ACCEPTED", "UNKNOWN"]

"""Strict public-safe contracts for Content Activity commands and read models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


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

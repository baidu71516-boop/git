"""Stable, HTTP-independent errors for the Phase 3A Campaign/Outreach domain."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID


class CampaignOutreachError(Exception):
    """A domain failure with the status and code the HTTP adapter will expose later."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        current_version: int | None = None,
        entity_id: UUID | None = None,
        safe_details: Mapping[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.current_version = current_version
        self.entity_id = entity_id
        # Domain callers must opt in explicitly to information that the HTTP
        # adapter can expose.  Keep a detached mapping so later mutation by a
        # caller cannot alter the emitted error payload.
        self.safe_details = dict(safe_details) if safe_details is not None else None
        super().__init__(message)

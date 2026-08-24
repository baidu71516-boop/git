"""Fail-closed TikHub Xiaohongshu App V2 adapter for Content Activity V1.

This module owns only provider mechanics and normalization.  A caller must pass
the current, policy-accepted ``xiaohongshu.userid`` binding; this adapter cannot
turn an import/bootstrap value into trusted identity on its own.

The V1 activity path makes exactly one request.  It never retries, follows a
cursor, or logs provider responses, request inputs, or Authorization material.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, TypeGuard

import httpx

from backend_core.common.logging import suppress_http_client_request_logs
from backend_core.config.settings import Settings
from backend_core.content_activity.capability_policy import (
    TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1,
    TIKHUB_PROVIDER,
    TIKHUB_XHS_APP_V2_CURRENT_PUBLIC_VISIBILITY_POLICY_V1,
    TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1,
    TIKHUB_XHS_APP_V2_GET_USER_POSTED_NOTES_RESPONSE_SCHEMA_V1,
    XHS_APP_V2_PRODUCT,
    XHS_GET_USER_INFO_ENDPOINT,
    XHS_GET_USER_POSTED_NOTES_ENDPOINT,
    XHS_SEMANTIC_SUCCESS_CODE,
    CapabilityArtifactIntegrityError,
    assert_xhs_v1_registry_integrity,
)
from backend_core.content_activity.enums import (
    ContentActivityCoverageStatus,
    ContentActivityObservationStatus,
    ContentActivityProviderErrorClass,
    ContentActivityPublicationType,
    ContentActivityResult,
    ContentActivityScanTerminalReason,
)


class XhsAdapterError(ValueError):
    """A redacted, closed adapter failure suitable for sanitized diagnostics."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class XhsSchemaViolation(XhsAdapterError):
    """The response did not exactly match a recognized V1 schema."""


class XhsSemanticFailure(XhsAdapterError):
    """The provider HTTP response lacked the pinned semantic-success marker."""


class ProviderResponseTooLarge(XhsAdapterError):
    """The provider body exceeded the configured bounded-response allowance."""

    def __init__(self) -> None:
        super().__init__("RESPONSE_TOO_LARGE")


@dataclass(frozen=True, slots=True)
class HttpRequestLimits:
    """Finite socket, wall-clock, and body-size bounds for provider calls.

    ``httpx.Timeout`` limits individual transport operations.  The authoritative
    ``total_timeout_seconds`` deadline is additionally enforced around the whole
    provider interaction so a stream cannot extend a call by drip-feeding bytes.
    """

    connect_timeout_seconds: float
    read_timeout_seconds: float
    pool_timeout_seconds: float
    total_timeout_seconds: float
    max_response_bytes: int


@dataclass(frozen=True, slots=True)
class HttpTransportResponse:
    """Bounded raw bytes returned by an injected HTTP transport."""

    status_code: int
    body: bytes


class AsyncHttpTransport(Protocol):
    """Narrow injectable transport; it exposes no implicit retry behavior."""

    async def get(
        self,
        *,
        path: str,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        limits: HttpRequestLimits,
    ) -> HttpTransportResponse: ...


class HttpxAsyncTransport:
    """HTTPX transport with streaming size bounds, deadlines, and no retries."""

    def __init__(self, base_url: str, *, client: httpx.AsyncClient | None = None) -> None:
        # This adapter places resolver input and canonical identities in query
        # parameters.  Keep the safety control local too, so direct worker or
        # script usage remains safe even if an embedding process skipped the
        # normal API/worker logging bootstrap.
        suppress_http_client_request_logs()
        self._client = client or httpx.AsyncClient(base_url=base_url, follow_redirects=False)
        self._owns_client = client is None

    async def get(
        self,
        *,
        path: str,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        limits: HttpRequestLimits,
    ) -> HttpTransportResponse:
        timeout = httpx.Timeout(
            limits.total_timeout_seconds,
            connect=limits.connect_timeout_seconds,
            read=limits.read_timeout_seconds,
            write=limits.total_timeout_seconds,
            pool=limits.pool_timeout_seconds,
        )
        # HTTPX applies the limits above per connect/read/write/pool operation.
        # This outer scope is the provider-call deadline: it begins immediately
        # before request start and stays active through headers, every stream
        # chunk, size validation, and body assembly.
        async with asyncio.timeout(limits.total_timeout_seconds):
            async with self._client.stream(
                "GET",
                path,
                params=params,
                headers=headers,
                timeout=timeout,
            ) as response:
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError:
                        declared_size = 0
                    if declared_size > limits.max_response_bytes:
                        raise ProviderResponseTooLarge()

                chunks: list[bytes] = []
                received_bytes = 0
                async for chunk in response.aiter_bytes():
                    received_bytes += len(chunk)
                    if received_bytes > limits.max_response_bytes:
                        raise ProviderResponseTooLarge()
                    chunks.append(chunk)
                return HttpTransportResponse(
                    status_code=response.status_code,
                    body=b"".join(chunks),
                )

    async def aclose(self) -> None:
        """Close only a client created by this transport instance."""

        if self._owns_client:
            await self._client.aclose()


@dataclass(frozen=True, slots=True)
class XhsIdentityResolution:
    """Normalized resolver outcome, never a persisted identity binding by itself."""

    observation_status: ContentActivityObservationStatus
    userid: str | None
    provider_error_class: ContentActivityProviderErrorClass | None
    provider_error_code: str | None
    terminal_reason: ContentActivityScanTerminalReason
    provider: str = TIKHUB_PROVIDER
    provider_product: str = XHS_APP_V2_PRODUCT
    endpoint: str = XHS_GET_USER_INFO_ENDPOINT
    capability_policy_version: str = TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1
    schema_contract_id: str = TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1
    request_count: int = 0

    @property
    def usable(self) -> bool:
        """Whether the resolver returned a schema-valid canonical external identity."""

        return (
            self.observation_status is ContentActivityObservationStatus.COMPLETE
            and self.userid is not None
        )


@dataclass(frozen=True, slots=True)
class NormalizedXhsPublication:
    """One schema-recognized current-public candidate from the single response."""

    publication_id: str
    publication_type: ContentActivityPublicationType
    published_at: datetime
    is_top: bool


@dataclass(frozen=True, slots=True)
class ParsedXhsPostedNotes:
    """Validated one-response payload before its coverage decision is applied."""

    has_more: bool
    publications: tuple[NormalizedXhsPublication, ...]
    raw_item_count: int


@dataclass(frozen=True, slots=True)
class XhsActivityAttempt:
    """Provider-neutral values ready for the durable service to persist/projection."""

    observation_status: ContentActivityObservationStatus
    coverage_status: ContentActivityCoverageStatus
    activity_result: ContentActivityResult
    observed_at: datetime
    last_publication_at: datetime | None
    latest_publication_id: str | None
    latest_publication_type: ContentActivityPublicationType | None
    co_latest_publication_count: int | None
    provider_error_class: ContentActivityProviderErrorClass | None
    provider_error_code: str | None
    terminal_reason: ContentActivityScanTerminalReason
    item_count: int
    request_count: int
    provider: str = TIKHUB_PROVIDER
    provider_product: str = XHS_APP_V2_PRODUCT
    endpoint: str = XHS_GET_USER_POSTED_NOTES_ENDPOINT
    capability_policy_version: str = TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1
    schema_contract_id: str = TIKHUB_XHS_APP_V2_GET_USER_POSTED_NOTES_RESPONSE_SCHEMA_V1
    visibility_policy_id: str = TIKHUB_XHS_APP_V2_CURRENT_PUBLIC_VISIBILITY_POLICY_V1

    @property
    def trusted(self) -> bool:
        """Only the strict COMPLETE/FULL pair is trusted current-public evidence."""

        return (
            self.observation_status is ContentActivityObservationStatus.COMPLETE
            and self.coverage_status is ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET
            and self.activity_result
            in (ContentActivityResult.PUBLICATION_FOUND, ContentActivityResult.NO_PUBLIC_CONTENT)
        )


class TikHubXhsClient:
    """Pinned V1 TikHub XHS client with an injected/testable HTTP boundary."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: AsyncHttpTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock or _utc_now
        self._transport = transport
        self._owns_transport = False
        if transport is None and self._calls_permitted:
            self._transport = HttpxAsyncTransport(settings.tikhub_base_url)
            self._owns_transport = True

    async def aclose(self) -> None:
        """Release a default transport while leaving an injected one to its owner."""

        if self._owns_transport and isinstance(self._transport, HttpxAsyncTransport):
            await self._transport.aclose()

    async def resolve_user(self, *, bootstrap_input: str) -> XhsIdentityResolution:
        """Resolve bounded, ephemeral bootstrap text into a schema-valid ``userid``.

        The caller must discard ``bootstrap_input`` after this call and must still
        perform the canonical identity-binding conflict checks before the value may
        support a trusted Content Activity observation.
        """

        if not self._calls_permitted:
            return _identity_failure(
                observation_status=ContentActivityObservationStatus.IDENTITY_UNRESOLVED,
                error_class=ContentActivityProviderErrorClass.POLICY_REJECTED,
                error_code="CONFIG_DISABLED",
                terminal_reason=ContentActivityScanTerminalReason.REQUEST_NOT_PERMITTED,
            )
        if not _is_valid_bootstrap_input(bootstrap_input):
            return _identity_failure(
                observation_status=ContentActivityObservationStatus.IDENTITY_UNRESOLVED,
                error_class=ContentActivityProviderErrorClass.INPUT_UNAVAILABLE,
                error_code="BOOTSTRAP_INPUT_INVALID",
                terminal_reason=ContentActivityScanTerminalReason.IDENTITY_UNRESOLVED,
            )
        try:
            assert_xhs_v1_registry_integrity()
        except CapabilityArtifactIntegrityError:
            return _identity_failure(
                observation_status=ContentActivityObservationStatus.RESULT_UNTRUSTED,
                error_class=ContentActivityProviderErrorClass.SCHEMA_DRIFT,
                error_code="CAPABILITY_ARTIFACT_INVALID",
                terminal_reason=ContentActivityScanTerminalReason.POLICY_REJECTED,
            )

        try:
            # The transport owns its own exact request-start deadline.  Keep an
            # adapter scope too so parsed/validated completion cannot outlive
            # the configured provider-call budget.
            async with asyncio.timeout(self._limits.total_timeout_seconds):
                response = await self._request(
                    path=XHS_GET_USER_INFO_ENDPOINT,
                    params={"share_text": bootstrap_input},
                )
                http_failure = _identity_http_failure(response.status_code)
                if http_failure is not None:
                    return http_failure
                if len(response.body) > self._limits.max_response_bytes:
                    return _identity_failure(
                        observation_status=ContentActivityObservationStatus.IDENTITY_UNRESOLVED,
                        error_class=ContentActivityProviderErrorClass.MALFORMED_RESPONSE,
                        error_code="RESPONSE_TOO_LARGE",
                        terminal_reason=ContentActivityScanTerminalReason.PROVIDER_FAILURE,
                        request_count=1,
                    )

                try:
                    userid = parse_xhs_user_info_response(response.body)
                except XhsSemanticFailure:
                    return _identity_failure(
                        observation_status=ContentActivityObservationStatus.RESULT_UNTRUSTED,
                        error_class=ContentActivityProviderErrorClass.SEMANTIC_FAILURE,
                        error_code="SEMANTIC_CODE_REJECTED",
                        terminal_reason=ContentActivityScanTerminalReason.RESPONSE_UNTRUSTED,
                        request_count=1,
                    )
                except XhsSchemaViolation as exc:
                    return _identity_failure(
                        observation_status=ContentActivityObservationStatus.RESULT_UNTRUSTED,
                        error_class=ContentActivityProviderErrorClass.SCHEMA_DRIFT,
                        error_code=exc.code,
                        terminal_reason=ContentActivityScanTerminalReason.RESPONSE_UNTRUSTED,
                        request_count=1,
                    )

                return XhsIdentityResolution(
                    observation_status=ContentActivityObservationStatus.COMPLETE,
                    userid=userid,
                    provider_error_class=None,
                    provider_error_code=None,
                    terminal_reason=ContentActivityScanTerminalReason.SINGLE_RESPONSE_COMPLETE,
                    request_count=1,
                )
        except ProviderResponseTooLarge:
            return _identity_failure(
                observation_status=ContentActivityObservationStatus.IDENTITY_UNRESOLVED,
                error_class=ContentActivityProviderErrorClass.MALFORMED_RESPONSE,
                error_code="RESPONSE_TOO_LARGE",
                terminal_reason=ContentActivityScanTerminalReason.PROVIDER_FAILURE,
                request_count=1,
            )
        except (httpx.TimeoutException, TimeoutError):
            return _identity_failure(
                observation_status=ContentActivityObservationStatus.IDENTITY_UNRESOLVED,
                error_class=ContentActivityProviderErrorClass.TIMEOUT,
                error_code="TRANSPORT_TIMEOUT",
                terminal_reason=ContentActivityScanTerminalReason.PROVIDER_FAILURE,
                request_count=1,
            )
        except (httpx.HTTPError, OSError):
            return _identity_failure(
                observation_status=ContentActivityObservationStatus.IDENTITY_UNRESOLVED,
                error_class=ContentActivityProviderErrorClass.TRANSPORT,
                error_code="TRANSPORT_FAILURE",
                terminal_reason=ContentActivityScanTerminalReason.PROVIDER_FAILURE,
                request_count=1,
            )

    async def observe_posted_notes(
        self,
        *,
        verified_userid: str,
        observed_at: datetime | None = None,
    ) -> XhsActivityAttempt:
        """Observe one complete-or-fail-closed current-public XHS response.

        ``verified_userid`` must originate from a current policy-accepted
        ProviderAccountIdentity binding.  Invalid input, a disabled gate, or any
        response uncertainty returns an untrusted/undetermined attempt and makes
        no retry or second-page request.
        """

        now = _normalize_observed_at(observed_at or self._clock())
        if not _is_valid_opaque_identity(verified_userid):
            return _activity_failure(
                observed_at=now,
                observation_status=ContentActivityObservationStatus.IDENTITY_UNRESOLVED,
                coverage_status=ContentActivityCoverageStatus.UNKNOWN,
                error_class=ContentActivityProviderErrorClass.INPUT_UNAVAILABLE,
                error_code="VERIFIED_USERID_INVALID",
                terminal_reason=ContentActivityScanTerminalReason.IDENTITY_UNRESOLVED,
            )
        if not self._calls_permitted:
            return _activity_failure(
                observed_at=now,
                observation_status=ContentActivityObservationStatus.UNKNOWN,
                coverage_status=ContentActivityCoverageStatus.UNKNOWN,
                error_class=ContentActivityProviderErrorClass.POLICY_REJECTED,
                error_code="CONFIG_DISABLED",
                terminal_reason=ContentActivityScanTerminalReason.REQUEST_NOT_PERMITTED,
            )

        try:
            assert_xhs_v1_registry_integrity()
        except CapabilityArtifactIntegrityError:
            return _activity_failure(
                observed_at=now,
                observation_status=ContentActivityObservationStatus.RESULT_UNTRUSTED,
                coverage_status=ContentActivityCoverageStatus.UNKNOWN,
                error_class=ContentActivityProviderErrorClass.SCHEMA_DRIFT,
                error_code="CAPABILITY_ARTIFACT_INVALID",
                terminal_reason=ContentActivityScanTerminalReason.POLICY_REJECTED,
            )

        try:
            # See the resolver path above.  This keeps the deadline active
            # until the full response has been parsed into a trusted-or-failed
            # attempt, not merely until headers or a partial stream arrive.
            async with asyncio.timeout(self._limits.total_timeout_seconds):
                response = await self._request(
                    path=XHS_GET_USER_POSTED_NOTES_ENDPOINT,
                    params={"user_id": verified_userid, "cursor": ""},
                )
                http_failure = _activity_http_failure(response.status_code, now)
                if http_failure is not None:
                    return http_failure
                if len(response.body) > self._limits.max_response_bytes:
                    return _activity_failure(
                        observed_at=now,
                        observation_status=ContentActivityObservationStatus.PROVIDER_ERROR,
                        coverage_status=ContentActivityCoverageStatus.UNKNOWN,
                        error_class=ContentActivityProviderErrorClass.MALFORMED_RESPONSE,
                        error_code="RESPONSE_TOO_LARGE",
                        terminal_reason=ContentActivityScanTerminalReason.PROVIDER_FAILURE,
                        request_count=1,
                    )

                try:
                    parsed = parse_xhs_posted_notes_response(response.body, observed_at=now)
                except XhsSemanticFailure:
                    return _activity_failure(
                        observed_at=now,
                        observation_status=ContentActivityObservationStatus.RESULT_UNTRUSTED,
                        coverage_status=ContentActivityCoverageStatus.UNKNOWN,
                        error_class=ContentActivityProviderErrorClass.SEMANTIC_FAILURE,
                        error_code="SEMANTIC_CODE_REJECTED",
                        terminal_reason=ContentActivityScanTerminalReason.RESPONSE_UNTRUSTED,
                        request_count=1,
                    )
                except XhsSchemaViolation as exc:
                    return _activity_failure(
                        observed_at=now,
                        observation_status=ContentActivityObservationStatus.RESULT_UNTRUSTED,
                        coverage_status=ContentActivityCoverageStatus.UNKNOWN,
                        error_class=ContentActivityProviderErrorClass.SCHEMA_DRIFT,
                        error_code=exc.code,
                        terminal_reason=ContentActivityScanTerminalReason.RESPONSE_UNTRUSTED,
                        request_count=1,
                    )

                if parsed.has_more:
                    return _activity_failure(
                        observed_at=now,
                        observation_status=ContentActivityObservationStatus.RESULT_INCOMPLETE,
                        coverage_status=ContentActivityCoverageStatus.INCOMPLETE,
                        error_class=None,
                        error_code=None,
                        terminal_reason=ContentActivityScanTerminalReason.HAS_MORE_TRUE,
                        item_count=parsed.raw_item_count,
                        request_count=1,
                    )
                if not parsed.publications:
                    return XhsActivityAttempt(
                        observation_status=ContentActivityObservationStatus.COMPLETE,
                        coverage_status=ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET,
                        activity_result=ContentActivityResult.NO_PUBLIC_CONTENT,
                        observed_at=now,
                        last_publication_at=None,
                        latest_publication_id=None,
                        latest_publication_type=None,
                        co_latest_publication_count=None,
                        provider_error_class=None,
                        provider_error_code=None,
                        terminal_reason=ContentActivityScanTerminalReason.SINGLE_RESPONSE_COMPLETE,
                        item_count=0,
                        request_count=1,
                    )

                latest_at = max(publication.published_at for publication in parsed.publications)
                latest = sorted(
                    (
                        publication
                        for publication in parsed.publications
                        if publication.published_at == latest_at
                    ),
                    key=lambda publication: publication.publication_id,
                )
                representative = latest[0]
                return XhsActivityAttempt(
                    observation_status=ContentActivityObservationStatus.COMPLETE,
                    coverage_status=ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET,
                    activity_result=ContentActivityResult.PUBLICATION_FOUND,
                    observed_at=now,
                    last_publication_at=latest_at,
                    latest_publication_id=representative.publication_id,
                    latest_publication_type=representative.publication_type,
                    co_latest_publication_count=len(latest),
                    provider_error_class=None,
                    provider_error_code=None,
                    terminal_reason=ContentActivityScanTerminalReason.SINGLE_RESPONSE_COMPLETE,
                    item_count=parsed.raw_item_count,
                    request_count=1,
                )
        except ProviderResponseTooLarge:
            return _activity_failure(
                observed_at=now,
                observation_status=ContentActivityObservationStatus.PROVIDER_ERROR,
                coverage_status=ContentActivityCoverageStatus.UNKNOWN,
                error_class=ContentActivityProviderErrorClass.MALFORMED_RESPONSE,
                error_code="RESPONSE_TOO_LARGE",
                terminal_reason=ContentActivityScanTerminalReason.PROVIDER_FAILURE,
                request_count=1,
            )
        except (httpx.TimeoutException, TimeoutError):
            return _activity_failure(
                observed_at=now,
                observation_status=ContentActivityObservationStatus.PROVIDER_ERROR,
                coverage_status=ContentActivityCoverageStatus.UNKNOWN,
                error_class=ContentActivityProviderErrorClass.TIMEOUT,
                error_code="TRANSPORT_TIMEOUT",
                terminal_reason=ContentActivityScanTerminalReason.PROVIDER_FAILURE,
                request_count=1,
            )
        except (httpx.HTTPError, OSError):
            return _activity_failure(
                observed_at=now,
                observation_status=ContentActivityObservationStatus.PROVIDER_ERROR,
                coverage_status=ContentActivityCoverageStatus.UNKNOWN,
                error_class=ContentActivityProviderErrorClass.TRANSPORT,
                error_code="TRANSPORT_FAILURE",
                terminal_reason=ContentActivityScanTerminalReason.PROVIDER_FAILURE,
                request_count=1,
            )

    @property
    def _calls_permitted(self) -> bool:
        return (
            self._settings.content_activity_enabled
            and self._settings.content_activity_xhs_enabled
            and self._settings.content_activity_provider_governance_approved
            and self._settings.content_activity_provider_max_calls_per_run >= 1
            and self._settings.content_activity_xhs_max_calls_per_account == 1
            and self._transport is not None
        )

    @property
    def _limits(self) -> HttpRequestLimits:
        return HttpRequestLimits(
            connect_timeout_seconds=self._settings.content_activity_http_connect_timeout_seconds,
            read_timeout_seconds=self._settings.content_activity_http_read_timeout_seconds,
            pool_timeout_seconds=self._settings.content_activity_http_pool_timeout_seconds,
            total_timeout_seconds=self._settings.content_activity_http_total_timeout_seconds,
            max_response_bytes=self._settings.content_activity_http_max_response_bytes,
        )

    async def _request(self, *, path: str, params: Mapping[str, str]) -> HttpTransportResponse:
        if self._transport is None:
            raise OSError("transport unavailable")
        token = self._settings.tikhub_api_key
        token_value = token.get_secret_value() if token is not None else ""
        if not token_value.strip():
            raise OSError("token unavailable")
        return await self._transport.get(
            path=path,
            params=params,
            headers={"Authorization": f"Bearer {token_value}"},
            limits=self._limits,
        )


def parse_xhs_user_info_response(response_body: bytes) -> str:
    """Parse the exact V1 identity resolver envelope and return its opaque userid."""

    root = _parse_json_object(response_body)
    _ensure_exact_keys(root, required={"code", "data"}, allowed={"code", "data"})
    _require_semantic_success(root["code"])
    outer_data = _require_mapping(root["data"], "DATA_ENVELOPE_INVALID")
    _ensure_exact_keys(outer_data, required={"data"}, allowed={"data"})
    payload = _require_mapping(outer_data["data"], "IDENTITY_PAYLOAD_INVALID")
    _ensure_exact_keys(payload, required={"userid"}, allowed={"userid"})
    userid = payload["userid"]
    if not _is_valid_opaque_identity(userid):
        raise XhsSchemaViolation("USERID_INVALID")
    return userid


def parse_xhs_posted_notes_response(
    response_body: bytes,
    *,
    observed_at: datetime,
) -> ParsedXhsPostedNotes:
    """Parse all V1 decision-bearing fields before any trusted MAX is calculated."""

    normalized_observed_at = _normalize_observed_at(observed_at)
    root = _parse_json_object(response_body)
    _ensure_exact_keys(root, required={"code", "data"}, allowed={"code", "data"})
    _require_semantic_success(root["code"])
    outer_data = _require_mapping(root["data"], "DATA_ENVELOPE_INVALID")
    _ensure_exact_keys(outer_data, required={"data"}, allowed={"data"})
    payload = _require_mapping(outer_data["data"], "ACTIVITY_PAYLOAD_INVALID")
    _ensure_exact_keys(payload, required={"has_more", "notes"}, allowed={"has_more", "notes"})

    has_more = payload["has_more"]
    if type(has_more) is not bool:
        raise XhsSchemaViolation("HAS_MORE_NOT_BOOLEAN")
    notes = payload["notes"]
    if not isinstance(notes, list):
        raise XhsSchemaViolation("NOTES_NOT_ARRAY")

    publications = tuple(
        _parse_note_entry(entry, observed_at=normalized_observed_at) for entry in notes
    )
    _assert_no_conflicting_duplicates(publications)
    return ParsedXhsPostedNotes(
        has_more=has_more,
        publications=_collapse_identical_duplicates(publications),
        raw_item_count=len(notes),
    )


def _parse_note_entry(entry: Any, *, observed_at: datetime) -> NormalizedXhsPublication:
    note_entry = _require_mapping(entry, "NOTE_ENTRY_INVALID")
    _ensure_exact_keys(note_entry, required={"note"}, allowed={"note", "cursor"})
    if "cursor" in note_entry and not _is_valid_opaque_identity(note_entry["cursor"]):
        raise XhsSchemaViolation("CURSOR_INVALID")

    note = _require_mapping(note_entry["note"], "NOTE_INVALID")
    _ensure_exact_keys(
        note,
        required={"note_id", "type", "create_time"},
        allowed={"note_id", "type", "create_time", "is_top"},
    )
    publication_id = note["note_id"]
    if not _is_valid_opaque_identity(publication_id):
        raise XhsSchemaViolation("NOTE_ID_INVALID")

    raw_type = note["type"]
    publication_type = _publication_type_for(raw_type)
    published_at = _parse_unix_seconds(note["create_time"], observed_at=observed_at)
    is_top = note.get("is_top", False)
    if type(is_top) is not bool:
        raise XhsSchemaViolation("IS_TOP_NOT_BOOLEAN")
    return NormalizedXhsPublication(
        publication_id=publication_id,
        publication_type=publication_type,
        published_at=published_at,
        is_top=is_top,
    )


def _assert_no_conflicting_duplicates(
    publications: tuple[NormalizedXhsPublication, ...],
) -> None:
    seen: dict[str, NormalizedXhsPublication] = {}
    for publication in publications:
        existing = seen.get(publication.publication_id)
        if existing is not None and existing != publication:
            raise XhsSchemaViolation("DUPLICATE_PUBLICATION_CONFLICT")
        seen[publication.publication_id] = publication


def _collapse_identical_duplicates(
    publications: tuple[NormalizedXhsPublication, ...],
) -> tuple[NormalizedXhsPublication, ...]:
    collapsed: dict[str, NormalizedXhsPublication] = {}
    for publication in publications:
        collapsed[publication.publication_id] = publication
    return tuple(collapsed.values())


def _publication_type_for(raw_type: Any) -> ContentActivityPublicationType:
    if raw_type == "normal":
        return ContentActivityPublicationType.IMAGE_TEXT
    if raw_type == "video":
        return ContentActivityPublicationType.VIDEO
    raise XhsSchemaViolation("CONTENT_TYPE_UNKNOWN")


def _parse_unix_seconds(value: Any, *, observed_at: datetime) -> datetime:
    if type(value) is not int or value <= 0:
        raise XhsSchemaViolation("CREATE_TIME_INVALID")
    try:
        published_at = datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise XhsSchemaViolation("CREATE_TIME_INVALID") from exc
    if published_at > observed_at:
        raise XhsSchemaViolation("CREATE_TIME_FUTURE")
    return published_at


def _parse_json_object(response_body: bytes) -> Mapping[str, Any]:
    try:
        decoded = response_body.decode("utf-8")
        parsed = json.loads(decoded, object_pairs_hook=_json_object_without_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, XhsSchemaViolation) as exc:
        raise XhsSchemaViolation("JSON_RESPONSE_INVALID") from exc
    return _require_mapping(parsed, "ROOT_NOT_OBJECT")


def _json_object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise XhsSchemaViolation("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _require_semantic_success(value: Any) -> None:
    if type(value) is not int or value != XHS_SEMANTIC_SUCCESS_CODE:
        raise XhsSemanticFailure("SEMANTIC_CODE_REJECTED")


def _require_mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise XhsSchemaViolation(code)
    return value


def _ensure_exact_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    allowed: set[str],
) -> None:
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(allowed):
        raise XhsSchemaViolation("SCHEMA_KEYS_UNRECOGNIZED")


def _is_valid_bootstrap_input(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= 2_048
        and not any(ord(character) < 32 and character not in "\t\r\n" for character in value)
    )


def _is_valid_opaque_identity(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and _has_safe_opaque_text(value, max_length=256)


def _has_safe_opaque_text(value: str, *, max_length: int) -> bool:
    return (
        bool(value)
        and value == value.strip()
        and len(value) <= max_length
        and not any(character.isspace() or ord(character) < 32 for character in value)
    )


def _normalize_observed_at(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _identity_http_failure(status_code: int) -> XhsIdentityResolution | None:
    if 200 <= status_code < 300:
        return None
    if status_code in (401, 403):
        return _identity_failure(
            observation_status=ContentActivityObservationStatus.PROVIDER_AUTH_ERROR,
            error_class=ContentActivityProviderErrorClass.AUTHENTICATION,
            error_code="HTTP_AUTH_FAILURE",
            terminal_reason=ContentActivityScanTerminalReason.PROVIDER_FAILURE,
            request_count=1,
        )
    if status_code == 429:
        return _identity_failure(
            observation_status=ContentActivityObservationStatus.PROVIDER_RATE_LIMITED,
            error_class=ContentActivityProviderErrorClass.RATE_LIMITED,
            error_code="HTTP_RATE_LIMITED",
            terminal_reason=ContentActivityScanTerminalReason.PROVIDER_FAILURE,
            request_count=1,
        )
    return _identity_failure(
        observation_status=ContentActivityObservationStatus.PROVIDER_ERROR,
        error_class=ContentActivityProviderErrorClass.TRANSPORT,
        error_code=_safe_http_error_code(status_code),
        terminal_reason=ContentActivityScanTerminalReason.PROVIDER_FAILURE,
        request_count=1,
    )


def _activity_http_failure(status_code: int, observed_at: datetime) -> XhsActivityAttempt | None:
    if 200 <= status_code < 300:
        return None
    if status_code in (401, 403):
        return _activity_failure(
            observed_at=observed_at,
            observation_status=ContentActivityObservationStatus.PROVIDER_AUTH_ERROR,
            coverage_status=ContentActivityCoverageStatus.UNKNOWN,
            error_class=ContentActivityProviderErrorClass.AUTHENTICATION,
            error_code="HTTP_AUTH_FAILURE",
            terminal_reason=ContentActivityScanTerminalReason.PROVIDER_FAILURE,
            request_count=1,
        )
    if status_code == 429:
        return _activity_failure(
            observed_at=observed_at,
            observation_status=ContentActivityObservationStatus.PROVIDER_RATE_LIMITED,
            coverage_status=ContentActivityCoverageStatus.UNKNOWN,
            error_class=ContentActivityProviderErrorClass.RATE_LIMITED,
            error_code="HTTP_RATE_LIMITED",
            terminal_reason=ContentActivityScanTerminalReason.PROVIDER_FAILURE,
            request_count=1,
        )
    return _activity_failure(
        observed_at=observed_at,
        observation_status=ContentActivityObservationStatus.PROVIDER_ERROR,
        coverage_status=ContentActivityCoverageStatus.UNKNOWN,
        error_class=ContentActivityProviderErrorClass.TRANSPORT,
        error_code=_safe_http_error_code(status_code),
        terminal_reason=ContentActivityScanTerminalReason.PROVIDER_FAILURE,
        request_count=1,
    )


def _safe_http_error_code(status_code: int) -> str:
    if 300 <= status_code < 400:
        return "HTTP_REDIRECT_REJECTED"
    if 400 <= status_code < 500:
        return "HTTP_CLIENT_FAILURE"
    if 500 <= status_code < 600:
        return "HTTP_SERVER_FAILURE"
    return "HTTP_STATUS_INVALID"


def _identity_failure(
    *,
    observation_status: ContentActivityObservationStatus,
    error_class: ContentActivityProviderErrorClass,
    error_code: str,
    terminal_reason: ContentActivityScanTerminalReason,
    request_count: int = 0,
) -> XhsIdentityResolution:
    return XhsIdentityResolution(
        observation_status=observation_status,
        userid=None,
        provider_error_class=error_class,
        provider_error_code=error_code,
        terminal_reason=terminal_reason,
        request_count=request_count,
    )


def _activity_failure(
    *,
    observed_at: datetime,
    observation_status: ContentActivityObservationStatus,
    coverage_status: ContentActivityCoverageStatus,
    error_class: ContentActivityProviderErrorClass | None,
    error_code: str | None,
    terminal_reason: ContentActivityScanTerminalReason,
    item_count: int = 0,
    request_count: int = 0,
) -> XhsActivityAttempt:
    return XhsActivityAttempt(
        observation_status=observation_status,
        coverage_status=coverage_status,
        activity_result=ContentActivityResult.UNDETERMINED,
        observed_at=observed_at,
        last_publication_at=None,
        latest_publication_id=None,
        latest_publication_type=None,
        co_latest_publication_count=None,
        provider_error_class=error_class,
        provider_error_code=error_code,
        terminal_reason=terminal_reason,
        item_count=item_count,
        request_count=request_count,
    )

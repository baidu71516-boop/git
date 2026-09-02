"""Transactional identity, observation, and durable refresh orchestration.

This service intentionally owns no broad provider/job framework.  It converts
one manually requested, account-scoped XHS check into immutable evidence and
two independent projections, using PostgreSQL locks and bounded retry state.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from secrets import token_urlsafe
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.audit.repository import AuditRepository
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
)
from backend_core.content_activity.enums import (
    ContentActivityCoverageStatus,
    ContentActivityObservationStatus,
    ContentActivityProvider,
    ContentActivityProviderErrorClass,
    ContentActivityRefreshRequestState,
    ContentActivityResult,
    ContentActivityScanTerminalReason,
    ContentActivitySemantics,
    ProviderAccountIdentityNamespace,
    ProviderAccountIdentityVerificationOutcome,
    ProviderAccountIdentityVerificationState,
)
from backend_core.content_activity.huitun_douyin import (
    HUITUN_DOUYIN_ADAPTER_VERSION,
    HUITUN_DOUYIN_CAPABILITY_POLICY_VERSION,
    HUITUN_DOUYIN_ENDPOINT,
    HUITUN_DOUYIN_ENDPOINT_VERSION,
    HUITUN_DOUYIN_IDENTITY_CONTRACT_VERSION,
    HUITUN_DOUYIN_IDENTITY_SOURCE,
    HUITUN_DOUYIN_PROVENANCE_REF,
    HUITUN_DOUYIN_PROVIDER_PRODUCT,
    HUITUN_DOUYIN_PUBLICATION_KEY_NAMESPACE,
    HUITUN_DOUYIN_RESPONSE_SCHEMA_VERSION,
    HUITUN_DOUYIN_SOURCE_TIMEZONE,
    HUITUN_DOUYIN_TIMESTAMP_ENCODING,
    HUITUN_DOUYIN_VISIBILITY_POLICY_VERSION,
    HuitunDouyinRuntimeAttempt,
    normalize_douyin_runtime_capture,
)
from backend_core.content_activity.models import (
    ContentActivityObservation,
    ContentActivityProjection,
    ContentActivityRefreshRequest,
    ProviderAccountIdentity,
    ProviderAccountIdentityVerification,
)
from backend_core.content_activity.projection import (
    apply_observation,
    invalidate_trusted_current,
    is_trusted_current_public,
)
from backend_core.content_activity.repository import ContentActivityRepository
from backend_core.content_activity.schemas import (
    ContentActivityRefreshRequestPublic,
    DouyinRuntimeCaptureIngestInput,
    DouyinRuntimeCaptureIngestPublic,
    DouyinRuntimeCaptureLaunchPublic,
    XhsIdentityResolutionPublic,
)
from backend_core.content_activity.tikhub_xhs import (
    TikHubXhsClient,
    XhsActivityAttempt,
    XhsIdentityResolution,
)
from backend_core.growth.idempotency import validate_idempotency_key
from backend_core.imports.hashing import advisory_lock_key
from backend_core.influencers.enums import Platform
from backend_core.influencers.models import InfluencerPlatformAccount

IDENTITY_SOURCE = "TIKHUB_XHS_APP_V2"
IDENTITY_PROVENANCE_REF = "tikhub_xhs_app_v2_user_info_v1"
OBSERVATION_PROVENANCE_REF = "tikhub_xhs_app_v2_activity_v1"
NOTE_ID_NAMESPACE = "xiaohongshu.noteid"
ADAPTER_VERSION = "TIKHUB_XHS_CONTENT_ACTIVITY_ADAPTER_V1"
ENDPOINT_VERSION = "APP_V2"
# A non-blocking, database-global slot enforces the frozen D1A one-call
# concurrency even if more than one analytics worker process consumes the queue.
PROVIDER_CALL_SLOT_LOCK_KEY = advisory_lock_key("content-activity:xhs-provider-call-slot:v1")
DOUYIN_RUNTIME_CAPTURE_TTL = timedelta(minutes=10)


class ContentActivityError(Exception):
    """A deliberately safe domain failure for HTTP and worker adapters."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class RefreshPolicy:
    """Finite durable-delivery parameters, separate from provider HTTP policy."""

    max_attempts: int = 3
    lease_seconds: int = 120
    retry_backoff_base_seconds: int = 5
    retry_backoff_max_seconds: int = 300
    reconcile_batch_size: int = 100

    @classmethod
    def from_settings(cls, settings: Settings) -> RefreshPolicy:
        return cls(
            max_attempts=settings.content_activity_refresh_max_attempts,
            lease_seconds=settings.content_activity_refresh_lease_seconds,
            retry_backoff_base_seconds=settings.content_activity_refresh_retry_backoff_base_seconds,
            retry_backoff_max_seconds=settings.content_activity_refresh_retry_backoff_max_seconds,
            reconcile_batch_size=settings.content_activity_refresh_reconcile_batch_size,
        )

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.lease_seconds < 2 or self.reconcile_batch_size < 1:
            raise ValueError("Content Activity refresh policy is invalid")
        if self.retry_backoff_base_seconds < 1 or (
            self.retry_backoff_base_seconds > self.retry_backoff_max_seconds
        ):
            raise ValueError("Content Activity refresh backoff policy is invalid")


@dataclass(frozen=True, slots=True)
class RefreshClaim:
    request_token: UUID
    platform_account_id: UUID
    lease_generation: int
    attempt_started_at: datetime


@dataclass(frozen=True, slots=True)
class RefreshProcessResult:
    request_token: UUID
    state: ContentActivityRefreshRequestState | None
    observation_id: UUID | None
    claimed: bool


class ContentActivityService:
    """Own Content Activity writes while keeping raw provider values out of storage."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        client: TikHubXhsClient | None = None,
        refresh_policy: RefreshPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.repository = ContentActivityRepository(session)
        self.client = client or TikHubXhsClient(settings)
        self.refresh_policy = refresh_policy or RefreshPolicy.from_settings(settings)
        self.clock = clock

    async def aclose(self) -> None:
        """Close only the optional provider client owned by this service."""

        await self.client.aclose()

    async def resolve_xhs_identity(
        self,
        *,
        platform_account_id: UUID,
        bootstrap_input: str,
        idempotency_key: str,
        operator_id: UUID | None,
        department_id: UUID | None,
        ip: str,
        user_agent: str,
    ) -> XhsIdentityResolutionPublic:
        """Resolve and atomically bind one canonical XHS userid.

        Bootstrap text is used only in the provider call below.  It never enters
        a task record, Audit payload, identity provenance, exception message, or
        return DTO.
        """

        self._require_provider_enabled()
        account = await self._require_xhs_account(platform_account_id)
        event_key = _scoped_idempotency_key("identity", account.id, idempotency_key)

        replay = await self._verification_replay(event_key)
        if replay is not None:
            binding, event = replay
            if binding.platform_account_id != account.id:
                raise ContentActivityError(
                    409,
                    "IDEMPOTENCY_KEY_REUSED",
                    "Idempotency-Key was already used for a different request",
                )
            return XhsIdentityResolutionPublic(
                platform_account_id=account.id,
                identity_binding_id=binding.id,
                verification_event_id=event.id,
                outcome=(
                    "VERIFIED_CURRENT" if _is_policy_accepted_identity(binding) else "UNTRUSTED"
                ),
            )

        current = await self.repository.get_current_identity(
            platform_account_id=account.id,
            namespace=ProviderAccountIdentityNamespace.XIAOHONGSHU_USERID,
        )
        if current is not None:
            verification_id = await self._latest_verification_id(current.id)
            return XhsIdentityResolutionPublic(
                platform_account_id=account.id,
                identity_binding_id=current.id,
                verification_event_id=verification_id,
                outcome=(
                    "VERIFIED_CURRENT"
                    if _is_policy_accepted_identity(current) and verification_id is not None
                    else "UNTRUSTED"
                ),
            )

        resolution = await self.client.resolve_user(bootstrap_input=bootstrap_input)
        if not resolution.usable:
            return XhsIdentityResolutionPublic(
                platform_account_id=account.id,
                identity_binding_id=None,
                verification_event_id=None,
                outcome=(
                    "IDENTITY_UNRESOLVED"
                    if resolution.observation_status
                    is ContentActivityObservationStatus.IDENTITY_UNRESOLVED
                    else "UNTRUSTED"
                ),
            )
        if not _is_accepted_xhs_identity_resolution(resolution):
            return XhsIdentityResolutionPublic(
                platform_account_id=account.id,
                identity_binding_id=None,
                verification_event_id=None,
                outcome="UNTRUSTED",
            )
        assert resolution.userid is not None

        now = await self._current_time()
        try:
            locked_account = await self._require_xhs_account(account.id, for_update=True)
            current = await self.repository.get_current_identity(
                platform_account_id=locked_account.id,
                namespace=ProviderAccountIdentityNamespace.XIAOHONGSHU_USERID,
                for_update=True,
            )
            if current is not None:
                # Another serialized resolver already bound this account.  A
                # mismatch is never silently reconciled with the fresh response.
                if current.opaque_external_identity != resolution.userid:
                    await self._record_identity_conflict(
                        account_id=locked_account.id,
                        operator_id=operator_id,
                        department_id=department_id,
                        ip=ip,
                        user_agent=user_agent,
                    )
                    await self.session.commit()
                    raise ContentActivityError(
                        409, "IDENTITY_CONFLICT", "Canonical identity binding conflicts"
                    )
                verification_id = await self._latest_verification_id(current.id)
                return XhsIdentityResolutionPublic(
                    platform_account_id=locked_account.id,
                    identity_binding_id=current.id,
                    verification_event_id=verification_id,
                    outcome=(
                        "VERIFIED_CURRENT"
                        if _is_policy_accepted_identity(current) and verification_id is not None
                        else "UNTRUSTED"
                    ),
                )

            if (
                locked_account.platform_account_id is not None
                and locked_account.platform_account_id != resolution.userid
            ):
                await self._record_identity_conflict(
                    account_id=locked_account.id,
                    operator_id=operator_id,
                    department_id=department_id,
                    ip=ip,
                    user_agent=user_agent,
                )
                await self.session.commit()
                raise ContentActivityError(
                    409, "IDENTITY_CONFLICT", "Canonical identity binding conflicts"
                )

            owner = await self._identity_owner(resolution.userid, for_update=True)
            if owner is not None:
                if owner.platform_account_id == locked_account.id and (
                    owner.verification_state
                    is ProviderAccountIdentityVerificationState.VERIFIED_CURRENT
                ):
                    # This only occurs after a race/transaction boundary.  It is
                    # safe to use the already-current row, but we append the
                    # idempotent event so evidence remains traceable.
                    if not _is_policy_accepted_identity(owner):
                        await self.session.rollback()
                        return XhsIdentityResolutionPublic(
                            platform_account_id=locked_account.id,
                            identity_binding_id=owner.id,
                            verification_event_id=None,
                            outcome="UNTRUSTED",
                        )
                    event = await self._append_verification(
                        owner,
                        verified_at=now,
                        idempotency_key=event_key,
                    )
                    await self._record_identity_verified(
                        account_id=locked_account.id,
                        binding_id=owner.id,
                        operator_id=operator_id,
                        department_id=department_id,
                        ip=ip,
                        user_agent=user_agent,
                    )
                    await self.session.commit()
                    return XhsIdentityResolutionPublic(
                        platform_account_id=locked_account.id,
                        identity_binding_id=owner.id,
                        verification_event_id=event.id,
                        outcome="VERIFIED_CURRENT",
                    )
                await self._record_identity_conflict(
                    account_id=locked_account.id,
                    operator_id=operator_id,
                    department_id=department_id,
                    ip=ip,
                    user_agent=user_agent,
                )
                await self.session.commit()
                raise ContentActivityError(
                    409,
                    "IDENTITY_CONFLICT",
                    "Canonical identity binding conflicts",
                )

            binding = ProviderAccountIdentity(
                platform_account_id=locked_account.id,
                platform=Platform.XIAOHONGSHU,
                namespace=ProviderAccountIdentityNamespace.XIAOHONGSHU_USERID,
                opaque_external_identity=resolution.userid,
                identity_source=IDENTITY_SOURCE,
                resolver_contract_version=resolution.schema_contract_id,
                verification_state=ProviderAccountIdentityVerificationState.VERIFIED_CURRENT,
                resolved_at=now,
                verified_at=now,
                provenance_ref=IDENTITY_PROVENANCE_REF,
                lock_version=1,
                superseded_at=None,
                revoked_at=None,
            )
            self.session.add(binding)
            await self.session.flush()
            event = await self._append_verification(
                binding,
                verified_at=now,
                idempotency_key=event_key,
            )
            await self._record_identity_verified(
                account_id=locked_account.id,
                binding_id=binding.id,
                operator_id=operator_id,
                department_id=department_id,
                ip=ip,
                user_agent=user_agent,
            )
            await self.session.commit()
            return XhsIdentityResolutionPublic(
                platform_account_id=locked_account.id,
                identity_binding_id=binding.id,
                verification_event_id=event.id,
                outcome="VERIFIED_CURRENT",
            )
        except ContentActivityError:
            if self.session.in_transaction():
                await self.session.rollback()
            raise
        except IntegrityError as exc:
            await self.session.rollback()
            await self._record_identity_conflict(
                account_id=account.id,
                operator_id=operator_id,
                department_id=department_id,
                ip=ip,
                user_agent=user_agent,
            )
            await self.session.commit()
            raise ContentActivityError(
                409, "IDENTITY_CONFLICT", "Canonical identity binding conflicts"
            ) from exc

    async def supersede_xhs_identity(
        self,
        *,
        platform_account_id: UUID,
        expected_current_identity_id: UUID,
        expected_current_lock_version: int,
        bootstrap_input: str,
        idempotency_key: str,
        operator_id: UUID | None,
        department_id: UUID | None,
        ip: str,
        user_agent: str,
    ) -> tuple[ProviderAccountIdentity, ProviderAccountIdentityVerification]:
        """Perform the frozen explicit CAS supersession primitive.

        No HTTP route exposes this correction workflow in D1A. The candidate
        value must come from the same allowlisted resolver as an initial bind;
        callers cannot self-assert an opaque userid into trusted lineage.
        """

        self._require_provider_enabled()
        if expected_current_lock_version < 1:
            raise ContentActivityError(409, "IDENTITY_CONFLICT", "Identity version is stale")
        resolution = await self.client.resolve_user(bootstrap_input=bootstrap_input)
        if not _is_accepted_xhs_identity_resolution(resolution) or resolution.userid is None:
            raise ContentActivityError(
                422,
                "IDENTITY_UNRESOLVED",
                "Replacement canonical identity was not verified",
            )
        replacement_userid = resolution.userid
        account = await self._require_xhs_account(platform_account_id, for_update=True)
        current = await self.repository.get_current_identity(
            platform_account_id=account.id,
            namespace=ProviderAccountIdentityNamespace.XIAOHONGSHU_USERID,
            for_update=True,
        )
        if (
            current is None
            or current.id != expected_current_identity_id
            or current.lock_version != expected_current_lock_version
            or not _is_policy_accepted_identity(current)
        ):
            await self.session.rollback()
            raise ContentActivityError(409, "IDENTITY_CONFLICT", "Identity version is stale")
        owner = await self._identity_owner(replacement_userid, for_update=True)
        if owner is not None:
            await self.session.rollback()
            raise ContentActivityError(
                409,
                "IDENTITY_CONFLICT",
                "Canonical identity binding conflicts",
            )
        if current.opaque_external_identity == replacement_userid:
            await self.session.rollback()
            raise ContentActivityError(
                409,
                "IDENTITY_REPLACEMENT_SAME_VALUE",
                "Replacement must differ from the current canonical identity",
            )
        now = await self._current_time()
        projection = await self.repository.get_projection(account.id, for_update=True)
        current.verification_state = ProviderAccountIdentityVerificationState.SUPERSEDED
        current.superseded_at = now
        current.lock_version += 1
        await self.session.flush()
        if projection is not None:
            # A trusted current-public fact is inseparable from the exact
            # VERIFIED_CURRENT identity binding used to obtain it.  Keeping it
            # after a CAS replacement would let a former userid influence sales
            # reads/targeting during the freshness window.  Preserve history and
            # latest-attempt diagnostics, but require a fresh observation for
            # the replacement binding.
            invalidate_trusted_current(projection)
        replacement = ProviderAccountIdentity(
            platform_account_id=account.id,
            platform=Platform.XIAOHONGSHU,
            namespace=ProviderAccountIdentityNamespace.XIAOHONGSHU_USERID,
            opaque_external_identity=replacement_userid,
            identity_source=IDENTITY_SOURCE,
            resolver_contract_version=TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1,
            verification_state=ProviderAccountIdentityVerificationState.VERIFIED_CURRENT,
            resolved_at=now,
            verified_at=now,
            provenance_ref=IDENTITY_PROVENANCE_REF,
            lock_version=1,
            superseded_at=None,
            revoked_at=None,
        )
        self.session.add(replacement)
        await self.session.flush()
        event = await self._append_verification(
            replacement,
            verified_at=now,
            idempotency_key=_scoped_idempotency_key(
                "identity-replacement",
                account.id,
                idempotency_key,
            ),
        )
        AuditRepository(self.session).add(
            action=AuditAction.CONTENT_ACTIVITY_IDENTITY_SUPERSEDED,
            result=AuditResult.SUCCESS,
            department_id=department_id,
            operator_id=operator_id,
            ip=ip[:64],
            user_agent=user_agent[:512],
            entity_type="provider_account_identity",
            entity_id=replacement.id,
            after={
                "platform_account_id": str(account.id),
                "previous_identity_binding_id": str(current.id),
                "namespace": "xiaohongshu.userid",
                "lock_version": replacement.lock_version,
                "trusted_projection_invalidated": projection is not None,
            },
        )
        await self.session.commit()
        return replacement, event

    async def create_douyin_runtime_capture(
        self,
        *,
        platform_account_id: UUID,
        idempotency_key: str,
        operator_id: UUID | None,
        department_id: UUID | None,
        ip: str,
        user_agent: str,
    ) -> DouyinRuntimeCaptureLaunchPublic:
        """Create one short-lived, local-browser capture capability.

        This is intentionally not a provider refresh.  It makes no Huitun or
        TikHub request and remains available while all provider feature flags
        are off.  Only a digest of the generated one-time bridge token enters
        persistence.
        """

        try:
            validate_idempotency_key(idempotency_key)
        except ValueError as exc:
            raise ContentActivityError(
                422,
                "IDEMPOTENCY_KEY_INVALID",
                "Idempotency-Key is invalid",
            ) from exc

        now = await self._current_time()
        try:
            account = await self._require_douyin_account(platform_account_id, for_update=True)
            stored_key = _scoped_idempotency_key(
                "douyin-runtime-capture", account.id, idempotency_key
            )
            existing = await self._request_by_account_key(
                account.id,
                stored_key,
                for_update=True,
            )
            if existing is not None:
                # A raw one-time token is intentionally never persisted, so it
                # cannot be replayed after the original response is lost.
                raise ContentActivityError(
                    409,
                    "CAPTURE_TOKEN_ALREADY_ISSUED",
                    "Create a new capture request after the prior token expires",
                )
            active = await self._active_request_for_account(account.id, for_update=True)
            if active is not None:
                if (
                    active.capture_token_digest is not None
                    and active.lease_expires_at is not None
                    and _ledger_timestamp_utc(active.lease_expires_at) <= now
                ):
                    active.state = ContentActivityRefreshRequestState.FAILED
                    active.finished_at = now
                    active.lease_expires_at = None
                    active.next_attempt_at = None
                    active.last_error_code = "CAPTURE_EXPIRED"
                    active.capture_token_digest = None
                    self._audit_refresh_settlement(active, failed=True)
                    await self.session.flush()
                else:
                    raise ContentActivityError(
                        409,
                        "CAPTURE_ALREADY_ACTIVE",
                        "An active Content Activity request already exists for this account",
                    )

            capture_token = token_urlsafe(32)
            expires_at = now + DOUYIN_RUNTIME_CAPTURE_TTL
            request = ContentActivityRefreshRequest(
                platform_account_id=account.id,
                platform=Platform.DOUYIN,
                requested_by_operator_id=operator_id,
                request_token=uuid4(),
                idempotency_key=stored_key,
                state=ContentActivityRefreshRequestState.RUNNING,
                attempt_count=1,
                max_attempts=1,
                next_attempt_at=None,
                lease_generation=1,
                lease_expires_at=expires_at,
                started_at=now,
                finished_at=None,
                last_observation_id=None,
                last_error_code=None,
                capture_token_digest=sha256(capture_token.encode("utf-8")).hexdigest(),
            )
            self.session.add(request)
            await self.session.flush()
            AuditRepository(self.session).add(
                action=AuditAction.CONTENT_ACTIVITY_REFRESH_REQUESTED,
                result=AuditResult.SUCCESS,
                department_id=department_id,
                operator_id=operator_id,
                ip=ip[:64],
                user_agent=user_agent[:512],
                entity_type="content_activity_refresh_request",
                entity_id=request.id,
                after={
                    "platform_account_id": str(account.id),
                    "request_token": str(request.request_token),
                    "state": request.state.value,
                    "capture_kind": "HUITUN_DOUYIN_RUNTIME",
                },
            )
            await self.session.commit()
            return DouyinRuntimeCaptureLaunchPublic(
                capture_request_id=request.request_token,
                capture_token=capture_token,
                expires_at=expires_at,
                ingest_path="/api/v1/admin/content-activity/douyin/runtime-captures/ingest",
            )
        except ContentActivityError:
            await self.session.rollback()
            raise
        except IntegrityError as exc:
            await self.session.rollback()
            raise ContentActivityError(
                409,
                "CAPTURE_CONFLICT",
                "Runtime capture request conflicts",
            ) from exc

    async def ingest_douyin_runtime_capture(
        self,
        *,
        capture_token: str,
        payload: DouyinRuntimeCaptureIngestInput,
    ) -> DouyinRuntimeCaptureIngestPublic:
        """Persist one extension-emitted semantic result under a single-use token."""

        if not capture_token or len(capture_token) > 512:
            raise ContentActivityError(401, "CAPTURE_TOKEN_INVALID", "Capture token is invalid")

        # Resolve the account before taking any row lock so the authoritative
        # order stays account -> request -> identity, matching the existing
        # Content Activity write paths.
        untrusted_request = await self._request_by_token(payload.capture_request_id)
        if (
            untrusted_request is None
            or untrusted_request.capture_token_digest is None
            or not compare_digest(
                sha256(capture_token.encode("utf-8")).hexdigest(),
                untrusted_request.capture_token_digest,
            )
        ):
            await self.session.rollback()
            raise ContentActivityError(401, "CAPTURE_TOKEN_INVALID", "Capture token is invalid")

        account = await self.repository.get_platform_account(
            untrusted_request.platform_account_id,
            for_update=True,
        )
        request = await self._request_by_token(payload.capture_request_id, for_update=True)
        if request is None or request.capture_token_digest is None:
            await self.session.rollback()
            raise ContentActivityError(
                409,
                "CAPTURE_REQUEST_MISMATCH",
                "Capture request is unavailable",
            )
        if not compare_digest(
            sha256(capture_token.encode("utf-8")).hexdigest(), request.capture_token_digest
        ):
            await self.session.rollback()
            raise ContentActivityError(401, "CAPTURE_TOKEN_INVALID", "Capture token is invalid")

        now = await self._current_time()
        if request.state is not ContentActivityRefreshRequestState.RUNNING:
            await self.session.rollback()
            raise ContentActivityError(
                409,
                "CAPTURE_ALREADY_SETTLED",
                "Capture request is already settled",
            )
        if (
            request.lease_expires_at is None
            or _ledger_timestamp_utc(request.lease_expires_at) <= now
        ):
            request.state = ContentActivityRefreshRequestState.FAILED
            request.finished_at = now
            request.lease_expires_at = None
            request.next_attempt_at = None
            request.last_error_code = "CAPTURE_EXPIRED"
            request.capture_token_digest = None
            self._audit_refresh_settlement(request, failed=True)
            await self.session.commit()
            raise ContentActivityError(410, "CAPTURE_EXPIRED", "Capture request expired")

        if account is None or account.platform is not Platform.DOUYIN or not account.is_active:
            attempt = _douyin_untrusted_attempt(
                payload.actual_uid,
                error_class=ContentActivityProviderErrorClass.POLICY_REJECTED,
                error_code="ACCOUNT_NOT_PERMITTED",
            )
            identity = None
            verification_id = None
        else:
            attempt = normalize_douyin_runtime_capture(payload, observed_at=now)
            identity: ProviderAccountIdentity | None = None
            verification_id: UUID | None = None
            if attempt.is_accepted_semantic_result:
                identity, verification_id = await self._bind_douyin_runtime_identity(
                    account=account,
                    uid=attempt.actual_uid,
                    verified_at=now,
                    request_token=request.request_token,
                    operator_id=request.requested_by_operator_id,
                )
                if identity is None or verification_id is None:
                    attempt = _douyin_untrusted_attempt(
                        attempt.actual_uid,
                        error_class=ContentActivityProviderErrorClass.IDENTITY_CONFLICT,
                        error_code="IDENTITY_CONFLICT",
                    )

        observation = _douyin_observation_from_attempt(
            account_id=request.platform_account_id,
            identity_id=identity.id if identity is not None else None,
            verification_id=verification_id,
            attempt_started_at=(
                _ledger_timestamp_utc(request.started_at) if request.started_at is not None else now
            ),
            request_token=request.request_token,
            runtime_request_id=payload.runtime_request_id,
            observed_at=now,
            attempt=attempt,
        )
        self.session.add(observation)
        await self.session.flush()
        projection = await self.repository.get_projection(
            request.platform_account_id,
            for_update=True,
        )
        self.session.add(apply_observation(projection, observation))

        request.last_observation_id = observation.id
        request.finished_at = now
        request.lease_expires_at = None
        request.next_attempt_at = None
        request.last_error_code = attempt.provider_error_code
        # A settled capability can never be used again. Retain no
        # authentication material—raw or digest—after its terminal audit and
        # immutable observation have been recorded.
        request.capture_token_digest = None
        request.state = (
            ContentActivityRefreshRequestState.SUCCEEDED
            if (
                attempt.is_accepted_semantic_result
                and identity is not None
                and verification_id is not None
            )
            else ContentActivityRefreshRequestState.FAILED
        )
        self._audit_refresh_settlement(
            request,
            failed=request.state is ContentActivityRefreshRequestState.FAILED,
        )
        await self.session.commit()
        return DouyinRuntimeCaptureIngestPublic(
            capture_request_id=request.request_token,
            observation_id=observation.id,
            outcome=(
                "ACCEPTED"
                if request.state is ContentActivityRefreshRequestState.SUCCEEDED
                else "UNKNOWN"
            ),
        )

    async def create_xhs_refresh_requests(
        self,
        *,
        platform_account_ids: Iterable[UUID],
        idempotency_key: str,
        operator_id: UUID | None,
        department_id: UUID | None,
        ip: str,
        user_agent: str,
    ) -> tuple[ContentActivityRefreshRequestPublic, ...]:
        """Create one bounded native request per exact XHS platform account."""

        self._require_provider_enabled()
        account_ids = tuple(platform_account_ids)
        effective_batch_limit = min(
            self.settings.content_activity_refresh_batch_size,
            self.settings.content_activity_provider_max_calls_per_run,
        )
        if not account_ids or len(account_ids) > effective_batch_limit:
            raise ContentActivityError(
                422,
                "REFRESH_BATCH_INVALID",
                "Refresh batch size is invalid",
            )
        if len(account_ids) != len(set(account_ids)):
            raise ContentActivityError(
                422,
                "REFRESH_BATCH_INVALID",
                "Refresh batch contains duplicates",
            )
        try:
            validate_idempotency_key(idempotency_key)
        except ValueError as exc:
            raise ContentActivityError(
                422,
                "IDEMPOTENCY_KEY_INVALID",
                "Idempotency-Key is invalid",
            ) from exc

        now = await self._current_time()
        result_by_account: dict[UUID, ContentActivityRefreshRequestPublic] = {}
        try:
            # Always obtain account row locks in one global order. Responses are
            # rebuilt in caller order below, so this cannot make overlap races
            # depend on the client-supplied batch ordering.
            for account_id in sorted(account_ids, key=str):
                account = await self._require_xhs_account(account_id, for_update=True)
                stored_key = _scoped_idempotency_key("refresh", account.id, idempotency_key)
                existing = await self._request_by_account_key(
                    account.id,
                    stored_key,
                    for_update=True,
                )
                if existing is not None:
                    result_by_account[account.id] = _refresh_public(existing)
                    continue
                active = await self._active_request_for_account(account.id, for_update=True)
                if active is not None:
                    result_by_account[account.id] = _refresh_public(active)
                    continue
                request = ContentActivityRefreshRequest(
                    platform_account_id=account.id,
                    platform=Platform.XIAOHONGSHU,
                    requested_by_operator_id=operator_id,
                    request_token=uuid4(),
                    idempotency_key=stored_key,
                    state=ContentActivityRefreshRequestState.PENDING,
                    attempt_count=0,
                    max_attempts=self.refresh_policy.max_attempts,
                    next_attempt_at=now,
                    lease_generation=0,
                    lease_expires_at=None,
                    started_at=None,
                    finished_at=None,
                    last_observation_id=None,
                    last_error_code=None,
                )
                self.session.add(request)
                await self.session.flush()
                AuditRepository(self.session).add(
                    action=AuditAction.CONTENT_ACTIVITY_REFRESH_REQUESTED,
                    result=AuditResult.SUCCESS,
                    department_id=department_id,
                    operator_id=operator_id,
                    ip=ip[:64],
                    user_agent=user_agent[:512],
                    entity_type="content_activity_refresh_request",
                    entity_id=request.id,
                    after={
                        "platform_account_id": str(account.id),
                        "request_token": str(request.request_token),
                        "state": request.state.value,
                    },
                )
                result_by_account[account.id] = _refresh_public(request)
            await self.session.commit()
            return tuple(result_by_account[account_id] for account_id in account_ids)
        except ContentActivityError:
            await self.session.rollback()
            raise
        except IntegrityError as exc:
            await self.session.rollback()
            raise ContentActivityError(
                409,
                "REFRESH_CONFLICT",
                "Refresh request conflicts",
            ) from exc

    async def claim_refresh_request(self, request_token: UUID) -> RefreshClaim | None:
        """Claim one due request with a lease; duplicate broker delivery is harmless."""

        request = await self._request_by_token(request_token, for_update=True)
        if request is None:
            await self.session.rollback()
            return None
        now = await self._current_time()
        if request.state in {
            ContentActivityRefreshRequestState.SUCCEEDED,
            ContentActivityRefreshRequestState.FAILED,
            ContentActivityRefreshRequestState.CANCELLED,
        }:
            await self.session.rollback()
            return None
        if request.state is ContentActivityRefreshRequestState.RUNNING:
            if request.lease_expires_at is not None and request.lease_expires_at > now:
                await self.session.rollback()
                return None
            if request.attempt_count >= request.max_attempts:
                request.state = ContentActivityRefreshRequestState.FAILED
                request.finished_at = now
                request.lease_expires_at = None
                request.next_attempt_at = None
                request.last_error_code = "LEASE_EXPIRED"
                self._audit_refresh_settlement(request, failed=True)
                await self.session.commit()
                return None
            request.state = ContentActivityRefreshRequestState.RETRY_WAIT
            request.lease_expires_at = None
            request.next_attempt_at = now + timedelta(
                seconds=self._retry_backoff(request.attempt_count)
            )
            request.last_error_code = "LEASE_EXPIRED"
            await self.session.commit()
            return None
        if request.next_attempt_at is None or request.next_attempt_at > now:
            await self.session.rollback()
            return None
        if request.attempt_count >= request.max_attempts:
            request.state = ContentActivityRefreshRequestState.FAILED
            request.finished_at = now
            request.lease_expires_at = None
            request.next_attempt_at = None
            request.last_error_code = "RETRY_EXHAUSTED"
            self._audit_refresh_settlement(request, failed=True)
            await self.session.commit()
            return None
        request.state = ContentActivityRefreshRequestState.RUNNING
        request.attempt_count += 1
        request.lease_generation += 1
        request.started_at = request.started_at or now
        request.lease_expires_at = now + timedelta(seconds=self.refresh_policy.lease_seconds)
        request.next_attempt_at = None
        await self.session.commit()
        return RefreshClaim(
            request_token=request.request_token,
            platform_account_id=request.platform_account_id,
            lease_generation=request.lease_generation,
            attempt_started_at=now,
        )

    async def process_refresh_request(self, request_token: UUID) -> RefreshProcessResult:
        """Claim, call at most one XHS endpoint, persist, then settle/retry safely."""

        # Governance can be revoked after a request was durably created. Settle
        # that old request to a non-trusted local observation instead of making a
        # provider call or leaving it indefinitely replayable under a disabled
        # feature flag.
        if not self._provider_enabled:
            claim = await self.claim_refresh_request(request_token)
            if claim is None:
                return RefreshProcessResult(request_token, None, None, claimed=False)
            return await self._persist_claimed_attempt(
                claim=claim,
                identity=None,
                verification_id=None,
                attempt=_policy_rejected_attempt(await self._current_time()),
            )

        async with self._try_provider_call_slot() as slot_acquired:
            # Do not claim a durable request while another process is executing
            # the single allowed provider call. The reconciler will republish its
            # still-PENDING token, without a lease-expiry race or extra call.
            if not slot_acquired:
                return RefreshProcessResult(request_token, None, None, claimed=False)

            claim = await self.claim_refresh_request(request_token)
            if claim is None:
                return RefreshProcessResult(request_token, None, None, claimed=False)

            # Lock the account through the only external call.  This prevents a
            # concurrent deactivation from racing a provider request that was
            # authorized by an already-stale account state.  The endpoint is
            # bounded by the adapter timeout policy, and all other write paths
            # take the account lock before a refresh-request lock as well.
            account = await self.repository.get_platform_account(
                claim.platform_account_id,
                for_update=True,
            )
            if account is None or account.platform is not Platform.XIAOHONGSHU:
                return await self._fail_claim_without_observation(
                    claim=claim,
                    error_code="ACCOUNT_NOT_PERMITTED",
                )
            if not account.is_active:
                return await self._persist_claimed_attempt(
                    claim=claim,
                    identity=None,
                    verification_id=None,
                    attempt=_account_inactive_attempt(await self._current_time()),
                )

            identity = await self.repository.get_current_identity(
                platform_account_id=claim.platform_account_id,
                namespace=ProviderAccountIdentityNamespace.XIAOHONGSHU_USERID,
                for_update=True,
            )
            verification_id: UUID | None = None
            if identity is None or not _is_policy_accepted_identity(identity):
                identity = None
                attempt = _identity_binding_not_accepted_attempt(await self._current_time())
            else:
                verification_id = await self._latest_verification_id(identity.id)
                if verification_id is None:
                    identity = None
                    attempt = _identity_unresolved_attempt(await self._current_time())
                else:
                    try:
                        attempt = await self.client.observe_posted_notes(
                            verified_userid=identity.opaque_external_identity
                        )
                    except Exception:
                        # The adapter maps all expected transport/schema paths.
                        # A future adapter bug or unrecognized client exception
                        # must still create one secret-free, non-trusted durable
                        # attempt instead of leaving a silently acknowledged
                        # worker delivery without observation evidence.
                        attempt = _unexpected_provider_attempt(await self._current_time())
                    if not _is_accepted_xhs_activity_attempt(attempt):
                        attempt = _untrusted_attempt(
                            await self._current_time(),
                            error_class=ContentActivityProviderErrorClass.SCHEMA_DRIFT,
                            error_code="ACTIVITY_CONTRACT_MISMATCH",
                        )

            return await self._persist_claimed_attempt(
                claim=claim,
                identity=identity,
                verification_id=verification_id,
                attempt=attempt,
            )

    async def reconcile_due_refresh_requests(self, *, limit: int | None = None) -> tuple[UUID, ...]:
        """Return a bounded set of ID-only messages eligible for safe republishing."""

        selected = await self.repository.list_due_refresh_requests(
            as_of=await self._current_time(),
            limit=limit or self.refresh_policy.reconcile_batch_size,
            for_update_skip_locked=True,
        )
        # No mutation is necessary here: duplicate publications are absorbed by
        # the request lease.  Keeping the due state makes a DB-before-publish
        # crash resume-friendly without a new queue topology.
        tokens = tuple(request.request_token for request in selected)
        await self.session.rollback()
        return tokens

    async def _persist_claimed_attempt(
        self,
        *,
        claim: RefreshClaim,
        identity: ProviderAccountIdentity | None,
        verification_id: UUID | None,
        attempt: XhsActivityAttempt,
    ) -> RefreshProcessResult:
        # Keep the lock order consistent with create/identity paths:
        # platform-account, refresh-request, then identity.  In particular,
        # process_refresh_request may already own the account row through its
        # bounded provider call.
        account = await self._xhs_account_for_settlement(
            claim.platform_account_id,
            for_update=True,
        )
        if account is None:
            return await self._fail_claim_without_observation(
                claim=claim,
                error_code="ACCOUNT_NOT_PERMITTED",
            )

        request = await self._request_by_token(claim.request_token, for_update=True)
        if (
            request is None
            or request.state is not ContentActivityRefreshRequestState.RUNNING
            or request.lease_generation != claim.lease_generation
        ):
            await self.session.rollback()
            return RefreshProcessResult(claim.request_token, None, None, claimed=False)
        now = await self._current_time()
        if request.lease_expires_at is None or request.lease_expires_at <= now:
            await self.session.rollback()
            return RefreshProcessResult(claim.request_token, None, None, claimed=False)

        locked_identity = await self.repository.get_current_identity(
            platform_account_id=account.id,
            namespace=ProviderAccountIdentityNamespace.XIAOHONGSHU_USERID,
            for_update=True,
        )
        # An already-local failure (missing identity, revoked governance, etc.)
        # is itself meaningful immutable evidence. Only replace a fetched
        # provider attempt when its once-current binding changed during that
        # external call.
        if identity is not None and (
            not _is_policy_accepted_identity(identity)
            or locked_identity is None
            or not _is_policy_accepted_identity(locked_identity)
            or locked_identity.id != identity.id
            or verification_id is None
        ):
            identity = None
            verification_id = None
            attempt = _identity_unresolved_attempt(now)

        normalized_attempt = _with_observed_at(attempt, now)
        observation = _observation_from_attempt(
            account_id=account.id,
            identity_id=identity.id if identity is not None else None,
            verification_id=verification_id,
            attempt_started_at=claim.attempt_started_at,
            request_token=request.request_token,
            attempt=normalized_attempt,
        )
        projection = await self.repository.get_projection(account.id, for_update=True)
        if _equal_observed_at_trusted_conflict(projection, observation):
            # Equal observation instants with materially different trusted
            # current-public facts cannot be ordered by ingestion. Quarantine
            # the later response as explicit untrusted evidence instead of
            # silently letting last writer win in either projection.
            normalized_attempt = _equal_observed_at_conflict_attempt(now)
            observation = _observation_from_attempt(
                account_id=account.id,
                identity_id=identity.id if identity is not None else None,
                verification_id=verification_id,
                attempt_started_at=claim.attempt_started_at,
                request_token=request.request_token,
                attempt=normalized_attempt,
            )
        self.session.add(observation)
        await self.session.flush()

        projection = apply_observation(projection, observation)
        if projection.id is None:  # SQLAlchemy transient projection only.
            self.session.add(projection)
        request.last_observation_id = observation.id
        request.last_error_code = normalized_attempt.provider_error_code
        request.lease_expires_at = None
        request.next_attempt_at = None

        retryable = _is_retryable(normalized_attempt)
        if retryable and request.attempt_count < request.max_attempts:
            request.state = ContentActivityRefreshRequestState.RETRY_WAIT
            request.next_attempt_at = now + timedelta(
                seconds=self._retry_backoff(request.attempt_count)
            )
        elif retryable:
            request.state = ContentActivityRefreshRequestState.FAILED
            request.finished_at = now
            self._audit_refresh_settlement(request, failed=True)
        else:
            request.state = ContentActivityRefreshRequestState.SUCCEEDED
            request.finished_at = now
            self._audit_refresh_settlement(request, failed=False)

        await self.session.commit()
        return RefreshProcessResult(
            request_token=request.request_token,
            state=request.state,
            observation_id=observation.id,
            claimed=True,
        )

    async def _fail_claim_without_observation(
        self,
        *,
        claim: RefreshClaim,
        error_code: str,
    ) -> RefreshProcessResult:
        """Terminally settle an impossible/stale account reference without a call.

        A normal inactive XHS account still receives a non-trusted local
        observation.  This smaller escape hatch is only for a referentially
        impossible missing/non-XHS account, where an XHS observation cannot be
        written without violating the composite platform foreign key.
        """

        request = await self._request_by_token(claim.request_token, for_update=True)
        if (
            request is None
            or request.state is not ContentActivityRefreshRequestState.RUNNING
            or request.lease_generation != claim.lease_generation
        ):
            await self.session.rollback()
            return RefreshProcessResult(claim.request_token, None, None, claimed=False)
        now = await self._current_time()
        if request.lease_expires_at is None or request.lease_expires_at <= now:
            await self.session.rollback()
            return RefreshProcessResult(claim.request_token, None, None, claimed=False)

        request.state = ContentActivityRefreshRequestState.FAILED
        request.finished_at = now
        request.lease_expires_at = None
        request.next_attempt_at = None
        request.last_error_code = error_code
        self._audit_refresh_settlement(request, failed=True)
        await self.session.commit()
        return RefreshProcessResult(
            request_token=request.request_token,
            state=request.state,
            observation_id=None,
            claimed=True,
        )

    async def _xhs_account_for_settlement(
        self,
        platform_account_id: UUID,
        *,
        for_update: bool = False,
    ) -> InfluencerPlatformAccount | None:
        """Return an XHS account for safe local settlement, even if inactive."""

        account = await self.repository.get_platform_account(
            platform_account_id,
            for_update=for_update,
        )
        if account is None or account.platform is not Platform.XIAOHONGSHU:
            return None
        return account

    async def _require_xhs_account(
        self,
        platform_account_id: UUID,
        *,
        for_update: bool = False,
    ) -> InfluencerPlatformAccount:
        account = await self.repository.get_platform_account(
            platform_account_id,
            for_update=for_update,
        )
        if account is None:
            raise ContentActivityError(
                404,
                "PLATFORM_ACCOUNT_NOT_FOUND",
                "Platform account was not found",
            )
        if account.platform is not Platform.XIAOHONGSHU or not account.is_active:
            raise ContentActivityError(
                422,
                "XHS_ACCOUNT_REQUIRED",
                "An active Xiaohongshu account is required",
            )
        return account

    async def _require_douyin_account(
        self,
        platform_account_id: UUID,
        *,
        for_update: bool = False,
    ) -> InfluencerPlatformAccount:
        account = await self.repository.get_platform_account(
            platform_account_id,
            for_update=for_update,
        )
        if account is None:
            raise ContentActivityError(
                404,
                "PLATFORM_ACCOUNT_NOT_FOUND",
                "Platform account was not found",
            )
        if account.platform is not Platform.DOUYIN or not account.is_active:
            raise ContentActivityError(
                422,
                "DOUYIN_ACCOUNT_REQUIRED",
                "An active Douyin account is required",
            )
        return account

    async def _bind_douyin_runtime_identity(
        self,
        *,
        account: InfluencerPlatformAccount,
        uid: str | None,
        verified_at: datetime,
        request_token: UUID,
        operator_id: UUID | None,
    ) -> tuple[ProviderAccountIdentity | None, UUID | None]:
        """Return the current matching uid binding, creating it only from capture evidence."""

        if uid is None:
            return None, None
        current = await self.repository.get_current_identity(
            platform_account_id=account.id,
            namespace=ProviderAccountIdentityNamespace.DOUYIN_HUITUN_UID,
            for_update=True,
        )
        if current is not None:
            if (
                current.opaque_external_identity != uid
                or current.platform is not Platform.DOUYIN
                or current.identity_source != HUITUN_DOUYIN_IDENTITY_SOURCE
                or current.resolver_contract_version != HUITUN_DOUYIN_IDENTITY_CONTRACT_VERSION
            ):
                return None, None
            verification_id = await self._latest_douyin_runtime_verification_id(current.id)
            return current, verification_id

        # The capture request is already account-scoped and authenticated with a
        # one-time digest.  The opaque uid is taken only from the matching real
        # awemeList request, never inferred from an internal account field.
        binding = ProviderAccountIdentity(
            platform_account_id=account.id,
            platform=Platform.DOUYIN,
            namespace=ProviderAccountIdentityNamespace.DOUYIN_HUITUN_UID,
            opaque_external_identity=uid,
            identity_source=HUITUN_DOUYIN_IDENTITY_SOURCE,
            resolver_contract_version=HUITUN_DOUYIN_IDENTITY_CONTRACT_VERSION,
            verification_state=ProviderAccountIdentityVerificationState.VERIFIED_CURRENT,
            resolved_at=verified_at,
            verified_at=verified_at,
            provenance_ref=f"huitun-runtime-capture:{request_token}",
            lock_version=1,
            superseded_at=None,
            revoked_at=None,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(binding)
                await self.session.flush()
        except IntegrityError:
            # The external uid may already be bound to another account, or a
            # concurrent capture may have won this account's binding.  Do not
            # reassign or guess: the caller persists an UNKNOWN attempt.
            return None, None
        verification = ProviderAccountIdentityVerification(
            provider_account_identity_id=binding.id,
            identity_source=HUITUN_DOUYIN_IDENTITY_SOURCE,
            resolver_contract_version=HUITUN_DOUYIN_IDENTITY_CONTRACT_VERSION,
            verification_outcome=ProviderAccountIdentityVerificationOutcome.POLICY_VERIFIED,
            verified_at=verified_at,
            provenance_ref=HUITUN_DOUYIN_PROVENANCE_REF,
            idempotency_key=f"douyin-runtime:{request_token}",
        )
        self.session.add(verification)
        await self.session.flush()
        await self._record_identity_verified(
            account_id=account.id,
            binding_id=binding.id,
            operator_id=operator_id,
            department_id=None,
            ip="extension",
            user_agent="huitun-douyin-runtime-capture",
            namespace=ProviderAccountIdentityNamespace.DOUYIN_HUITUN_UID,
        )
        return binding, verification.id

    async def _latest_douyin_runtime_verification_id(self, binding_id: UUID) -> UUID | None:
        return cast(
            UUID | None,
            await self.session.scalar(
                select(ProviderAccountIdentityVerification.id)
                .where(
                    ProviderAccountIdentityVerification.provider_account_identity_id == binding_id,
                    ProviderAccountIdentityVerification.identity_source
                    == HUITUN_DOUYIN_IDENTITY_SOURCE,
                    ProviderAccountIdentityVerification.resolver_contract_version
                    == HUITUN_DOUYIN_IDENTITY_CONTRACT_VERSION,
                    ProviderAccountIdentityVerification.verification_outcome
                    == ProviderAccountIdentityVerificationOutcome.POLICY_VERIFIED,
                )
                .order_by(
                    ProviderAccountIdentityVerification.verified_at.desc(),
                    ProviderAccountIdentityVerification.id.desc(),
                )
                .limit(1)
            ),
        )

    def _require_provider_enabled(self) -> None:
        if not self._provider_enabled:
            raise ContentActivityError(
                409,
                "CONTENT_ACTIVITY_DISABLED",
                "Content Activity provider use is not enabled",
            )

    @property
    def _provider_enabled(self) -> bool:
        return (
            self.settings.content_activity_enabled
            and self.settings.content_activity_xhs_enabled
            and self.settings.content_activity_provider_governance_approved
            and self.settings.content_activity_provider_max_calls_per_run >= 1
        )

    async def _current_time(self) -> datetime:
        value: object
        if self.clock is not None:
            value = self.clock()
        elif self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            value = await self.session.scalar(select(func.clock_timestamp()))
        else:
            value = datetime.now(UTC)
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise RuntimeError("Content Activity clock must return an aware datetime")
        return value.astimezone(UTC)

    @asynccontextmanager
    async def _try_provider_call_slot(self) -> AsyncIterator[bool]:
        """Acquire the XHS P0 global call slot without blocking a worker lease.

        A separate short-lived connection owns the session-level advisory lock.
        This avoids accidentally returning a still-locked connection to the ORM
        pool after the request-claim transaction commits. Non-PostgreSQL unit
        tests have no distributed worker topology, so they proceed normally.
        """

        bind = self.session.bind
        if not isinstance(bind, AsyncEngine) or bind.dialect.name != "postgresql":
            yield True
            return
        async with bind.connect() as connection:
            acquired = bool(
                await connection.scalar(
                    text("SELECT pg_try_advisory_lock(:lock_key)"),
                    {"lock_key": PROVIDER_CALL_SLOT_LOCK_KEY},
                )
            )
            try:
                yield acquired
            finally:
                if acquired:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_key)"),
                        {"lock_key": PROVIDER_CALL_SLOT_LOCK_KEY},
                    )
                await connection.commit()

    async def _identity_owner(
        self,
        userid: str,
        *,
        for_update: bool = False,
    ) -> ProviderAccountIdentity | None:
        statement = select(ProviderAccountIdentity).where(
            ProviderAccountIdentity.platform == Platform.XIAOHONGSHU,
            ProviderAccountIdentity.namespace
            == ProviderAccountIdentityNamespace.XIAOHONGSHU_USERID,
            ProviderAccountIdentity.opaque_external_identity == userid,
        )
        if for_update:
            statement = statement.execution_options(populate_existing=True).with_for_update()
        return cast(ProviderAccountIdentity | None, await self.session.scalar(statement))

    async def _latest_verification_id(self, binding_id: UUID) -> UUID | None:
        return cast(
            UUID | None,
            await self.session.scalar(
                select(ProviderAccountIdentityVerification.id)
                .where(
                    ProviderAccountIdentityVerification.provider_account_identity_id == binding_id,
                    ProviderAccountIdentityVerification.identity_source == IDENTITY_SOURCE,
                    ProviderAccountIdentityVerification.resolver_contract_version
                    == TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1,
                    ProviderAccountIdentityVerification.verification_outcome
                    == ProviderAccountIdentityVerificationOutcome.POLICY_VERIFIED,
                )
                .order_by(
                    ProviderAccountIdentityVerification.verified_at.desc(),
                    ProviderAccountIdentityVerification.id.desc(),
                )
                .limit(1)
            ),
        )

    async def _verification_replay(
        self,
        event_key: str,
    ) -> tuple[ProviderAccountIdentity, ProviderAccountIdentityVerification] | None:
        event = await self.session.scalar(
            select(ProviderAccountIdentityVerification).where(
                ProviderAccountIdentityVerification.identity_source == IDENTITY_SOURCE,
                ProviderAccountIdentityVerification.resolver_contract_version
                == TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1,
                ProviderAccountIdentityVerification.verification_outcome
                == ProviderAccountIdentityVerificationOutcome.POLICY_VERIFIED,
                ProviderAccountIdentityVerification.idempotency_key == event_key,
            )
        )
        if event is None:
            return None
        binding = await self.session.get(
            ProviderAccountIdentity,
            event.provider_account_identity_id,
        )
        if binding is None:
            raise RuntimeError("Content Activity verification binding is missing")
        return binding, event

    async def _append_verification(
        self,
        binding: ProviderAccountIdentity,
        *,
        verified_at: datetime,
        idempotency_key: str,
    ) -> ProviderAccountIdentityVerification:
        event = ProviderAccountIdentityVerification(
            provider_account_identity_id=binding.id,
            identity_source=IDENTITY_SOURCE,
            resolver_contract_version=TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1,
            verification_outcome=ProviderAccountIdentityVerificationOutcome.POLICY_VERIFIED,
            verified_at=verified_at,
            provenance_ref=IDENTITY_PROVENANCE_REF,
            idempotency_key=idempotency_key,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def _request_by_token(
        self,
        request_token: UUID,
        *,
        for_update: bool = False,
    ) -> ContentActivityRefreshRequest | None:
        statement = select(ContentActivityRefreshRequest).where(
            ContentActivityRefreshRequest.request_token == request_token
        )
        if for_update:
            statement = statement.execution_options(populate_existing=True).with_for_update()
        return cast(ContentActivityRefreshRequest | None, await self.session.scalar(statement))

    async def _request_by_account_key(
        self,
        account_id: UUID,
        stored_key: str,
        *,
        for_update: bool = False,
    ) -> ContentActivityRefreshRequest | None:
        statement = select(ContentActivityRefreshRequest).where(
            ContentActivityRefreshRequest.platform_account_id == account_id,
            ContentActivityRefreshRequest.idempotency_key == stored_key,
        )
        if for_update:
            statement = statement.execution_options(populate_existing=True).with_for_update()
        return cast(ContentActivityRefreshRequest | None, await self.session.scalar(statement))

    async def _active_request_for_account(
        self,
        account_id: UUID,
        *,
        for_update: bool = False,
    ) -> ContentActivityRefreshRequest | None:
        statement = select(ContentActivityRefreshRequest).where(
            ContentActivityRefreshRequest.platform_account_id == account_id,
            ContentActivityRefreshRequest.state.in_(
                (
                    ContentActivityRefreshRequestState.PENDING,
                    ContentActivityRefreshRequestState.RUNNING,
                    ContentActivityRefreshRequestState.RETRY_WAIT,
                )
            ),
        )
        if for_update:
            statement = statement.execution_options(populate_existing=True).with_for_update()
        return cast(ContentActivityRefreshRequest | None, await self.session.scalar(statement))

    async def _record_identity_verified(
        self,
        *,
        account_id: UUID,
        binding_id: UUID,
        operator_id: UUID | None,
        department_id: UUID | None,
        ip: str,
        user_agent: str,
        namespace: ProviderAccountIdentityNamespace = (
            ProviderAccountIdentityNamespace.XIAOHONGSHU_USERID
        ),
    ) -> None:
        AuditRepository(self.session).add(
            action=AuditAction.CONTENT_ACTIVITY_IDENTITY_VERIFIED,
            result=AuditResult.SUCCESS,
            department_id=department_id,
            operator_id=operator_id,
            ip=ip[:64],
            user_agent=user_agent[:512],
            entity_type="provider_account_identity",
            entity_id=binding_id,
            after={"platform_account_id": str(account_id), "namespace": namespace.value},
        )

    async def _record_identity_conflict(
        self,
        *,
        account_id: UUID,
        operator_id: UUID | None,
        department_id: UUID | None,
        ip: str,
        user_agent: str,
    ) -> None:
        AuditRepository(self.session).add(
            action=AuditAction.CONTENT_ACTIVITY_IDENTITY_CONFLICT,
            result=AuditResult.DENIED,
            department_id=department_id,
            operator_id=operator_id,
            ip=ip[:64],
            user_agent=user_agent[:512],
            entity_type="influencer_platform_account",
            entity_id=account_id,
            after={"namespace": "xiaohongshu.userid", "reason": "IDENTITY_CONFLICT"},
        )

    def _audit_refresh_settlement(
        self,
        request: ContentActivityRefreshRequest,
        *,
        failed: bool,
    ) -> None:
        AuditRepository(self.session).add(
            action=(
                AuditAction.CONTENT_ACTIVITY_REFRESH_FAILED
                if failed
                else AuditAction.CONTENT_ACTIVITY_REFRESH_COMPLETED
            ),
            result=AuditResult.FAILED if failed else AuditResult.SUCCESS,
            department_id=None,
            operator_id=request.requested_by_operator_id,
            ip="worker",
            user_agent="content_activity.refresh_request",
            entity_type="content_activity_refresh_request",
            entity_id=request.id,
            after={
                "request_token": str(request.request_token),
                "state": request.state.value,
                "attempt_count": request.attempt_count,
                "last_error_code": request.last_error_code,
            },
        )

    def _retry_backoff(self, attempt: int) -> int:
        exponent = max(0, min(attempt - 1, 30))
        return min(
            self.refresh_policy.retry_backoff_max_seconds,
            self.refresh_policy.retry_backoff_base_seconds * (1 << exponent),
        )


def _scoped_idempotency_key(scope: str, account_id: UUID, key: str) -> str:
    try:
        validate_idempotency_key(key)
    except ValueError as exc:
        raise ContentActivityError(
            422,
            "IDEMPOTENCY_KEY_INVALID",
            "Idempotency-Key is invalid",
        ) from exc
    digest = sha256(f"{scope}:{account_id}:{key}".encode()).hexdigest()
    return f"{scope}:{digest}"


def _ledger_timestamp_utc(value: datetime) -> datetime:
    """Normalize an ORM timestamp for comparison without trusting client time.

    PostgreSQL returns the model's timestamptz values aware. SQLite test
    adapters return the same persisted UTC instants without tzinfo, so normalize
    that storage representation at the ledger boundary instead of allowing a
    naive/aware comparison to bypass expiry or crash a capture settlement.
    """

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_policy_accepted_identity(binding: ProviderAccountIdentity) -> bool:
    """Return whether a binding belongs to the immutable D1A resolver registry."""

    return (
        binding.platform is Platform.XIAOHONGSHU
        and binding.namespace is ProviderAccountIdentityNamespace.XIAOHONGSHU_USERID
        and binding.verification_state is ProviderAccountIdentityVerificationState.VERIFIED_CURRENT
        and binding.identity_source == IDENTITY_SOURCE
        and binding.resolver_contract_version == TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1
    )


def _is_accepted_xhs_identity_resolution(resolution: XhsIdentityResolution) -> bool:
    """Reject a non-pinned resolver result before it can create a binding."""

    return (
        resolution.usable
        and resolution.provider == TIKHUB_PROVIDER
        and resolution.provider_product == XHS_APP_V2_PRODUCT
        and resolution.endpoint == XHS_GET_USER_INFO_ENDPOINT
        and resolution.capability_policy_version == TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1
        and resolution.schema_contract_id == TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1
        and resolution.provider_error_class is None
        and resolution.provider_error_code is None
        and resolution.request_count == 1
    )


def _is_accepted_xhs_activity_attempt(attempt: XhsActivityAttempt) -> bool:
    """Ensure an adapter return still names the exact immutable V1 contract."""

    return (
        attempt.provider == TIKHUB_PROVIDER
        and attempt.provider_product == XHS_APP_V2_PRODUCT
        and attempt.endpoint == XHS_GET_USER_POSTED_NOTES_ENDPOINT
        and attempt.capability_policy_version == TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1
        and attempt.schema_contract_id == TIKHUB_XHS_APP_V2_GET_USER_POSTED_NOTES_RESPONSE_SCHEMA_V1
        and attempt.visibility_policy_id == TIKHUB_XHS_APP_V2_CURRENT_PUBLIC_VISIBILITY_POLICY_V1
    )


def _refresh_public(request: ContentActivityRefreshRequest) -> ContentActivityRefreshRequestPublic:
    return ContentActivityRefreshRequestPublic(
        request_token=request.request_token,
        platform_account_id=request.platform_account_id,
        state=request.state.value,
        attempt_count=request.attempt_count,
        max_attempts=request.max_attempts,
        requested_at=request.created_at,
    )


def _with_observed_at(attempt: XhsActivityAttempt, observed_at: datetime) -> XhsActivityAttempt:
    """Use the authoritative write clock while retaining the adapter decision."""

    normalized = observed_at.astimezone(UTC)
    if (
        attempt.activity_result is ContentActivityResult.PUBLICATION_FOUND
        and attempt.last_publication_at is not None
        and attempt.last_publication_at > normalized
    ):
        return _untrusted_attempt(
            normalized,
            error_class=ContentActivityProviderErrorClass.SCHEMA_DRIFT,
            error_code="CREATE_TIME_FUTURE",
        )
    return replace(attempt, observed_at=normalized)


def _observation_from_attempt(
    *,
    account_id: UUID,
    identity_id: UUID | None,
    verification_id: UUID | None,
    attempt_started_at: datetime,
    request_token: UUID,
    attempt: XhsActivityAttempt,
) -> ContentActivityObservation:
    is_publication = attempt.activity_result is ContentActivityResult.PUBLICATION_FOUND
    return ContentActivityObservation(
        platform_account_id=account_id,
        platform=Platform.XIAOHONGSHU,
        schema_version=1,
        activity_semantics=ContentActivitySemantics.CURRENT_PUBLIC_VISIBLE,
        provider_account_identity_id=identity_id,
        provider_account_identity_verification_id=verification_id,
        activity_source_provider=ContentActivityProvider.TIKHUB,
        provider_product=attempt.provider_product,
        endpoint=attempt.endpoint,
        endpoint_version=ENDPOINT_VERSION,
        adapter_version=ADAPTER_VERSION,
        capability_policy_version=attempt.capability_policy_version,
        response_schema_version=attempt.schema_contract_id,
        visibility_policy_version=attempt.visibility_policy_id,
        attempt_started_at=attempt_started_at,
        observed_at=attempt.observed_at,
        observation_status=attempt.observation_status,
        coverage_status=attempt.coverage_status,
        activity_result=attempt.activity_result,
        last_publication_at=attempt.last_publication_at if is_publication else None,
        latest_publication_id_namespace=NOTE_ID_NAMESPACE if is_publication else None,
        latest_publication_id=attempt.latest_publication_id if is_publication else None,
        latest_publication_type=attempt.latest_publication_type if is_publication else None,
        co_latest_publication_count=attempt.co_latest_publication_count if is_publication else None,
        coverage_start_at=None,
        coverage_end_at=None,
        timestamp_encoding="UNIX_SECONDS" if is_publication else None,
        source_timezone=None,
        timezone_basis="UNIX_SECONDS" if is_publication else None,
        normalized_timezone="UTC",
        request_ref=f"content-activity-refresh:{request_token}",
        provenance_ref=OBSERVATION_PROVENANCE_REF,
        scan_terminal_reason=attempt.terminal_reason,
        scanned_page_count=attempt.request_count,
        scanned_item_count=attempt.item_count,
        provider_error_class=attempt.provider_error_class,
        provider_error_code=attempt.provider_error_code,
    )


def _douyin_observation_from_attempt(
    *,
    account_id: UUID,
    identity_id: UUID | None,
    verification_id: UUID | None,
    attempt_started_at: datetime,
    request_token: UUID,
    runtime_request_id: str,
    observed_at: datetime,
    attempt: HuitunDouyinRuntimeAttempt,
) -> ContentActivityObservation:
    """Build an immutable returned-scope observation without provider payload data."""

    is_publication = attempt.activity_result is ContentActivityResult.PUBLICATION_FOUND
    return ContentActivityObservation(
        platform_account_id=account_id,
        platform=Platform.DOUYIN,
        schema_version=1,
        activity_semantics=ContentActivitySemantics.HUITUN_RETURNED_SCOPE,
        provider_account_identity_id=identity_id,
        provider_account_identity_verification_id=verification_id,
        activity_source_provider=ContentActivityProvider.HUITUN_DOUYIN_AWEME_LIST,
        provider_product=HUITUN_DOUYIN_PROVIDER_PRODUCT,
        endpoint=HUITUN_DOUYIN_ENDPOINT,
        endpoint_version=HUITUN_DOUYIN_ENDPOINT_VERSION,
        adapter_version=HUITUN_DOUYIN_ADAPTER_VERSION,
        capability_policy_version=HUITUN_DOUYIN_CAPABILITY_POLICY_VERSION,
        response_schema_version=HUITUN_DOUYIN_RESPONSE_SCHEMA_VERSION,
        visibility_policy_version=HUITUN_DOUYIN_VISIBILITY_POLICY_VERSION,
        attempt_started_at=attempt_started_at.astimezone(UTC),
        observed_at=observed_at.astimezone(UTC),
        observation_status=attempt.observation_status,
        coverage_status=attempt.coverage_status,
        activity_result=attempt.activity_result,
        last_publication_at=attempt.last_publication_at if is_publication else None,
        latest_publication_id_namespace=(
            HUITUN_DOUYIN_PUBLICATION_KEY_NAMESPACE if is_publication else None
        ),
        latest_publication_id=attempt.latest_publication_id if is_publication else None,
        latest_publication_type=attempt.latest_publication_type if is_publication else None,
        co_latest_publication_count=(
            attempt.co_latest_publication_count if is_publication else None
        ),
        coverage_start_at=attempt.coverage_start_at,
        coverage_end_at=attempt.coverage_end_at,
        timestamp_encoding=HUITUN_DOUYIN_TIMESTAMP_ENCODING,
        source_timezone=HUITUN_DOUYIN_SOURCE_TIMEZONE,
        timezone_basis=HUITUN_DOUYIN_TIMESTAMP_ENCODING,
        normalized_timezone="UTC",
        request_ref=f"huitun-douyin-runtime:{request_token}:{runtime_request_id}",
        provenance_ref=HUITUN_DOUYIN_PROVENANCE_REF,
        scan_terminal_reason=attempt.terminal_reason,
        scanned_page_count=1 if attempt.actual_uid is not None else 0,
        scanned_item_count=attempt.scanned_item_count,
        provider_error_class=attempt.provider_error_class,
        provider_error_code=attempt.provider_error_code,
    )


def _douyin_untrusted_attempt(
    actual_uid: str | None,
    *,
    error_class: ContentActivityProviderErrorClass,
    error_code: str,
) -> HuitunDouyinRuntimeAttempt:
    return HuitunDouyinRuntimeAttempt(
        actual_uid=actual_uid,
        observation_status=ContentActivityObservationStatus.RESULT_UNTRUSTED,
        coverage_status=ContentActivityCoverageStatus.UNKNOWN,
        activity_result=ContentActivityResult.UNDETERMINED,
        last_publication_at=None,
        latest_publication_id=None,
        latest_publication_type=None,
        co_latest_publication_count=None,
        coverage_start_at=None,
        coverage_end_at=None,
        terminal_reason=ContentActivityScanTerminalReason.RESPONSE_UNTRUSTED,
        scanned_item_count=0,
        provider_error_class=error_class,
        provider_error_code=error_code,
    )


def _identity_unresolved_attempt(observed_at: datetime) -> XhsActivityAttempt:
    return XhsActivityAttempt(
        observation_status=ContentActivityObservationStatus.IDENTITY_UNRESOLVED,
        coverage_status=ContentActivityCoverageStatus.UNKNOWN,
        activity_result=ContentActivityResult.UNDETERMINED,
        observed_at=observed_at,
        last_publication_at=None,
        latest_publication_id=None,
        latest_publication_type=None,
        co_latest_publication_count=None,
        provider_error_class=ContentActivityProviderErrorClass.INPUT_UNAVAILABLE,
        provider_error_code="IDENTITY_UNRESOLVED",
        terminal_reason=ContentActivityScanTerminalReason.IDENTITY_UNRESOLVED,
        item_count=0,
        request_count=0,
    )


def _identity_binding_not_accepted_attempt(observed_at: datetime) -> XhsActivityAttempt:
    """Do not use an unregistered/forged verified-current binding for activity."""

    return replace(
        _untrusted_attempt(
            observed_at,
            error_class=ContentActivityProviderErrorClass.POLICY_REJECTED,
            error_code="IDENTITY_BINDING_NOT_ACCEPTED",
        ),
        terminal_reason=ContentActivityScanTerminalReason.IDENTITY_UNRESOLVED,
    )


def _untrusted_attempt(
    observed_at: datetime,
    *,
    error_class: ContentActivityProviderErrorClass,
    error_code: str,
) -> XhsActivityAttempt:
    return XhsActivityAttempt(
        observation_status=ContentActivityObservationStatus.RESULT_UNTRUSTED,
        coverage_status=ContentActivityCoverageStatus.UNKNOWN,
        activity_result=ContentActivityResult.UNDETERMINED,
        observed_at=observed_at,
        last_publication_at=None,
        latest_publication_id=None,
        latest_publication_type=None,
        co_latest_publication_count=None,
        provider_error_class=error_class,
        provider_error_code=error_code,
        terminal_reason=ContentActivityScanTerminalReason.RESPONSE_UNTRUSTED,
        item_count=0,
        request_count=0,
    )


def _policy_rejected_attempt(observed_at: datetime) -> XhsActivityAttempt:
    """Record post-enqueue governance revocation without an external call."""

    return _request_not_permitted_attempt(observed_at, error_code="CONFIG_DISABLED")


def _account_inactive_attempt(observed_at: datetime) -> XhsActivityAttempt:
    """Persist a no-call, non-trusted result after account deactivation."""

    return _request_not_permitted_attempt(observed_at, error_code="ACCOUNT_INACTIVE")


def _request_not_permitted_attempt(
    observed_at: datetime,
    *,
    error_code: str,
) -> XhsActivityAttempt:
    return replace(
        _untrusted_attempt(
            observed_at,
            error_class=ContentActivityProviderErrorClass.POLICY_REJECTED,
            error_code=error_code,
        ),
        terminal_reason=ContentActivityScanTerminalReason.REQUEST_NOT_PERMITTED,
    )


def _unexpected_provider_attempt(observed_at: datetime) -> XhsActivityAttempt:
    """Fail closed when an adapter violates its normalized-return contract."""

    return _untrusted_attempt(
        observed_at,
        error_class=ContentActivityProviderErrorClass.UNKNOWN,
        error_code="UNEXPECTED_PROVIDER_FAILURE",
    )


def _equal_observed_at_conflict_attempt(observed_at: datetime) -> XhsActivityAttempt:
    return _untrusted_attempt(
        observed_at,
        error_class=ContentActivityProviderErrorClass.UNKNOWN,
        error_code="EQUAL_OBSERVED_AT_CONFLICT",
    )


def _equal_observed_at_trusted_conflict(
    projection: ContentActivityProjection | None,
    observation: ContentActivityObservation,
) -> bool:
    """Detect an otherwise unorderable equal-time trusted-current contradiction."""

    if (
        projection is None
        or projection.trusted_observed_at != observation.observed_at
        or not is_trusted_current_public(observation)
    ):
        return False
    return (
        projection.trusted_observation_status,
        projection.trusted_coverage_status,
        projection.trusted_activity_result,
        projection.trusted_capability_policy_version,
        projection.last_publication_at,
        projection.latest_publication_id_namespace,
        projection.latest_publication_id,
        projection.latest_publication_type,
        projection.co_latest_publication_count,
    ) != (
        observation.observation_status,
        observation.coverage_status,
        observation.activity_result,
        observation.capability_policy_version,
        observation.last_publication_at,
        observation.latest_publication_id_namespace,
        observation.latest_publication_id,
        observation.latest_publication_type,
        observation.co_latest_publication_count,
    )


def _is_retryable(attempt: XhsActivityAttempt) -> bool:
    return attempt.provider_error_class in {
        ContentActivityProviderErrorClass.TIMEOUT,
        ContentActivityProviderErrorClass.TRANSPORT,
        ContentActivityProviderErrorClass.RATE_LIMITED,
    }


__all__ = [
    "ContentActivityError",
    "ContentActivityService",
    "RefreshClaim",
    "RefreshPolicy",
    "RefreshProcessResult",
]

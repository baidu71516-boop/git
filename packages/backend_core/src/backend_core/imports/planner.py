"""One matcher/freshness/merge planner used by both Preview and Confirm."""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from backend_core.imports.contracts import CanonicalInfluencerRecord, RowIssue
from backend_core.imports.enums import ImportMatchType, ImportRowAction
from backend_core.imports.hashing import hash_document
from backend_core.influencers.enums import (
    ContactType,
    ContactValidationStatus,
    DataSource,
    Platform,
)
from backend_core.influencers.models import (
    InfluencerContact,
    InfluencerCurrentMetrics,
    InfluencerPlatformAccount,
    InfluencerSourceState,
    PlatformAccountSourceIdentity,
)

ACCOUNT_FIELD_MAP = {
    "display_name": "account_name",
    "account_handle": "account_handle",
    "profile_url": "profile_url",
    "normalized_profile_url": "normalized_profile_url",
    "bio": "bio",
    "gender": "gender",
    "region_raw": "region_raw",
    "verification_info": "verification_info",
    "mcn_name": "mcn_name",
    "creator_tags": "source_tags",
    "creator_level": "creator_level",
    "is_brand_partner": "is_brand_partner",
}


class FreshnessRelation(StrEnum):
    NEW = "new"
    NEWER = "newer"
    SAME = "same"
    OLDER = "older"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MatchResult:
    account: InfluencerPlatformAccount | None
    match_type: ImportMatchType
    manual_review: bool
    warnings: tuple[RowIssue, ...]
    candidates: dict[str, list[str]]


@dataclass(frozen=True)
class PlannedImportRow:
    matched_influencer_id: UUID | None
    matched_platform_account_id: UUID | None
    match_type: ImportMatchType
    action: ImportRowAction
    merge_plan: dict[str, Any]
    warnings: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    plan_hash: str


class ImportPlanningRepository(Protocol):
    """Read-only persistence boundary required by the shared Planner."""

    async def accounts_by_platform_id(
        self, platform: Platform, platform_account_id: str
    ) -> list[InfluencerPlatformAccount]: ...

    async def accounts_by_external_id(
        self,
        platform: Platform,
        source: DataSource,
        external_account_id: str,
    ) -> list[InfluencerPlatformAccount]: ...

    async def accounts_by_profile_url(
        self, platform: Platform, normalized_profile_url: str
    ) -> list[InfluencerPlatformAccount]: ...

    async def accounts_by_handle(
        self, platform: Platform, account_handle: str
    ) -> list[InfluencerPlatformAccount]: ...

    async def get_source_state(
        self, platform_account_id: UUID, source: DataSource
    ) -> InfluencerSourceState | None: ...

    async def get_source_identity(
        self,
        platform_account_id: UUID,
        source: DataSource,
        external_account_id: str,
    ) -> PlatformAccountSourceIdentity | None: ...

    async def get_current_metrics(
        self, platform_account_id: UUID, source: DataSource
    ) -> InfluencerCurrentMetrics | None: ...

    async def snapshot_exists(self, snapshot_key: str) -> bool: ...

    async def source_contacts(
        self,
        influencer_id: UUID,
        source: DataSource,
        contact_type: ContactType,
    ) -> list[InfluencerContact]: ...

    async def contacts_with_normalized_value(
        self, contact_type: ContactType, normalized_value: str
    ) -> list[InfluencerContact]: ...


def _is_empty(value: Any) -> bool:
    return value is None or value == ""


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _datetime_text(value: datetime | None) -> str | None:
    normalized = _as_utc(value)
    return normalized.isoformat() if normalized is not None else None


def freshness_relation(
    incoming: datetime | None, existing: datetime | None, *, exists: bool
) -> FreshnessRelation:
    if not exists:
        return FreshnessRelation.NEW
    incoming_utc = _as_utc(incoming)
    existing_utc = _as_utc(existing)
    if incoming_utc is None:
        return FreshnessRelation.UNKNOWN
    if existing_utc is None or incoming_utc > existing_utc:
        return FreshnessRelation.NEWER
    if incoming_utc < existing_utc:
        return FreshnessRelation.OLDER
    return FreshnessRelation.SAME


def identity_lock_keys(record: CanonicalInfluencerRecord) -> list[str]:
    identity = record.platform_identity
    prefix = identity.platform.value
    keys: list[str] = []
    if identity.platform_account_id:
        keys.append(f"platform:{prefix}:account:{identity.platform_account_id}")
    if identity.external_source_id:
        keys.append(
            f"source:{record.source.value}:platform:{prefix}:external:"
            f"{identity.external_source_id}"
        )
    if identity.normalized_profile_url:
        keys.append(f"platform:{prefix}:profile:{identity.normalized_profile_url}")
    return keys


def primary_identity_key(record: CanonicalInfluencerRecord) -> str | None:
    keys = identity_lock_keys(record)
    return keys[0] if keys else None


def _metric_snapshot_key(record: CanonicalInfluencerRecord, metrics_hash: str) -> str:
    identity = record.platform_identity
    return hash_document(
        {
            "platform": identity.platform.value,
            "platform_account_id": identity.platform_account_id,
            "normalized_profile_url": identity.normalized_profile_url,
            "external_source_id": identity.external_source_id,
            "source": record.source.value,
            "source_updated_at": record.source_updated_at,
            "metrics_hash": metrics_hash,
        }
    )


def metric_snapshot_key(record: CanonicalInfluencerRecord) -> str:
    """Return the exact snapshot lookup key used by Planner metrics logic."""

    serialized_metrics = record.as_dict()["metrics"]
    metrics = {key: value for key, value in serialized_metrics.items() if value is not None}
    return _metric_snapshot_key(record, hash_document(metrics))


def build_preview_context(
    records: list[tuple[int, CanonicalInfluencerRecord]],
) -> tuple[dict[int, int], set[str]]:
    """Return duplicate-row owners and emails shared by different identities."""

    identity_owner: dict[str, int] = {}
    duplicate_owner: dict[int, int] = {}
    email_identities: dict[str, set[str]] = {}
    for row_number, record in records:
        identity_key = primary_identity_key(record)
        if identity_key is not None:
            owner = identity_owner.setdefault(identity_key, row_number)
            if owner != row_number:
                duplicate_owner[row_number] = owner
        for contact in record.contacts:
            if contact.type == ContactType.EMAIL and identity_key is not None:
                email_identities.setdefault(contact.normalized_value, set()).add(identity_key)
    duplicate_emails = {
        email for email, identities in email_identities.items() if len(identities) > 1
    }
    return duplicate_owner, duplicate_emails


class ImportPlanner:
    def __init__(self, repository: ImportPlanningRepository) -> None:
        self.repository = repository

    async def _match(self, record: CanonicalInfluencerRecord) -> MatchResult:
        identity = record.platform_identity
        candidates: dict[str, list[InfluencerPlatformAccount]] = {}
        if identity.platform_account_id:
            candidates[ImportMatchType.PLATFORM_ACCOUNT_ID.value] = (
                await self.repository.accounts_by_platform_id(
                    identity.platform, identity.platform_account_id
                )
            )
        if identity.external_source_id:
            candidates[ImportMatchType.EXTERNAL_SOURCE_ID.value] = (
                await self.repository.accounts_by_external_id(
                    identity.platform, record.source, identity.external_source_id
                )
            )
        if identity.normalized_profile_url:
            candidates[ImportMatchType.NORMALIZED_PROFILE_URL.value] = (
                await self.repository.accounts_by_profile_url(
                    identity.platform, identity.normalized_profile_url
                )
            )

        candidate_summary = {
            basis: sorted(str(account.id) for account in accounts)
            for basis, accounts in candidates.items()
        }
        unique_accounts = {
            account.id: account for accounts in candidates.values() for account in accounts
        }
        if any(len(accounts) > 1 for accounts in candidates.values()) or len(unique_accounts) > 1:
            return MatchResult(
                account=None,
                match_type=ImportMatchType.NONE,
                manual_review=True,
                warnings=(
                    RowIssue(
                        code="IDENTITY_CONFLICT",
                        message="Identity candidates resolve to different platform accounts",
                    ),
                ),
                candidates=candidate_summary,
            )

        account = next(iter(unique_accounts.values()), None)
        if account is not None:
            if (
                identity.platform_account_id
                and account.platform_account_id
                and identity.platform_account_id != account.platform_account_id
            ):
                return MatchResult(
                    account=None,
                    match_type=ImportMatchType.NONE,
                    manual_review=True,
                    warnings=(
                        RowIssue(
                            code="IDENTITY_CONFLICT",
                            message="Incoming platform identity conflicts with the matched account",
                        ),
                    ),
                    candidates=candidate_summary,
                )
            priority = (
                ImportMatchType.PLATFORM_ACCOUNT_ID,
                ImportMatchType.EXTERNAL_SOURCE_ID,
                ImportMatchType.NORMALIZED_PROFILE_URL,
            )
            match_type = next(
                item
                for item in priority
                if any(candidate.id == account.id for candidate in candidates.get(item.value, []))
            )
            return MatchResult(
                account=account,
                match_type=match_type,
                manual_review=False,
                warnings=(),
                candidates=candidate_summary,
            )

        if identity.account_handle:
            handle_matches = await self.repository.accounts_by_handle(
                identity.platform, identity.account_handle
            )
            if handle_matches:
                candidate_summary["account_handle"] = sorted(
                    str(candidate.id) for candidate in handle_matches
                )
                return MatchResult(
                    account=None,
                    match_type=ImportMatchType.NONE,
                    manual_review=True,
                    warnings=(
                        RowIssue(
                            code="ACCOUNT_HANDLE_POTENTIAL_MATCH",
                            message="Account handle has a potential match and requires review",
                        ),
                    ),
                    candidates=candidate_summary,
                )
        return MatchResult(
            account=None,
            match_type=ImportMatchType.NONE,
            manual_review=False,
            warnings=(),
            candidates=candidate_summary,
        )

    async def match(self, record: CanonicalInfluencerRecord) -> MatchResult:
        """Expose the frozen Phase 1B matcher to batch component coordination."""

        return await self._match(record)

    @staticmethod
    def _incoming_account_values(record: CanonicalInfluencerRecord) -> dict[str, Any]:
        identity = record.platform_identity
        values: dict[str, Any] = {
            "display_name": record.display_name,
            "account_handle": identity.account_handle,
            "profile_url": identity.profile_url,
            "normalized_profile_url": identity.normalized_profile_url,
            **record.public_profile,
        }
        return {key: value for key, value in values.items() if value is not None}

    async def plan(
        self,
        *,
        job_id: UUID,
        row_number: int,
        record: CanonicalInfluencerRecord,
        normalized_data: dict[str, Any],
        mapping_hash: str,
        preview_revision: int,
        initial_warnings: list[dict[str, Any]] | None = None,
        initial_errors: list[dict[str, Any]] | None = None,
        duplicate_owner_row: int | None = None,
        possible_duplicate_emails: set[str] | None = None,
        row_locator: dict[str, Any] | None = None,
        match_override: MatchResult | None = None,
    ) -> PlannedImportRow:
        warnings = list(initial_warnings or [])
        errors = list(initial_errors or [])
        identity_key = primary_identity_key(record)
        if errors or identity_key is None:
            if identity_key is None and not errors:
                errors.append(
                    {
                        "code": "MISSING_PLATFORM_IDENTITY",
                        "message": "A stable platform identity is required",
                    }
                )
            return self._finalize(
                job_id=job_id,
                row_number=row_number,
                normalized_data=normalized_data,
                mapping_hash=mapping_hash,
                preview_revision=preview_revision,
                match_type=ImportMatchType.NONE,
                action=ImportRowAction.ERROR,
                matched_influencer_id=None,
                matched_platform_account_id=None,
                merge_plan={"identity_keys": identity_lock_keys(record)},
                warnings=warnings,
                errors=errors,
                preconditions={},
                row_locator=row_locator,
            )
        if duplicate_owner_row is not None:
            warnings.append(
                {
                    "code": "DUPLICATE_IDENTITY_IN_FILE",
                    "message": "A previous row in this file has the same hard identity",
                    "owner_row": duplicate_owner_row,
                }
            )
            return self._finalize(
                job_id=job_id,
                row_number=row_number,
                normalized_data=normalized_data,
                mapping_hash=mapping_hash,
                preview_revision=preview_revision,
                match_type=ImportMatchType.NONE,
                action=ImportRowAction.SKIP,
                matched_influencer_id=None,
                matched_platform_account_id=None,
                merge_plan={"identity_keys": identity_lock_keys(record)},
                warnings=warnings,
                errors=errors,
                preconditions={"duplicate_owner_row": duplicate_owner_row},
                row_locator=row_locator,
            )

        match = match_override or await self._match(record)
        warnings.extend(issue.as_dict() for issue in match.warnings)
        if match.manual_review:
            return self._finalize(
                job_id=job_id,
                row_number=row_number,
                normalized_data=normalized_data,
                mapping_hash=mapping_hash,
                preview_revision=preview_revision,
                match_type=match.match_type,
                action=ImportRowAction.MANUAL_REVIEW,
                matched_influencer_id=None,
                matched_platform_account_id=None,
                merge_plan={"identity_keys": identity_lock_keys(record)},
                warnings=warnings,
                errors=errors,
                preconditions={"identity_candidates": match.candidates},
                row_locator=row_locator,
            )

        account = match.account
        source_state = (
            await self.repository.get_source_state(account.id, record.source)
            if account is not None
            else None
        )
        relation = freshness_relation(
            record.source_updated_at,
            source_state.source_updated_at if source_state else None,
            exists=source_state is not None,
        )
        incoming_values = self._incoming_account_values(record)
        account_updates: dict[str, Any] = {}
        if account is not None:
            for canonical_field, incoming in incoming_values.items():
                model_field = ACCOUNT_FIELD_MAP.get(canonical_field)
                if model_field is None:
                    continue
                existing = getattr(account, model_field)
                if _is_empty(existing):
                    account_updates[model_field] = incoming
                elif existing == incoming:
                    continue
                elif relation in {FreshnessRelation.NEW, FreshnessRelation.NEWER}:
                    account_updates[model_field] = incoming
                    if model_field == "account_handle":
                        warnings.append(
                            {
                                "code": "ACCOUNT_HANDLE_CHANGED",
                                "message": "The source reports a newer account handle",
                                "field": "account_handle",
                            }
                        )
                elif relation == FreshnessRelation.SAME:
                    warnings.append(
                        {
                            "code": "SAME_TIMESTAMP_CONFLICT",
                            "message": "Existing value was kept for a same-time conflict",
                            "field": canonical_field,
                        }
                    )
                elif relation == FreshnessRelation.OLDER:
                    warnings.append(
                        {
                            "code": "STALE_SOURCE_VALUE_IGNORED",
                            "message": "Older source data did not overwrite the existing value",
                            "field": canonical_field,
                        }
                    )
                else:
                    warnings.append(
                        {
                            "code": "SOURCE_TIME_MISSING_FILL_ONLY",
                            "message": "Missing source time limits this field to fill-only",
                            "field": canonical_field,
                        }
                    )

        existing_source_data = dict(source_state.source_data) if source_state else {}
        merged_source_data = dict(existing_source_data)
        for key, incoming in incoming_values.items():
            existing = merged_source_data.get(key)
            if _is_empty(existing) or relation in {
                FreshnessRelation.NEW,
                FreshnessRelation.NEWER,
            }:
                merged_source_data[key] = incoming
        merged_source_hash = hash_document(merged_source_data)
        source_state_changes = (
            source_state is None
            or merged_source_hash != source_state.source_data_hash
            or (
                relation == FreshnessRelation.NEWER
                and _as_utc(record.source_updated_at) != _as_utc(source_state.source_updated_at)
            )
        )
        source_state_plan: dict[str, Any] | None = None
        if source_state_changes:
            source_state_plan = {
                "operation": "create" if source_state is None else "update",
                "source_updated_at": (
                    record.as_dict()["source_updated_at"]
                    if relation in {FreshnessRelation.NEW, FreshnessRelation.NEWER}
                    else (_datetime_text(source_state.source_updated_at) if source_state else None)
                ),
                "source_data": merged_source_data,
                "source_data_hash": merged_source_hash,
                "state_version": 1 if source_state is None else source_state.state_version + 1,
            }

        source_identity_plan: dict[str, Any] | None = None
        existing_source_identity_id: str | None = None
        external_id = record.platform_identity.external_source_id
        if external_id and account is not None:
            source_identity = await self.repository.get_source_identity(
                account.id, record.source, external_id
            )
            existing_source_identity_id = str(source_identity.id) if source_identity else None
            if source_identity is None:
                source_identity_plan = {"external_account_id": external_id}
        elif external_id:
            source_identity_plan = {"external_account_id": external_id}

        current_metrics = (
            await self.repository.get_current_metrics(account.id, record.source)
            if account is not None
            else None
        )
        metrics_plan, metric_warnings, metric_preconditions = await self._plan_metrics(
            record, account, current_metrics, relation
        )
        warnings.extend(metric_warnings)

        contacts_plan, contact_warnings, contact_preconditions = await self._plan_contacts(
            record,
            account,
            relation,
            possible_duplicate_emails or set(),
        )
        warnings.extend(contact_warnings)

        creates_account = account is None
        writes = bool(
            creates_account
            or account_updates
            or source_state_plan
            or source_identity_plan
            or metrics_plan.get("current")
            or metrics_plan.get("snapshot")
            or contacts_plan.get("create")
            or contacts_plan.get("deactivate_ids")
            or contacts_plan.get("mark_duplicate_ids")
        )
        action = (
            ImportRowAction.CREATE
            if creates_account
            else ImportRowAction.UPDATE if writes else ImportRowAction.NO_CHANGE
        )
        account_create = None
        if creates_account:
            identity = record.platform_identity
            account_create = {
                "display_name": record.display_name or identity.account_handle or "未命名达人",
                "platform": identity.platform.value,
                "platform_account_id": identity.platform_account_id,
                "account_name": record.display_name or identity.account_handle or "未命名账号",
                "account_handle": identity.account_handle,
                "profile_url": identity.profile_url,
                "normalized_profile_url": identity.normalized_profile_url,
                "source": record.source.value,
                "public_profile": record.public_profile,
            }

        account_precondition = (
            {
                "id": str(account.id),
                "influencer_id": str(account.influencer_id),
                "platform_account_id": account.platform_account_id,
                "account_handle": account.account_handle,
                "profile_url": account.profile_url,
                "normalized_profile_url": account.normalized_profile_url,
                **{
                    model_field: getattr(account, model_field)
                    for model_field in ACCOUNT_FIELD_MAP.values()
                    if model_field
                    not in {
                        "account_handle",
                        "profile_url",
                        "normalized_profile_url",
                    }
                },
            }
            if account is not None
            else None
        )
        preconditions = {
            "identity_candidates": match.candidates,
            "account": account_precondition,
            "source_state": (
                {
                    "id": str(source_state.id),
                    "source_updated_at": _as_utc(source_state.source_updated_at),
                    "source_data_hash": source_state.source_data_hash,
                    "state_version": source_state.state_version,
                }
                if source_state
                else None
            ),
            "source_identity_id": existing_source_identity_id,
            "metrics": metric_preconditions,
            "contacts": contact_preconditions,
        }
        merge_plan = {
            "identity_keys": identity_lock_keys(record),
            "account_create": account_create,
            "account_updates": account_updates,
            "source_state": source_state_plan,
            "source_identity": source_identity_plan,
            "metrics": metrics_plan,
            "contacts": contacts_plan,
        }
        return self._finalize(
            job_id=job_id,
            row_number=row_number,
            normalized_data=normalized_data,
            mapping_hash=mapping_hash,
            preview_revision=preview_revision,
            match_type=match.match_type,
            action=action,
            matched_influencer_id=account.influencer_id if account else None,
            matched_platform_account_id=account.id if account else None,
            merge_plan=merge_plan,
            warnings=warnings,
            errors=errors,
            preconditions=preconditions,
            row_locator=row_locator,
        )

    def plan_batch_duplicate(
        self,
        *,
        job_id: UUID,
        row_number: int,
        normalized_data: dict[str, Any],
        mapping_hash: str,
        row_locator: dict[str, Any],
        merge_plan: dict[str, Any],
        warnings: list[dict[str, Any]],
        errors: list[dict[str, Any]],
    ) -> PlannedImportRow:
        """Create a deterministic revision-zero plan for a retained duplicate row."""

        return self._finalize(
            job_id=job_id,
            row_number=row_number,
            normalized_data=normalized_data,
            mapping_hash=mapping_hash,
            preview_revision=0,
            match_type=ImportMatchType.NONE,
            action=ImportRowAction.SKIP,
            matched_influencer_id=None,
            matched_platform_account_id=None,
            merge_plan=merge_plan,
            warnings=warnings,
            errors=errors,
            preconditions={},
            row_locator=row_locator,
        )

    def plan_batch_manual_review(
        self,
        *,
        job_id: UUID,
        row_number: int,
        normalized_data: dict[str, Any],
        mapping_hash: str,
        row_locator: dict[str, Any],
        merge_plan: dict[str, Any],
        warnings: list[dict[str, Any]],
        errors: list[dict[str, Any]],
    ) -> PlannedImportRow:
        """Create a deterministic non-sensitive plan for a conflicted component."""

        return self._finalize(
            job_id=job_id,
            row_number=row_number,
            normalized_data=normalized_data,
            mapping_hash=mapping_hash,
            preview_revision=0,
            match_type=ImportMatchType.NONE,
            action=ImportRowAction.MANUAL_REVIEW,
            matched_influencer_id=None,
            matched_platform_account_id=None,
            merge_plan=merge_plan,
            warnings=warnings,
            errors=errors,
            preconditions={},
            row_locator=row_locator,
        )

    async def _plan_metrics(
        self,
        record: CanonicalInfluencerRecord,
        account: InfluencerPlatformAccount | None,
        current_metrics: InfluencerCurrentMetrics | None,
        relation: FreshnessRelation,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        # Persist only the canonical JSON representation. Adapter contracts may
        # hold Decimal values in memory, but JSON/JSONB plans must be portable.
        serialized_metrics = record.as_dict()["metrics"]
        metrics = {key: value for key, value in serialized_metrics.items() if value is not None}
        metrics_hash = hash_document(metrics)
        snapshot_key = _metric_snapshot_key(record, metrics_hash)
        snapshot_exists = (
            await self.repository.snapshot_exists(snapshot_key) if account is not None else False
        )
        current_plan: dict[str, Any] | None = None
        if metrics:
            if current_metrics is None:
                current_plan = {
                    "operation": "create",
                    "source_updated_at": record.as_dict()["source_updated_at"],
                    "metrics": metrics,
                    "metrics_hash": metrics_hash,
                }
            elif relation == FreshnessRelation.NEWER:
                merged = {**current_metrics.metrics, **metrics}
                current_plan = {
                    "operation": "update",
                    "source_updated_at": record.as_dict()["source_updated_at"],
                    "metrics": merged,
                    "metrics_hash": hash_document(merged),
                }
            elif relation == FreshnessRelation.SAME:
                merged = dict(current_metrics.metrics)
                for field, incoming in metrics.items():
                    existing = merged.get(field)
                    if _is_empty(existing):
                        merged[field] = incoming
                    elif existing != incoming:
                        warnings.append(
                            {
                                "code": "SAME_TIMESTAMP_METRIC_CONFLICT",
                                "message": "Existing metric was kept for a same-time conflict",
                                "field": field,
                            }
                        )
                merged_hash = hash_document(merged)
                if merged_hash != current_metrics.metrics_hash:
                    current_plan = {
                        "operation": "update",
                        "source_updated_at": record.as_dict()["source_updated_at"],
                        "metrics": merged,
                        "metrics_hash": merged_hash,
                    }
            elif relation == FreshnessRelation.OLDER:
                warnings.append(
                    {
                        "code": "STALE_METRICS_CURRENT_IGNORED",
                        "message": "Older metrics will not replace the current projection",
                    }
                )
            elif relation == FreshnessRelation.UNKNOWN:
                warnings.append(
                    {
                        "code": "METRIC_SOURCE_TIME_MISSING",
                        "message": "Metrics without source time will not replace current metrics",
                    }
                )
        snapshot_plan = (
            {
                "snapshot_key": snapshot_key,
                "source_updated_at": record.as_dict()["source_updated_at"],
                "metrics": metrics,
                "metrics_hash": metrics_hash,
            }
            if metrics and not snapshot_exists
            else None
        )
        preconditions = {
            "current": (
                {
                    "id": str(current_metrics.id),
                    "source_updated_at": _as_utc(current_metrics.source_updated_at),
                    "metrics_hash": current_metrics.metrics_hash,
                }
                if current_metrics is not None
                else None
            ),
            "snapshot_key": snapshot_key,
            "snapshot_exists": snapshot_exists,
        }
        return {"current": current_plan, "snapshot": snapshot_plan}, warnings, preconditions

    async def _plan_contacts(
        self,
        record: CanonicalInfluencerRecord,
        account: InfluencerPlatformAccount | None,
        relation: FreshnessRelation,
        possible_duplicate_emails: set[str],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        warnings: list[dict[str, Any]] = []
        creates: list[dict[str, Any]] = []
        deactivate_ids: set[str] = set()
        mark_duplicate_ids: set[str] = set()
        observe_ids: set[str] = set()
        preconditions: list[dict[str, Any]] = []
        influencer_id = account.influencer_id if account else None
        existing_source_contacts = (
            await self.repository.source_contacts(influencer_id, record.source, ContactType.EMAIL)
            if influencer_id is not None
            else []
        )
        for existing in existing_source_contacts:
            preconditions.append(
                {
                    "id": str(existing.id),
                    "normalized_value": existing.normalized_value,
                    "is_current": existing.is_current,
                    "possible_duplicate_contact": existing.possible_duplicate_contact,
                    "source_updated_at": _as_utc(existing.source_updated_at),
                    "last_seen_at": _as_utc(existing.last_seen_at),
                    "last_import_job_id": (
                        str(existing.last_import_job_id)
                        if existing.last_import_job_id is not None
                        else None
                    ),
                    "last_import_row_id": (
                        str(existing.last_import_row_id)
                        if existing.last_import_row_id is not None
                        else None
                    ),
                }
            )
        for contact in record.contacts:
            if contact.validation_status != ContactValidationStatus.VALID:
                continue
            same_contact = next(
                (
                    item
                    for item in existing_source_contacts
                    if item.type == contact.type
                    and item.normalized_value == contact.normalized_value
                ),
                None,
            )
            global_matches = await self.repository.contacts_with_normalized_value(
                contact.type, contact.normalized_value
            )
            other_matches = [
                item
                for item in global_matches
                if influencer_id is None or item.influencer_id != influencer_id
            ]
            is_duplicate = bool(other_matches) or (
                contact.type == ContactType.EMAIL
                and contact.normalized_value in possible_duplicate_emails
            )
            mark_duplicate_ids.update(
                str(item.id) for item in other_matches if item.source != DataSource.MANUAL
            )
            if same_contact is not None:
                if same_contact.source != DataSource.MANUAL:
                    observe_ids.add(str(same_contact.id))
                if (
                    same_contact.source != DataSource.MANUAL
                    and is_duplicate
                    and not same_contact.possible_duplicate_contact
                ):
                    mark_duplicate_ids.add(str(same_contact.id))
                continue
            current_contacts = [
                item
                for item in existing_source_contacts
                if item.source != DataSource.MANUAL and item.is_current
            ]
            can_be_current = not current_contacts or relation in {
                FreshnessRelation.NEW,
                FreshnessRelation.NEWER,
            }
            if can_be_current:
                deactivate_ids.update(str(item.id) for item in current_contacts)
            elif current_contacts:
                warnings.append(
                    {
                        "code": "CONTACT_RETAINED_AS_HISTORY",
                        "message": (
                            "Contact was retained as history because source data is not newer"
                        ),
                        "field": contact.type.value,
                    }
                )
            creates.append(
                {
                    "type": contact.type.value,
                    "value": contact.value,
                    "normalized_value": contact.normalized_value,
                    "validation_status": contact.validation_status.value,
                    "is_current": can_be_current,
                    "possible_duplicate_contact": is_duplicate,
                    "source_updated_at": record.as_dict()["source_updated_at"],
                }
            )
        return (
            {
                "create": creates,
                "deactivate_ids": sorted(deactivate_ids),
                "mark_duplicate_ids": sorted(mark_duplicate_ids),
                "observe_ids": sorted(observe_ids),
            },
            warnings,
            sorted(preconditions, key=lambda item: item["id"]),
        )

    @staticmethod
    def _finalize(
        *,
        job_id: UUID,
        row_number: int,
        normalized_data: dict[str, Any],
        mapping_hash: str,
        preview_revision: int,
        match_type: ImportMatchType,
        action: ImportRowAction,
        matched_influencer_id: UUID | None,
        matched_platform_account_id: UUID | None,
        merge_plan: dict[str, Any],
        warnings: list[dict[str, Any]],
        errors: list[dict[str, Any]],
        preconditions: dict[str, Any],
        row_locator: dict[str, Any] | None = None,
    ) -> PlannedImportRow:
        payload = {
            "job_id": job_id,
            "row_number": row_number,
            "normalized_data": normalized_data,
            "matched_influencer_id": matched_influencer_id,
            "matched_platform_account_id": matched_platform_account_id,
            "match_type": match_type,
            "action": action,
            "merge_plan": merge_plan,
            "preconditions": preconditions,
            "mapping_hash": mapping_hash,
            "preview_revision": preview_revision,
        }
        if row_locator is not None:
            payload["row_locator"] = row_locator
        return PlannedImportRow(
            matched_influencer_id=matched_influencer_id,
            matched_platform_account_id=matched_platform_account_id,
            match_type=match_type,
            action=action,
            merge_plan=merge_plan,
            warnings=warnings,
            errors=errors,
            plan_hash=hash_document(payload),
        )

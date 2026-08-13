"""Pure Phase 2 contracts for unified preview interpretation and hashing.

The module deliberately has no persistence, worker, or HTTP dependencies.  It
turns already-canonical incoming records and already-planned rows into frozen,
JSON-safe evidence that higher layers can persist without reimplementing the
Phase 2 rules.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BeforeValidator,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from backend_core.imports.contracts import CanonicalInfluencerRecord, FrozenContract
from backend_core.imports.enums import (
    ImportJobFileStatus,
    ImportMatchType,
    ImportRowAction,
    ImportSourceType,
    SourceAcquiredAtOrigin,
)
from backend_core.imports.hashing import canonical_json, canonical_value, hash_document
from backend_core.influencers.enums import ContactType, ContactValidationStatus, Platform

CanonicalJsonValue = Annotated[JsonValue, BeforeValidator(canonical_value)]
NonnegativeStrictInt = Annotated[StrictInt, Field(ge=0)]
PositiveStrictInt = Annotated[StrictInt, Field(gt=0)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
SafeCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,79}$")]


class ScreeningResult(StrEnum):
    MATCH = "MATCH"
    NOT_MATCH = "NOT_MATCH"
    UNKNOWN = "UNKNOWN"


class ScreeningRulePayload(FrozenContract):
    """The only structured screening-rule schema supported by the MVP."""

    schema_version: Literal[1] = 1
    platforms: tuple[Platform, ...] = ()
    source_tags_exact_any: tuple[str, ...] = ()

    @field_validator("platforms")
    @classmethod
    def validate_platforms(cls, value: tuple[Platform, ...]) -> tuple[Platform, ...]:
        if len(value) != len(set(value)):
            raise ValueError("platforms must not contain duplicates")
        return tuple(sorted(value, key=lambda platform: platform.value))

    @field_validator("source_tags_exact_any", mode="before")
    @classmethod
    def validate_tags(cls, value: object) -> object:
        if value is None:
            raise ValueError("source_tags_exact_any must be an array")
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValueError("source_tags_exact_any must be an array")
        result: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("source tag items must be strings")
            normalized = item.strip()
            if not normalized:
                raise ValueError("source tag items must not be empty after trim")
            if len(normalized) > 160:
                raise ValueError("source tag items must not exceed 160 characters")
            result.append(normalized)
        if len(result) != len(set(result)):
            raise ValueError("source tags must not contain duplicates after trim")
        return tuple(result)


class ScreeningRuleSnapshot(FrozenContract):
    rules: ScreeningRulePayload
    rule_revision: PositiveStrictInt
    follower_min: NonnegativeStrictInt | None = None
    follower_max: NonnegativeStrictInt | None = None

    @model_validator(mode="after")
    def validate_range(self) -> ScreeningRuleSnapshot:
        if (
            self.follower_min is not None
            and self.follower_max is not None
            and self.follower_min > self.follower_max
        ):
            raise ValueError("follower_min must not exceed follower_max")
        return self

    @property
    def rule_hash(self) -> str:
        return hash_document(
            {
                "screening_rules": self.rules.model_dump(mode="json"),
                "screening_rules_revision": self.rule_revision,
                "follower_min": self.follower_min,
                "follower_max": self.follower_max,
            }
        )


class ScreeningRuleEvidence(FrozenContract):
    rule: Literal["platforms", "source_tags_exact_any", "followers"]
    result: ScreeningResult
    configured: CanonicalJsonValue
    observed: CanonicalJsonValue
    reason: SafeCode


class ScreeningEvaluation(FrozenContract):
    result: ScreeningResult
    rule_schema_version: Literal[1]
    rule_revision: PositiveStrictInt
    rule_hash: Sha256Hex
    evidence: tuple[ScreeningRuleEvidence, ...]


def _screen_platform(
    record: CanonicalInfluencerRecord,
    configured: tuple[Platform, ...],
) -> ScreeningRuleEvidence:
    observed = record.platform_identity.platform
    result = ScreeningResult.MATCH if observed in configured else ScreeningResult.NOT_MATCH
    return ScreeningRuleEvidence(
        rule="platforms",
        result=result,
        configured=[platform.value for platform in configured],
        observed=observed.value,
        reason="PLATFORM_MATCH" if result is ScreeningResult.MATCH else "PLATFORM_NOT_MATCH",
    )


def _screen_tags(
    record: CanonicalInfluencerRecord,
    configured: tuple[str, ...],
) -> ScreeningRuleEvidence:
    raw_tags = record.public_profile.get("creator_tags")
    observed: list[str] | None = None
    if isinstance(raw_tags, (list, tuple)) and all(isinstance(item, str) for item in raw_tags):
        usable = [item.strip() for item in raw_tags if item.strip()]
        if usable:
            observed = usable
    if observed is None:
        return ScreeningRuleEvidence(
            rule="source_tags_exact_any",
            result=ScreeningResult.UNKNOWN,
            configured=list(configured),
            observed=None,
            reason="SOURCE_TAGS_MISSING_OR_INVALID",
        )
    result = (
        ScreeningResult.MATCH
        if set(observed).intersection(configured)
        else ScreeningResult.NOT_MATCH
    )
    return ScreeningRuleEvidence(
        rule="source_tags_exact_any",
        result=result,
        configured=list(configured),
        observed=observed,
        reason="SOURCE_TAG_MATCH" if result is ScreeningResult.MATCH else "SOURCE_TAG_NOT_MATCH",
    )


def _screen_followers(
    record: CanonicalInfluencerRecord,
    minimum: int | None,
    maximum: int | None,
) -> ScreeningRuleEvidence:
    followers = record.metrics.get("followers_count")
    configured = {"min": minimum, "max": maximum}
    if type(followers) is not int or followers < 0:
        return ScreeningRuleEvidence(
            rule="followers",
            result=ScreeningResult.UNKNOWN,
            configured=configured,
            observed=None,
            reason="FOLLOWERS_MISSING_OR_INVALID",
        )
    matches = (minimum is None or followers >= minimum) and (
        maximum is None or followers <= maximum
    )
    result = ScreeningResult.MATCH if matches else ScreeningResult.NOT_MATCH
    return ScreeningRuleEvidence(
        rule="followers",
        result=result,
        configured=configured,
        observed=followers,
        reason="FOLLOWERS_MATCH" if matches else "FOLLOWERS_NOT_MATCH",
    )


def evaluate_screening(
    record: CanonicalInfluencerRecord,
    snapshot: ScreeningRuleSnapshot,
) -> ScreeningEvaluation:
    """Evaluate configured rules against incoming data only, using three states."""

    evidence: list[ScreeningRuleEvidence] = []
    if snapshot.rules.platforms:
        evidence.append(_screen_platform(record, snapshot.rules.platforms))
    if snapshot.rules.source_tags_exact_any:
        evidence.append(_screen_tags(record, snapshot.rules.source_tags_exact_any))
    if snapshot.follower_min is not None or snapshot.follower_max is not None:
        evidence.append(_screen_followers(record, snapshot.follower_min, snapshot.follower_max))

    outcomes = {item.result for item in evidence}
    if ScreeningResult.NOT_MATCH in outcomes:
        result = ScreeningResult.NOT_MATCH
    elif not evidence or ScreeningResult.UNKNOWN in outcomes:
        result = ScreeningResult.UNKNOWN
    else:
        result = ScreeningResult.MATCH
    return ScreeningEvaluation(
        result=result,
        rule_schema_version=snapshot.rules.schema_version,
        rule_revision=snapshot.rule_revision,
        rule_hash=snapshot.rule_hash,
        evidence=tuple(evidence),
    )


class ChangeScope(StrEnum):
    ACCOUNT = "account"
    SOURCE_STATE = "source_state"
    SOURCE_IDENTITY = "source_identity"
    CURRENT_METRICS = "current_metrics"
    METRIC_SNAPSHOT = "metric_snapshot"
    FRESHNESS = "freshness"


class ChangeEffect(StrEnum):
    APPLY = "apply"
    IGNORE = "ignore"
    OBSERVE = "observe"
    HISTORY = "history"


class ContactOperation(StrEnum):
    CREATE = "create"
    DEACTIVATE = "deactivate"
    MARK_POSSIBLE_DUPLICATE = "mark_possible_duplicate"
    OBSERVE = "observe"


_SENSITIVE_FIELD = re.compile(
    r"email|phone|mobile|contact|normalized.?value|password|secret|token",
    re.IGNORECASE,
)
_EMAIL_VALUE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_PHONE_VALUE = re.compile(r"(?<!\d)\+?\d(?:[\d\s().-]{5,}\d)(?!\d)")
_DECIMAL_VALUE = re.compile(r"^-?\d+\.\d+$")
_ISO_DATETIME_VALUE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?$"
)


def _contains_sensitive_value(value: JsonValue) -> bool:
    if isinstance(value, str):
        stripped = value.strip()
        phone_like = (
            not _DECIMAL_VALUE.fullmatch(stripped)
            and not _ISO_DATETIME_VALUE.fullmatch(stripped)
            and any(
                7 <= sum(character.isdigit() for character in match.group()) <= 15
                for match in _PHONE_VALUE.finditer(stripped)
            )
        )
        return bool(_EMAIL_VALUE.search(stripped) or phone_like)
    if isinstance(value, list):
        return any(_contains_sensitive_value(item) for item in value)
    if isinstance(value, dict):
        return any(
            _SENSITIVE_FIELD.search(key) is not None or _contains_sensitive_value(item)
            for key, item in value.items()
        )
    return False


def _safe_summary_value(value: Any) -> JsonValue:
    """Canonicalize display evidence and redact embedded Contact-like values.

    Field names such as ``bio`` or a source tag are not Contact fields, but a
    user can still place an email or phone number inside them.  A Preview must
    remain buildable while ensuring the shared Change Summary never becomes a
    second path for Contact disclosure.
    """

    canonical = canonical_value(value)
    return "[REDACTED]" if _contains_sensitive_value(canonical) else canonical


def _safe_summary_strings(values: Iterable[str]) -> tuple[str, ...]:
    return tuple("[REDACTED]" if _contains_sensitive_value(value) else value for value in values)


class FieldChange(FrozenContract):
    scope: ChangeScope
    field: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    before: CanonicalJsonValue = None
    incoming: CanonicalJsonValue = None
    after: CanonicalJsonValue = None
    effect: ChangeEffect
    reason: SafeCode
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    @model_validator(mode="after")
    def prevent_sensitive_data(self) -> FieldChange:
        if _SENSITIVE_FIELD.search(self.field):
            raise ValueError("sensitive fields must use the redacted ContactChange contract")
        if any(
            _contains_sensitive_value(value) for value in (self.before, self.incoming, self.after)
        ):
            raise ValueError("sensitive values are forbidden in field change summaries")
        if _contains_sensitive_value(list(self.added)) or _contains_sensitive_value(
            list(self.removed)
        ):
            raise ValueError("sensitive values are forbidden in field change summaries")
        if self.added != tuple(sorted(set(self.added))):
            raise ValueError("added values must be unique and sorted")
        if self.removed != tuple(sorted(set(self.removed))):
            raise ValueError("removed values must be unique and sorted")
        return self


class ContactChange(FrozenContract):
    scope: Literal["contact"] = "contact"
    contact_type: ContactType
    operation: ContactOperation
    validation_status: ContactValidationStatus | None = None
    count: PositiveStrictInt
    possible_duplicate: StrictBool = False


type ChangeItem = FieldChange | ContactChange


def _change_sort_key(change: ChangeItem) -> tuple[str, str, str]:
    scope: str
    if isinstance(change, ContactChange):
        identity = f"{change.contact_type.value}:{change.operation.value}"
        scope = change.scope
    else:
        identity = f"{change.scope.value}:{change.field}"
        scope = change.scope.value
    return (scope, identity, canonical_json(change.model_dump(mode="json")))


class ChangeSummary(FrozenContract):
    effective_changes: tuple[ChangeItem, ...] = ()
    ignored_changes: tuple[ChangeItem, ...] = ()
    freshness_changes: tuple[ChangeItem, ...] = ()
    historical_observations: tuple[ChangeItem, ...] = ()

    @field_validator(
        "effective_changes",
        "ignored_changes",
        "freshness_changes",
        "historical_observations",
    )
    @classmethod
    def sort_changes(cls, value: tuple[ChangeItem, ...]) -> tuple[ChangeItem, ...]:
        return tuple(sorted(value, key=_change_sort_key))

    @model_validator(mode="after")
    def validate_bucket_semantics(self) -> ChangeSummary:
        expected = (
            (self.effective_changes, {ChangeEffect.APPLY}),
            (self.ignored_changes, {ChangeEffect.IGNORE}),
            (self.freshness_changes, {ChangeEffect.OBSERVE}),
            (self.historical_observations, {ChangeEffect.HISTORY}),
        )
        for changes, effects in expected:
            if any(
                isinstance(change, FieldChange) and change.effect not in effects
                for change in changes
            ):
                raise ValueError("change effect does not match its summary bucket")
        contact_buckets = (
            (self.effective_changes, False),
            (self.ignored_changes, None),
            (self.freshness_changes, None),
            (self.historical_observations, True),
        )
        for changes, requires_observe in contact_buckets:
            for change in changes:
                if not isinstance(change, ContactChange):
                    continue
                is_observe = change.operation is ContactOperation.OBSERVE
                if requires_observe is None or is_observe != requires_observe:
                    raise ValueError("contact operation does not match its summary bucket")
        return self


def _canonical_tag_set(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("source tags must be strings")
        item = value.strip()
        if item:
            normalized.append(item)
    return tuple(sorted(set(normalized)))


def build_tag_change(
    *,
    scope: ChangeScope,
    field: str,
    before: Iterable[str],
    incoming: Iterable[str],
    effect: ChangeEffect,
    reason: str,
) -> FieldChange:
    previous = set(_canonical_tag_set(before))
    current = set(_canonical_tag_set(incoming))
    return FieldChange(
        scope=scope,
        field=field,
        effect=effect,
        reason=reason,
        added=tuple(sorted(current - previous)),
        removed=tuple(sorted(previous - current)),
    )


def change_summary_hash(summary: ChangeSummary) -> str:
    return hash_document(summary.model_dump(mode="json"))


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _canonical_incoming_account_values(
    record: CanonicalInfluencerRecord,
) -> dict[str, Any]:
    identity = record.platform_identity
    values: dict[str, Any] = {
        "display_name": record.display_name,
        "account_handle": identity.account_handle,
        "profile_url": identity.profile_url,
        "normalized_profile_url": identity.normalized_profile_url,
        **record.public_profile,
    }
    return {key: value for key, value in values.items() if value is not None}


def _append_projection_differences(
    *,
    scope: ChangeScope,
    incoming: Mapping[str, Any],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    effective: list[ChangeItem],
    ignored: list[ChangeItem],
    effective_reason: str,
    ignored_reason: str,
) -> None:
    for field in sorted(set(before).union(after).union(incoming)):
        previous = before.get(field)
        result = after.get(field)
        incoming_value = incoming.get(field)
        if previous != result:
            if (
                field in {"source_tags", "creator_tags"}
                and isinstance(previous, (list, tuple))
                and isinstance(result, (list, tuple))
            ):
                tag_change = build_tag_change(
                    scope=scope,
                    field=field,
                    before=_safe_summary_strings(previous),
                    incoming=_safe_summary_strings(result),
                    effect=ChangeEffect.APPLY,
                    reason=effective_reason,
                )
                if tag_change.added or tag_change.removed:
                    effective.append(tag_change)
            else:
                effective.append(
                    FieldChange(
                        scope=scope,
                        field=field,
                        before=_safe_summary_value(previous),
                        incoming=_safe_summary_value(incoming_value),
                        after=_safe_summary_value(result),
                        effect=ChangeEffect.APPLY,
                        reason=effective_reason,
                    )
                )
        elif field in incoming and incoming_value != result:
            if (
                field in {"source_tags", "creator_tags"}
                and isinstance(previous, (list, tuple))
                and isinstance(incoming_value, (list, tuple))
            ):
                tag_change = build_tag_change(
                    scope=scope,
                    field=field,
                    before=_safe_summary_strings(previous),
                    incoming=_safe_summary_strings(incoming_value),
                    effect=ChangeEffect.IGNORE,
                    reason=ignored_reason,
                )
                if tag_change.added or tag_change.removed:
                    ignored.append(tag_change)
            else:
                ignored.append(
                    FieldChange(
                        scope=scope,
                        field=field,
                        before=_safe_summary_value(previous),
                        incoming=_safe_summary_value(incoming_value),
                        after=_safe_summary_value(result),
                        effect=ChangeEffect.IGNORE,
                        reason=ignored_reason,
                    )
                )


def _contact_changes(contact_plan: Mapping[str, Any]) -> tuple[list[ChangeItem], list[ChangeItem]]:
    effective: list[ChangeItem] = []
    historical: list[ChangeItem] = []
    grouped_creates: dict[tuple[ContactType, ContactValidationStatus | None, bool], int] = {}
    creates = contact_plan.get("create")
    if isinstance(creates, list):
        for item in creates:
            if not isinstance(item, Mapping):
                continue
            raw_contact_type = item.get("type")
            try:
                contact_type = (
                    ContactType(raw_contact_type)
                    if isinstance(raw_contact_type, str)
                    else ContactType.OTHER
                )
            except (TypeError, ValueError):
                contact_type = ContactType.OTHER
            raw_status = item.get("validation_status")
            try:
                status = ContactValidationStatus(raw_status) if raw_status is not None else None
            except (TypeError, ValueError):
                status = None
            duplicate = item.get("possible_duplicate_contact") is True
            key = (contact_type, status, duplicate)
            grouped_creates[key] = grouped_creates.get(key, 0) + 1
    for (contact_type, status, duplicate), count in grouped_creates.items():
        effective.append(
            ContactChange(
                contact_type=contact_type,
                operation=ContactOperation.CREATE,
                validation_status=status,
                count=count,
                possible_duplicate=duplicate,
            )
        )

    operations = (
        ("deactivate_ids", ContactOperation.DEACTIVATE, effective),
        ("mark_duplicate_ids", ContactOperation.MARK_POSSIBLE_DUPLICATE, effective),
        ("observe_ids", ContactOperation.OBSERVE, historical),
    )
    for field, operation, bucket in operations:
        identifiers = contact_plan.get(field)
        if isinstance(identifiers, list) and identifiers:
            bucket.append(
                ContactChange(
                    contact_type=ContactType.EMAIL,
                    operation=operation,
                    count=len(set(str(identifier) for identifier in identifiers)),
                    possible_duplicate=operation is ContactOperation.MARK_POSSIBLE_DUPLICATE,
                )
            )
    return effective, historical


def build_change_summary(
    record: CanonicalInfluencerRecord,
    merge_plan: Mapping[str, Any],
    preconditions: Mapping[str, Any],
    *,
    source_acquired_at: datetime | None,
    previous_source_acquired_at: datetime | None,
    existing_source_data: Mapping[str, Any] | None = None,
    existing_metrics: Mapping[str, Any] | None = None,
) -> ChangeSummary:
    """Explain an existing Phase 1B merge plan without changing its decisions.

    Planner preconditions intentionally store only hashes for existing source
    data and metrics.  Callers using the bulk-prefetch path should therefore
    pass the two explicit projections.  When they are unavailable, this helper
    emits truthful hash-level evidence instead of fabricating ``before=null``.
    Contact plan values are never copied into the result.
    """

    for acquisition_time in (source_acquired_at, previous_source_acquired_at):
        if acquisition_time is not None and (
            acquisition_time.tzinfo is None or acquisition_time.utcoffset() is None
        ):
            raise ValueError("source acquisition time must include a timezone")

    effective: list[ChangeItem] = []
    ignored: list[ChangeItem] = []
    freshness: list[ChangeItem] = []
    historical: list[ChangeItem] = []

    account_before = _mapping(preconditions.get("account"))
    account_create = _mapping(merge_plan.get("account_create"))
    account_updates = _mapping(merge_plan.get("account_updates"))
    if account_create:
        for field, value in sorted(account_create.items()):
            if value is None:
                continue
            effective.append(
                FieldChange(
                    scope=ChangeScope.ACCOUNT,
                    field=field,
                    before=None,
                    incoming=_safe_summary_value(value),
                    after=_safe_summary_value(value),
                    effect=ChangeEffect.APPLY,
                    reason="ACCOUNT_CREATED",
                )
            )
    for field, value in sorted(account_updates.items()):
        before = account_before.get(field)
        if (
            field == "source_tags"
            and isinstance(before, (list, tuple))
            and isinstance(value, (list, tuple))
        ):
            change = build_tag_change(
                scope=ChangeScope.ACCOUNT,
                field=field,
                before=_safe_summary_strings(before),
                incoming=_safe_summary_strings(value),
                effect=ChangeEffect.APPLY,
                reason="ACCOUNT_FIELD_APPLIED",
            )
            if change.added or change.removed:
                effective.append(change)
        elif before != value:
            effective.append(
                FieldChange(
                    scope=ChangeScope.ACCOUNT,
                    field=field,
                    before=_safe_summary_value(before),
                    incoming=_safe_summary_value(value),
                    after=_safe_summary_value(value),
                    effect=ChangeEffect.APPLY,
                    reason="ACCOUNT_FIELD_APPLIED",
                )
            )

    incoming_source = _canonical_incoming_account_values(record)
    source_plan = _mapping(merge_plan.get("source_state"))
    source_precondition = _mapping(preconditions.get("source_state"))
    after_source = _mapping(source_plan.get("source_data"))
    if source_plan and after_source:
        known_before_source = existing_source_data
        if known_before_source is None and not source_precondition:
            known_before_source = {}
        if known_before_source is not None:
            _append_projection_differences(
                scope=ChangeScope.SOURCE_STATE,
                incoming=incoming_source,
                before=known_before_source,
                after=after_source,
                effective=effective,
                ignored=ignored,
                effective_reason="SOURCE_STATE_APPLIED",
                ignored_reason="SOURCE_VALUE_RETAINED",
            )
        else:
            previous_hash = source_precondition.get("source_data_hash")
            next_hash = source_plan.get("source_data_hash")
            if previous_hash != next_hash:
                effective.append(
                    FieldChange(
                        scope=ChangeScope.SOURCE_STATE,
                        field="source_data_hash",
                        before=previous_hash,
                        incoming=next_hash,
                        after=next_hash,
                        effect=ChangeEffect.APPLY,
                        reason="SOURCE_STATE_HASH_CHANGED",
                    )
                )
        previous_source_time = source_precondition.get("source_updated_at")
        next_source_time = source_plan.get("source_updated_at")
        if (
            "source_updated_at" in source_precondition or not source_precondition
        ) and previous_source_time != next_source_time:
            effective.append(
                FieldChange(
                    scope=ChangeScope.SOURCE_STATE,
                    field="source_updated_at",
                    before=previous_source_time,
                    incoming=record.source_updated_at,
                    after=next_source_time,
                    effect=ChangeEffect.APPLY,
                    reason="SOURCE_TIME_APPLIED",
                )
            )
    elif existing_source_data is not None:
        _append_projection_differences(
            scope=ChangeScope.SOURCE_STATE,
            incoming=incoming_source,
            before=existing_source_data,
            after=existing_source_data,
            effective=effective,
            ignored=ignored,
            effective_reason="SOURCE_STATE_APPLIED",
            ignored_reason="SOURCE_VALUE_RETAINED",
        )

    source_identity = _mapping(merge_plan.get("source_identity"))
    if source_identity:
        external_id = source_identity.get("external_account_id")
        if external_id is not None:
            effective.append(
                FieldChange(
                    scope=ChangeScope.SOURCE_IDENTITY,
                    field="external_account_id",
                    before=None,
                    incoming=_safe_summary_value(external_id),
                    after=_safe_summary_value(external_id),
                    effect=ChangeEffect.APPLY,
                    reason="SOURCE_IDENTITY_CREATED",
                )
            )

    metrics_plan = _mapping(merge_plan.get("metrics"))
    current_plan = _mapping(metrics_plan.get("current"))
    after_metrics = _mapping(current_plan.get("metrics"))
    metrics_precondition = _mapping(preconditions.get("metrics"))
    current_precondition = _mapping(metrics_precondition.get("current"))
    if current_plan and after_metrics:
        known_before_metrics = existing_metrics
        if known_before_metrics is None and not current_precondition:
            known_before_metrics = {}
        if known_before_metrics is not None:
            _append_projection_differences(
                scope=ChangeScope.CURRENT_METRICS,
                incoming=record.metrics,
                before=known_before_metrics,
                after=after_metrics,
                effective=effective,
                ignored=ignored,
                effective_reason="CURRENT_METRICS_APPLIED",
                ignored_reason="METRIC_VALUE_RETAINED",
            )
        else:
            previous_hash = current_precondition.get("metrics_hash")
            next_hash = current_plan.get("metrics_hash")
            if previous_hash != next_hash:
                effective.append(
                    FieldChange(
                        scope=ChangeScope.CURRENT_METRICS,
                        field="metrics_hash",
                        before=previous_hash,
                        incoming=next_hash,
                        after=next_hash,
                        effect=ChangeEffect.APPLY,
                        reason="CURRENT_METRICS_HASH_CHANGED",
                    )
                )
    elif existing_metrics is not None:
        _append_projection_differences(
            scope=ChangeScope.CURRENT_METRICS,
            incoming=record.metrics,
            before=existing_metrics,
            after=existing_metrics,
            effective=effective,
            ignored=ignored,
            effective_reason="CURRENT_METRICS_APPLIED",
            ignored_reason="METRIC_VALUE_RETAINED",
        )

    snapshot_plan = _mapping(metrics_plan.get("snapshot"))
    if snapshot_plan:
        historical.append(
            FieldChange(
                scope=ChangeScope.METRIC_SNAPSHOT,
                field="metrics",
                before=None,
                incoming=_safe_summary_value(snapshot_plan.get("metrics")),
                after=None,
                effect=ChangeEffect.HISTORY,
                reason="METRIC_SNAPSHOT_CREATED",
            )
        )

    contact_effective, contact_historical = _contact_changes(_mapping(merge_plan.get("contacts")))
    effective.extend(contact_effective)
    historical.extend(contact_historical)

    if source_acquired_at is not None and (
        previous_source_acquired_at is None or source_acquired_at > previous_source_acquired_at
    ):
        freshness.append(
            FieldChange(
                scope=ChangeScope.FRESHNESS,
                field="source_acquired_at",
                before=previous_source_acquired_at,
                incoming=source_acquired_at,
                after=source_acquired_at,
                effect=ChangeEffect.OBSERVE,
                reason="OBSERVATION_ADVANCED",
            )
        )

    return ChangeSummary(
        effective_changes=tuple(effective),
        ignored_changes=tuple(ignored),
        freshness_changes=tuple(freshness),
        historical_observations=tuple(historical),
    )


def has_effective_changes(summary: ChangeSummary) -> bool:
    """Return whether Confirm would change current/business state."""

    return bool(summary.effective_changes)


class PreviewRowFact(FrozenContract):
    action: ImportRowAction
    has_warning: StrictBool = False
    is_batch_duplicate: StrictBool = False
    possible_duplicate_contact: StrictBool = False
    screening_result: ScreeningResult | None

    @model_validator(mode="after")
    def validate_shape(self) -> PreviewRowFact:
        if self.is_batch_duplicate and self.action is not ImportRowAction.SKIP:
            raise ValueError("a batch duplicate must use the skip action")
        if self.action is ImportRowAction.SKIP and not self.is_batch_duplicate:
            raise ValueError("the unified preview skip action requires a batch duplicate")
        does_not_screen = self.action is ImportRowAction.ERROR or self.is_batch_duplicate
        if does_not_screen and self.screening_result is not None:
            raise ValueError("error and duplicate rows must not carry screening results")
        if not does_not_screen and self.screening_result is None:
            raise ValueError("valid unique rows must carry a screening result")
        return self


class UnifiedPreviewSummary(FrozenContract):
    file_count: NonnegativeStrictInt
    occurrence_count: NonnegativeStrictInt
    excluded_file_count: NonnegativeStrictInt
    raw_rows: NonnegativeStrictInt
    unique_rows: NonnegativeStrictInt
    internal_duplicate_rows: NonnegativeStrictInt
    existing_rows: NonnegativeStrictInt
    new_rows: NonnegativeStrictInt
    changed_rows: NonnegativeStrictInt
    no_change_rows: NonnegativeStrictInt
    created_rows: NonnegativeStrictInt
    updated_rows: NonnegativeStrictInt
    skipped_rows: NonnegativeStrictInt
    error_rows: NonnegativeStrictInt
    manual_review_rows: NonnegativeStrictInt
    warning_rows: NonnegativeStrictInt
    possible_duplicate_contact_rows: NonnegativeStrictInt
    screened_rows: NonnegativeStrictInt
    screening_match_rows: NonnegativeStrictInt
    screening_not_match_rows: NonnegativeStrictInt
    screening_unknown_rows: NonnegativeStrictInt

    @model_validator(mode="after")
    def validate_invariants(self) -> UnifiedPreviewSummary:
        if self.occurrence_count != self.file_count + self.excluded_file_count:
            raise ValueError("occurrence_count must equal file_count plus excluded_file_count")
        action_total = (
            self.created_rows
            + self.updated_rows
            + self.no_change_rows
            + self.skipped_rows
            + self.error_rows
            + self.manual_review_rows
        )
        if self.raw_rows != action_total:
            raise ValueError("raw_rows must equal the action partition")
        if self.internal_duplicate_rows > self.skipped_rows:
            raise ValueError("internal_duplicate_rows must be a subset of skipped_rows")
        if self.unique_rows != self.raw_rows - self.internal_duplicate_rows:
            raise ValueError("unique_rows must equal raw_rows minus internal_duplicate_rows")
        if self.new_rows != self.created_rows:
            raise ValueError("new_rows must equal created_rows")
        if self.changed_rows != self.updated_rows:
            raise ValueError("changed_rows must equal updated_rows")
        if self.existing_rows != self.changed_rows + self.no_change_rows:
            raise ValueError("existing_rows must equal changed_rows plus no_change_rows")
        screening_total = (
            self.screening_match_rows + self.screening_not_match_rows + self.screening_unknown_rows
        )
        if self.screened_rows != screening_total:
            raise ValueError("screened_rows must equal the screening result partition")
        if self.screened_rows > self.unique_rows:
            raise ValueError("screened_rows cannot exceed unique_rows")
        if self.screened_rows != self.unique_rows - self.error_rows:
            raise ValueError("screened_rows must equal unique non-error rows")
        if self.warning_rows > self.raw_rows:
            raise ValueError("warning_rows cannot exceed raw_rows")
        if self.possible_duplicate_contact_rows > self.raw_rows:
            raise ValueError("possible duplicate rows cannot exceed raw_rows")
        return self


def summarize_preview(
    *,
    occurrence_count: int,
    included_file_count: int,
    excluded_file_count: int,
    rows: Iterable[PreviewRowFact],
) -> UnifiedPreviewSummary:
    facts = tuple(rows)
    action_count = {action: 0 for action in ImportRowAction}
    screening_count = {result: 0 for result in ScreeningResult}
    for fact in facts:
        action_count[fact.action] += 1
        if fact.screening_result is not None:
            screening_count[fact.screening_result] += 1
    duplicate_count = sum(fact.is_batch_duplicate for fact in facts)
    return UnifiedPreviewSummary(
        file_count=included_file_count,
        occurrence_count=occurrence_count,
        excluded_file_count=excluded_file_count,
        raw_rows=len(facts),
        unique_rows=len(facts) - duplicate_count,
        internal_duplicate_rows=duplicate_count,
        existing_rows=(
            action_count[ImportRowAction.UPDATE] + action_count[ImportRowAction.NO_CHANGE]
        ),
        new_rows=action_count[ImportRowAction.CREATE],
        changed_rows=action_count[ImportRowAction.UPDATE],
        no_change_rows=action_count[ImportRowAction.NO_CHANGE],
        created_rows=action_count[ImportRowAction.CREATE],
        updated_rows=action_count[ImportRowAction.UPDATE],
        skipped_rows=action_count[ImportRowAction.SKIP],
        error_rows=action_count[ImportRowAction.ERROR],
        manual_review_rows=action_count[ImportRowAction.MANUAL_REVIEW],
        warning_rows=sum(fact.has_warning for fact in facts),
        possible_duplicate_contact_rows=sum(fact.possible_duplicate_contact for fact in facts),
        screened_rows=sum(screening_count.values()),
        screening_match_rows=screening_count[ScreeningResult.MATCH],
        screening_not_match_rows=screening_count[ScreeningResult.NOT_MATCH],
        screening_unknown_rows=screening_count[ScreeningResult.UNKNOWN],
    )


class ImportRowCategory(StrEnum):
    ATTENTION = "attention"
    ERROR = "error"
    MANUAL_REVIEW = "manual_review"
    WARNING = "warning"
    CHANGED = "changed"
    NEW = "new"
    NO_CHANGE = "no_change"
    DUPLICATE = "duplicate"
    ALL = "all"


def row_matches_category(fact: PreviewRowFact, category: ImportRowCategory) -> bool:
    predicates = {
        ImportRowCategory.ATTENTION: (
            fact.action in {ImportRowAction.ERROR, ImportRowAction.MANUAL_REVIEW}
            or fact.has_warning
        ),
        ImportRowCategory.ERROR: fact.action is ImportRowAction.ERROR,
        ImportRowCategory.MANUAL_REVIEW: fact.action is ImportRowAction.MANUAL_REVIEW,
        ImportRowCategory.WARNING: fact.has_warning,
        ImportRowCategory.CHANGED: fact.action is ImportRowAction.UPDATE,
        ImportRowCategory.NEW: fact.action is ImportRowAction.CREATE,
        ImportRowCategory.NO_CHANGE: fact.action is ImportRowAction.NO_CHANGE,
        ImportRowCategory.DUPLICATE: fact.is_batch_duplicate,
        ImportRowCategory.ALL: True,
    }
    return predicates[category]


def row_category_priority(fact: PreviewRowFact) -> int:
    if fact.action is ImportRowAction.ERROR:
        return 0
    if fact.action is ImportRowAction.MANUAL_REVIEW:
        return 1
    if fact.has_warning:
        return 2
    if fact.action is ImportRowAction.UPDATE:
        return 3
    if fact.action is ImportRowAction.CREATE:
        return 4
    if fact.action is ImportRowAction.NO_CHANGE:
        return 5
    return 6


# Backward-compatible internal name for the pure Preview-domain tests.  Both
# imports refer to the same enum object; there is one authoritative category
# enum shared by domain, repository, service, and HTTP.
PreviewRowCategory = ImportRowCategory


class PreviewFileManifestEntry(FrozenContract):
    import_job_file_id: UUID
    position: PositiveStrictInt
    stored_file_sha256: Sha256Hex
    mapping_hash: Sha256Hex | None
    status: ImportJobFileStatus
    included: StrictBool
    source_acquired_at: datetime | None
    source_acquired_at_origin: SourceAcquiredAtOrigin
    source_acquired_at_confirmation_required: StrictBool

    @field_validator("source_acquired_at")
    @classmethod
    def require_acquisition_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("source acquisition time must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_preview_eligibility(self) -> PreviewFileManifestEntry:
        if self.included:
            if self.status is not ImportJobFileStatus.READY:
                raise ValueError("included manifest files must be ready")
            if self.mapping_hash is None:
                raise ValueError("included manifest files must have a mapping hash")
            if self.source_acquired_at is None:
                raise ValueError("included manifest files must have acquisition time")
        elif self.status is not ImportJobFileStatus.EXCLUDED:
            raise ValueError("non-included manifest files must be excluded")
        if (self.source_acquired_at_origin is SourceAcquiredAtOrigin.LEGACY_UNKNOWN) != (
            self.source_acquired_at is None
        ):
            raise ValueError("acquisition origin and time are inconsistent")
        if self.source_acquired_at_confirmation_required and (
            self.source_acquired_at is None
            or self.source_acquired_at_origin is not SourceAcquiredAtOrigin.SERVER_DEFAULT
        ):
            raise ValueError("acquisition confirmation requires a server-default acquisition time")
        return self


class PreviewRevisionContext(FrozenContract):
    import_job_id: UUID
    preview_revision: PositiveStrictInt
    collection_job_id: UUID
    source_type: ImportSourceType
    refresh_queue_id: UUID | None = None
    files: tuple[PreviewFileManifestEntry, ...]
    screening: ScreeningRuleSnapshot
    planner_version: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    relevant_config: dict[str, CanonicalJsonValue] = Field(default_factory=dict)

    @field_validator("files")
    @classmethod
    def sort_and_validate_files(
        cls, value: tuple[PreviewFileManifestEntry, ...]
    ) -> tuple[PreviewFileManifestEntry, ...]:
        if not value:
            raise ValueError("preview manifest must contain at least one occurrence")
        if len({item.position for item in value}) != len(value):
            raise ValueError("preview manifest positions must be unique")
        if len({item.import_job_file_id for item in value}) != len(value):
            raise ValueError("preview manifest file IDs must be unique")
        ordered = tuple(sorted(value, key=lambda item: item.position))
        if not any(item.included for item in ordered):
            raise ValueError("preview manifest must include at least one ready file")
        return ordered

    @property
    def context_hash(self) -> str:
        return hash_document(self.hash_payload())

    def hash_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        # Preserve the Task 1-8 context hash for ordinary Bulk imports while
        # binding every linked return plan to its one frozen Queue aggregate.
        if self.refresh_queue_id is None:
            payload.pop("refresh_queue_id", None)
        return {**payload, "screening_rule_hash": self.screening.rule_hash}


class PreviewRowLocator(FrozenContract):
    import_job_file_id: UUID
    file_position: PositiveStrictInt
    row_number: Annotated[StrictInt, Field(ge=2)]
    import_row_id: UUID

    @property
    def sort_key(self) -> tuple[int, int, str, str]:
        return (
            self.file_position,
            self.row_number,
            str(self.import_job_file_id),
            str(self.import_row_id),
        )

    @property
    def physical_identity(self) -> tuple[UUID, int]:
        return (self.import_job_file_id, self.row_number)


class PreviewRowHashInput(FrozenContract):
    context_hash: Sha256Hex
    locator: PreviewRowLocator
    normalized_data: dict[str, CanonicalJsonValue]
    action: ImportRowAction
    match_type: ImportMatchType
    matched_influencer_id: UUID | None
    matched_platform_account_id: UUID | None
    duplicate_owner_locator: PreviewRowLocator | None
    merge_plan: dict[str, CanonicalJsonValue]
    preconditions: dict[str, CanonicalJsonValue]
    screening: ScreeningEvaluation | None
    change_summary: ChangeSummary
    warnings: tuple[dict[str, CanonicalJsonValue], ...]
    errors: tuple[dict[str, CanonicalJsonValue], ...]
    manual_review: CanonicalJsonValue

    @model_validator(mode="after")
    def validate_match_pair(self) -> PreviewRowHashInput:
        if self.matched_platform_account_id is not None and self.matched_influencer_id is None:
            raise ValueError("matched platform account requires matched influencer")
        return self


def hash_preview_row(value: PreviewRowHashInput) -> str:
    return hash_document(value.model_dump(mode="json"))


class BatchPlanHashEntry(FrozenContract):
    locator: PreviewRowLocator
    row_plan_hash: Sha256Hex


def hash_batch_plan(context_hash: str, rows: Iterable[BatchPlanHashEntry]) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", context_hash) is None:
        raise ValueError("context_hash must be a lowercase SHA-256 digest")
    entries = tuple(rows)
    identities = [entry.locator.physical_identity for entry in entries]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate row locator in batch plan")
    ordered = sorted(entries, key=lambda entry: entry.locator.sort_key)
    return hash_document(
        {
            "context_hash": context_hash,
            "rows": [entry.model_dump(mode="json") for entry in ordered],
        }
    )


__all__ = [
    "BatchPlanHashEntry",
    "ChangeEffect",
    "ChangeScope",
    "ChangeSummary",
    "ContactChange",
    "ContactOperation",
    "FieldChange",
    "ImportRowCategory",
    "PreviewFileManifestEntry",
    "PreviewRevisionContext",
    "PreviewRowCategory",
    "PreviewRowFact",
    "PreviewRowHashInput",
    "PreviewRowLocator",
    "ScreeningEvaluation",
    "ScreeningResult",
    "ScreeningRuleEvidence",
    "ScreeningRulePayload",
    "ScreeningRuleSnapshot",
    "UnifiedPreviewSummary",
    "build_change_summary",
    "build_tag_change",
    "change_summary_hash",
    "evaluate_screening",
    "hash_batch_plan",
    "hash_preview_row",
    "has_effective_changes",
    "row_category_priority",
    "row_matches_category",
    "summarize_preview",
]

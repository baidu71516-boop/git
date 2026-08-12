"""Deterministic, file-aware identity graph and batch duplicate decisions.

This module is deliberately independent from persistence and HTTP concerns.  It
accepts canonical rows, connects them by every frozen hard identity, and emits
decisions that a processor can persist without reimplementing deduplication
rules.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from backend_core.imports.contracts import CanonicalInfluencerRecord
from backend_core.imports.hashing import canonical_json, hash_document
from backend_core.influencers.enums import ContactType


@dataclass(frozen=True)
class RowLocator:
    """Physical identity of one imported row within a multi-file job.

    ``file_position`` participates in ordering, while the occurrence UUID and row
    number remain the durable uniqueness boundary.  ``import_row_id`` must be
    the deterministic/persisted value so every duplicate can carry a stable
    owner reference before the rows are flushed.
    """

    import_job_file_id: UUID
    file_position: int
    row_number: int
    import_row_id: UUID

    def __post_init__(self) -> None:
        if self.file_position < 1:
            raise ValueError("file position must be at least 1")
        if self.row_number < 2:
            raise ValueError("physical row number must be at least 2")

    @property
    def identity(self) -> tuple[UUID, int]:
        """The schema-level occurrence + physical-row identity."""

        return (self.import_job_file_id, self.row_number)

    @property
    def sort_key(self) -> tuple[int, int, str]:
        return (self.file_position, self.row_number, str(self.import_job_file_id))

    @property
    def position(self) -> int:
        """Compatibility alias for the persisted ``ImportJobFile.position`` name."""

        return self.file_position

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, RowLocator):
            return NotImplemented
        return self.sort_key < other.sort_key

    def as_dict(self, *, include_import_row_id: bool = True) -> dict[str, Any]:
        value: dict[str, Any] = {
            "import_job_file_id": str(self.import_job_file_id),
            "file_position": self.file_position,
            "row_number": self.row_number,
        }
        if include_import_row_id:
            value["import_row_id"] = str(self.import_row_id)
        return value


@dataclass(frozen=True)
class BatchRow:
    locator: RowLocator
    record: CanonicalInfluencerRecord
    normalized_data: dict[str, Any]


@dataclass(frozen=True, order=True)
class DatabaseIdentityTarget:
    """Existing database target reached through one incoming hard identity."""

    platform_account_id: UUID
    influencer_id: UUID


DatabaseIdentityMatches = Mapping[str, Sequence[DatabaseIdentityTarget]]


@dataclass(frozen=True)
class IdentityGraphStats:
    row_count: int
    distinct_identity_keys: int
    identity_links: int
    union_attempts: int


@dataclass(frozen=True)
class IdentityComponent:
    group_id: str
    rows: tuple[BatchRow, ...]
    hard_identity_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.rows:
            raise ValueError("identity component must contain at least one row")
        if self.rows != tuple(sorted(self.rows, key=lambda row: row.locator.sort_key)):
            raise ValueError("identity component rows must use stable locator order")
        if self.hard_identity_keys != tuple(sorted(set(self.hard_identity_keys))):
            raise ValueError("identity component keys must be unique and sorted")

    @property
    def first_locator(self) -> RowLocator:
        return self.rows[0].locator

    @property
    def members(self) -> tuple[BatchRow, ...]:
        return self.rows

    @property
    def identity_keys(self) -> tuple[str, ...]:
        return self.hard_identity_keys


@dataclass(frozen=True)
class IdentityGraph:
    components: tuple[IdentityComponent, ...]
    stats: IdentityGraphStats


class ComponentResolutionKind(StrEnum):
    OWNER = "owner"
    MANUAL_REVIEW = "manual_review"


class ManualReviewReason(StrEnum):
    MERGE_PAYLOAD_CONFLICT = "BATCH_MERGE_PAYLOAD_CONFLICT"
    DATABASE_IDENTITY_CONFLICT = "BATCH_DATABASE_IDENTITY_CONFLICT"


@dataclass(frozen=True)
class DatabaseIdentityEvidence:
    hard_identity: str
    platform_account_id: UUID
    influencer_id: UUID

    def as_dict(self) -> dict[str, str]:
        return {
            "hard_identity": self.hard_identity,
            "platform_account_id": str(self.platform_account_id),
            "influencer_id": str(self.influencer_id),
        }


@dataclass(frozen=True)
class DuplicateAnnotation:
    group_id: str
    duplicate: RowLocator
    owner: RowLocator
    hard_identity_keys: tuple[str, ...]

    def warning(self) -> dict[str, str]:
        return {
            "code": "BATCH_DUPLICATE",
            "message": "Row duplicates another included row in this import batch",
        }

    def merge_plan(self) -> dict[str, Any]:
        owner_reference: dict[str, Any] = {
            "owner_import_job_file_id": str(self.owner.import_job_file_id),
            "owner_file_position": self.owner.file_position,
            "owner_row_number": self.owner.row_number,
        }
        owner_reference["owner_import_row_id"] = str(self.owner.import_row_id)
        return {
            "batch_duplicate": {
                "group_id": self.group_id,
                **owner_reference,
                "hard_identity_keys": list(self.hard_identity_keys),
            }
        }


@dataclass(frozen=True)
class ComponentResolution:
    component: IdentityComponent
    kind: ComponentResolutionKind
    owner: BatchRow | None
    duplicates: tuple[BatchRow, ...]
    manual_review_reason: ManualReviewReason | None = None
    conflict_fields: tuple[str, ...] = ()
    database_identity_evidence: tuple[DatabaseIdentityEvidence, ...] = ()

    def __post_init__(self) -> None:
        if self.kind == ComponentResolutionKind.OWNER:
            if self.owner is None or self.manual_review_reason is not None:
                raise ValueError("owner resolutions require an owner and no manual-review reason")
            expected_duplicates = tuple(
                row for row in self.component.rows if row.locator != self.owner.locator
            )
            if self.duplicates != expected_duplicates:
                raise ValueError("duplicates must be every non-owner row in stable order")
        else:
            if self.owner is not None or self.duplicates:
                raise ValueError("manual-review resolutions cannot choose an owner or duplicates")
            if self.manual_review_reason is None:
                raise ValueError("manual-review resolutions require a reason")

    def duplicate_annotations(self) -> tuple[DuplicateAnnotation, ...]:
        if self.owner is None:
            return ()
        return tuple(
            DuplicateAnnotation(
                group_id=self.component.group_id,
                duplicate=row.locator,
                owner=self.owner.locator,
                hard_identity_keys=self.component.hard_identity_keys,
            )
            for row in self.duplicates
        )

    @property
    def reason(self) -> ManualReviewReason | None:
        return self.manual_review_reason

    def manual_review_warning(self) -> dict[str, str] | None:
        if self.manual_review_reason is None:
            return None
        return {
            "code": self.manual_review_reason.value,
            "message": "Batch identity component requires manual review",
        }

    def manual_review_merge_plan(self) -> dict[str, Any] | None:
        if self.manual_review_reason is None:
            return None
        return {
            "batch_manual_review": {
                "group_id": self.component.group_id,
                "reason": self.manual_review_reason.value,
                "rows": [
                    row.locator.as_dict(include_import_row_id=True) for row in self.component.rows
                ],
                "hard_identity_keys": list(self.component.hard_identity_keys),
                "conflict_fields": list(self.conflict_fields),
                "database_identity_candidates": [
                    evidence.as_dict() for evidence in self.database_identity_evidence
                ],
            }
        }


@dataclass(frozen=True)
class BatchDedupSummary:
    raw_rows: int
    owner_rows: int
    internal_duplicate_rows: int
    unique_rows: int
    manual_review_rows: int
    parse_error_rows: int

    def __post_init__(self) -> None:
        values = (
            self.raw_rows,
            self.owner_rows,
            self.internal_duplicate_rows,
            self.unique_rows,
            self.manual_review_rows,
            self.parse_error_rows,
        )
        if any(value < 0 for value in values):
            raise ValueError("batch summary counts cannot be negative")
        if self.unique_rows != self.raw_rows - self.internal_duplicate_rows:
            raise ValueError("unique_rows must equal raw_rows - internal_duplicate_rows")
        if self.raw_rows != (
            self.owner_rows
            + self.internal_duplicate_rows
            + self.manual_review_rows
            + self.parse_error_rows
        ):
            raise ValueError(
                "raw_rows must partition into owner, duplicate, manual-review, and error rows"
            )


class _UnionFind:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))
        self._rank = [0] * size

    def find(self, item: int) -> int:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != item:
            parent = self._parent[item]
            self._parent[item] = root
            item = parent
        return root

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        left_rank = self._rank[left_root]
        right_rank = self._rank[right_root]
        if left_rank < right_rank:
            self._parent[left_root] = right_root
        elif left_rank > right_rank:
            self._parent[right_root] = left_root
        else:
            self._parent[right_root] = left_root
            self._rank[left_root] += 1


def hard_identity_keys(record: CanonicalInfluencerRecord) -> tuple[str, ...]:
    """Return every frozen hard identity; contacts and soft fields never appear."""

    identity = record.platform_identity
    platform = identity.platform.value
    keys: list[str] = []
    if identity.platform_account_id:
        keys.append(f"platform:{platform}:account:{identity.platform_account_id}")
    if identity.external_source_id:
        keys.append(
            f"source:{record.source.value}:platform:{platform}:external:"
            f"{identity.external_source_id}"
        )
    if identity.normalized_profile_url:
        keys.append(f"platform:{platform}:profile:{identity.normalized_profile_url}")
    return tuple(dict.fromkeys(keys))


def build_identity_graph(rows: Iterable[BatchRow]) -> IdentityGraph:
    """Build all-key connected components with near-linear union-find work."""

    ordered_rows = tuple(sorted(rows, key=lambda row: row.locator.sort_key))
    seen_locators: set[tuple[UUID, int]] = set()
    for row in ordered_rows:
        if row.locator.identity in seen_locators:
            raise ValueError("duplicate occurrence + row_number locator")
        seen_locators.add(row.locator.identity)

    union_find = _UnionFind(len(ordered_rows))
    first_row_for_key: dict[str, int] = {}
    identity_links = 0
    union_attempts = 0
    row_keys: list[tuple[str, ...]] = []
    for index, row in enumerate(ordered_rows):
        keys = hard_identity_keys(row.record)
        row_keys.append(keys)
        identity_links += len(keys)
        for key in keys:
            owner_index = first_row_for_key.setdefault(key, index)
            if owner_index != index:
                union_find.union(owner_index, index)
                union_attempts += 1

    members: dict[int, list[int]] = defaultdict(list)
    for index in range(len(ordered_rows)):
        members[union_find.find(index)].append(index)

    components: list[IdentityComponent] = []
    for member_indices in members.values():
        component_rows = tuple(ordered_rows[index] for index in member_indices)
        component_keys = tuple(sorted({key for index in member_indices for key in row_keys[index]}))
        group_document = {
            "hard_identity_keys": component_keys,
            "row_locators": [
                row.locator.as_dict(include_import_row_id=False) for row in component_rows
            ],
        }
        components.append(
            IdentityComponent(
                group_id=f"batch:{hash_document(group_document)}",
                rows=component_rows,
                hard_identity_keys=component_keys,
            )
        )
    components.sort(key=lambda component: component.first_locator.sort_key)
    return IdentityGraph(
        components=tuple(components),
        stats=IdentityGraphStats(
            row_count=len(ordered_rows),
            distinct_identity_keys=len(first_row_for_key),
            identity_links=identity_links,
            union_attempts=union_attempts,
        ),
    )


def build_identity_components(rows: Iterable[BatchRow]) -> list[IdentityComponent]:
    """Convenience API for processors that do not need graph work counters."""

    return list(build_identity_graph(rows).components)


def resolve_identity_graph(
    graph: IdentityGraph,
    *,
    database_matches: DatabaseIdentityMatches | None = None,
) -> tuple[ComponentResolution, ...]:
    matches = database_matches or {}
    return tuple(
        resolve_component(component, database_matches=matches) for component in graph.components
    )


def resolve_component(
    component: IdentityComponent,
    *,
    database_matches: DatabaseIdentityMatches | None = None,
) -> ComponentResolution:
    """Choose one immutable owner or mark the entire component for review."""

    evidence = _database_identity_evidence(component, database_matches or {})
    database_targets = {(item.platform_account_id, item.influencer_id) for item in evidence}
    if len(database_targets) > 1:
        return ComponentResolution(
            component=component,
            kind=ComponentResolutionKind.MANUAL_REVIEW,
            owner=None,
            duplicates=(),
            manual_review_reason=ManualReviewReason.DATABASE_IDENTITY_CONFLICT,
            database_identity_evidence=evidence,
        )

    rows = component.rows
    if len(rows) == 1:
        return _owner_resolution(component, rows[0])

    exact_payloads = {_exact_payload(row.record) for row in rows}
    if len(exact_payloads) == 1:
        return _owner_resolution(component, rows[0])

    source_times = tuple(_source_time(row.record.source_updated_at) for row in rows)
    if all(source_time is not None for source_time in source_times):
        reliable_times = tuple(
            source_time for source_time in source_times if source_time is not None
        )
        if len(set(reliable_times)) > 1:
            newest_time = max(reliable_times)
            newest_rows = tuple(
                row
                for row, source_time in zip(rows, source_times, strict=True)
                if source_time == newest_time
            )
            conflict_fields = _merge_conflict_fields(newest_rows)
            if conflict_fields:
                return _payload_conflict_resolution(component, conflict_fields)
            return _owner_resolution(component, newest_rows[0])

    conflict_fields = _merge_conflict_fields(rows)
    if conflict_fields:
        return _payload_conflict_resolution(component, conflict_fields)
    return _owner_resolution(component, rows[0])


def select_component_owner(
    component: IdentityComponent,
    *,
    database_matches: DatabaseIdentityMatches | None = None,
) -> ComponentResolution:
    """Integration-facing name for deterministic owner/manual-review selection."""

    return resolve_component(component, database_matches=database_matches)


def duplicate_email_values(
    rows: Iterable[BatchRow], components: Iterable[IdentityComponent]
) -> set[str]:
    """Emails shared by different components, for possible-duplicate marking only.

    The returned values must never be supplied back to the identity graph.  They
    intentionally remain outside all hard-key and owner evidence.
    """

    component_by_locator = {
        member.locator.identity: component.group_id
        for component in components
        for member in component.members
    }
    email_components: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        component_id = component_by_locator.get(row.locator.identity)
        if component_id is None:
            raise ValueError("row is absent from supplied identity components")
        for contact in row.record.contacts:
            if contact.type == ContactType.EMAIL:
                email_components[contact.normalized_value].add(component_id)
    return {email for email, component_ids in email_components.items() if len(component_ids) > 1}


def summarize_resolutions(
    resolutions: Iterable[ComponentResolution], *, parse_error_rows: int = 0
) -> BatchDedupSummary:
    if parse_error_rows < 0:
        raise ValueError("parse_error_rows cannot be negative")
    resolved = tuple(resolutions)
    owner_rows = sum(1 for item in resolved if item.kind == ComponentResolutionKind.OWNER)
    duplicate_rows = sum(len(item.duplicates) for item in resolved)
    manual_review_rows = sum(
        len(item.component.rows)
        for item in resolved
        if item.kind == ComponentResolutionKind.MANUAL_REVIEW
    )
    raw_rows = owner_rows + duplicate_rows + manual_review_rows + parse_error_rows
    return BatchDedupSummary(
        raw_rows=raw_rows,
        owner_rows=owner_rows,
        internal_duplicate_rows=duplicate_rows,
        unique_rows=raw_rows - duplicate_rows,
        manual_review_rows=manual_review_rows,
        parse_error_rows=parse_error_rows,
    )


def _owner_resolution(component: IdentityComponent, owner: BatchRow) -> ComponentResolution:
    duplicates = tuple(row for row in component.rows if row.locator != owner.locator)
    return ComponentResolution(
        component=component,
        kind=ComponentResolutionKind.OWNER,
        owner=owner,
        duplicates=duplicates,
    )


def _payload_conflict_resolution(
    component: IdentityComponent, conflict_fields: tuple[str, ...]
) -> ComponentResolution:
    return ComponentResolution(
        component=component,
        kind=ComponentResolutionKind.MANUAL_REVIEW,
        owner=None,
        duplicates=(),
        manual_review_reason=ManualReviewReason.MERGE_PAYLOAD_CONFLICT,
        conflict_fields=conflict_fields,
    )


def _database_identity_evidence(
    component: IdentityComponent, database_matches: DatabaseIdentityMatches
) -> tuple[DatabaseIdentityEvidence, ...]:
    evidence = {
        DatabaseIdentityEvidence(
            hard_identity=key,
            platform_account_id=target.platform_account_id,
            influencer_id=target.influencer_id,
        )
        for key in component.hard_identity_keys
        for target in database_matches.get(key, ())
    }
    return tuple(
        sorted(
            evidence,
            key=lambda item: (
                item.hard_identity,
                str(item.platform_account_id),
                str(item.influencer_id),
            ),
        )
    )


def _source_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _exact_payload(record: CanonicalInfluencerRecord) -> str:
    value = _merge_payload(record)
    value["source_updated_at"] = _source_time(record.source_updated_at)
    return canonical_json(value)


def _merge_payload(record: CanonicalInfluencerRecord) -> dict[str, Any]:
    """Business fields used by Phase 1B merge, excluding row-local issues."""

    return {
        "display_name": record.display_name,
        "platform_identity": record.platform_identity.as_dict(),
        "source": record.source,
        "public_profile": record.public_profile,
        "metrics": record.metrics,
        "contacts": [contact.as_dict() for contact in record.contacts],
    }


_MISSING = object()


def _merge_conflict_fields(rows: Sequence[BatchRow]) -> tuple[str, ...]:
    records = tuple(row.record for row in rows)
    fields: list[str] = []
    _add_scalar_conflict(fields, "display_name", [record.display_name for record in records])
    _add_scalar_conflict(fields, "source", [record.source for record in records])

    identity_documents = [record.platform_identity.as_dict() for record in records]
    _add_mapping_conflicts(fields, "platform_identity", identity_documents)
    _add_mapping_conflicts(fields, "public_profile", [record.public_profile for record in records])
    _add_mapping_conflicts(fields, "metrics", [record.metrics for record in records])
    _add_scalar_conflict(
        fields,
        "contacts",
        [[contact.as_dict() for contact in record.contacts] or None for record in records],
    )
    return tuple(sorted(fields))


def _add_mapping_conflicts(
    output: list[str], prefix: str, documents: Sequence[Mapping[str, Any]]
) -> None:
    for key in sorted({str(key) for document in documents for key in document}):
        values = [document.get(key, _MISSING) for document in documents]
        _add_scalar_conflict(output, f"{prefix}.{key}", values)


def _add_scalar_conflict(output: list[str], field: str, values: Sequence[Any]) -> None:
    supplied = [value for value in values if value is not _MISSING and value is not None]
    if len({canonical_json(value) for value in supplied}) > 1:
        output.append(field)


__all__ = [
    "BatchRow",
    "BatchDedupSummary",
    "ComponentResolution",
    "ComponentResolutionKind",
    "DatabaseIdentityEvidence",
    "DatabaseIdentityMatches",
    "DatabaseIdentityTarget",
    "DuplicateAnnotation",
    "IdentityComponent",
    "IdentityGraph",
    "IdentityGraphStats",
    "ManualReviewReason",
    "RowLocator",
    "build_identity_components",
    "build_identity_graph",
    "duplicate_email_values",
    "hard_identity_keys",
    "resolve_component",
    "resolve_identity_graph",
    "select_component_owner",
    "summarize_resolutions",
]

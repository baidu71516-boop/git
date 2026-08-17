# Phase 3A - Persistence Amendment A1

Status: DESIGN AMENDMENT, READY; no implementation authorized  
Date: 2026-08-17  
Baseline: `phase-3a-growth-foundation@dfb92e104ccbbcec13c958587c200387b5bc552a`  
Amends only: Frozen Contract section S and its Phase 3A Audit persistence prerequisites

## A. Gap confirmed

Confirmed from the WO1 ORM and `0006_phase3a_persistence`, not from the prompt alone.

- `candidate_pool_runs` durably stores `(pool_id, idempotency_key, request_hash)`.
- `outreach_tasks` durably stores Department-scoped create key/hash data.
- `outreach_events` durably stores Task-scoped transition key/hash data.
- `campaigns`, `campaign_members`, `candidate_pools`, `targeting_policies`, and
  `outreach_targets` have no request-key/result persistence. Campaign create and
  Campaign Member bulk-add therefore cannot satisfy same-payload replay versus
  different-payload conflict. Strict reading of frozen section S exposes the same
  missing seam for the WO2 Candidate Pool/Policy create APIs and the WO3
  OutreachTarget create API.
- The closed `AuditAction` enum has no Candidate Pool, Campaign, or Outreach action.

The existing Campaign Member uniqueness prevents duplicate surviving rows, but it
cannot reproduce the original bulk result and cannot distinguish key reuse with a
different request.

## B. Phase 3A idempotency record

Add one Phase 3A-owned, immutable table named `phase3a_idempotency_records`:

| Column | Type | Null | Meaning |
|---|---|---:|---|
| `id` | UUID | no | Primary key |
| `department_id` | UUID | no | Authenticated Department scope; FK to `departments.id`, `RESTRICT` |
| `operation_scope` | `phase3a_operation_scope` | no | Closed Phase 3A operation enum |
| `idempotency_key` | varchar(255) | no | Exact opaque request header value |
| `request_hash` | varchar(64) | no | Lowercase SHA-256 hex of the canonical semantic request |
| `result_entity_id` | UUID | no | Created entity ID, or Campaign ID for member bulk-add |
| `result_schema_version` | smallint | no | Version of the scope-specific replay payload; starts at `1` |
| `result_payload` | JSONB | no | Bounded, allowlisted, redacted success response snapshot |
| `created_at` | timestamptz | no | Server completion/commit timestamp |

Required constraints and guards:

- primary key `(id)`;
- unique `(department_id, operation_scope, idempotency_key)`; `request_hash` is
  deliberately not part of this key;
- `1 <= length(idempotency_key) <= 255`;
- `request_hash ~ '^[0-9a-f]{64}$'`;
- `result_schema_version >= 1`;
- `jsonb_typeof(result_payload) = 'object'` and
  `octet_length(result_payload::text) <= 16384`;
- no `updated_at`; application access is insert/read only, and a table-specific DB
  trigger rejects `UPDATE` and `DELETE`.

`result_entity_id` is intentionally scope-interpreted rather than a company-wide
polymorphic framework. The replay payload is the durable result; Redis, logs, and
Audit are never consulted as its source of truth.

## C. Operation scopes

The initial closed `phase3a_operation_scope` values are:

- `CANDIDATE_POOL_CREATE`
- `TARGETING_POLICY_CREATE`
- `CAMPAIGN_CREATE`
- `CAMPAIGN_MEMBER_BULK_ADD`
- `OUTREACH_TARGET_CREATE`

The first, second, and fifth values close additional literal section S create-API
gaps found during the repository audit. They do not authorize new business
capabilities. `CANDIDATE_POOL_RUN`, `OUTREACH_TASK_CREATE`, and
`OUTREACH_TASK_TRANSITION` stay on their existing WO1 run/task/event records.
Campaign Member removal remains expected-version/CAS based; no bulk-remove API is
introduced by this amendment.

## D. Replay and conflict semantics

The service validates authorization and the request first, expands defaults, and
hashes canonical JSON of every mutation-affecting input. Path IDs are included;
transport data (`Idempotency-Key`, cookies, CSRF token, IP, user agent) is excluded.
Set-based member input is deduplicated and sorted by stable IDs before hashing, so
ordering alone does not change the hash.

- Same Department + scope + key + same hash: re-authorize the caller, return the
  scope-defined success status and the body from the versioned `result_payload`,
  and perform no mutation and no second business Audit.
- Same Department + scope + key + different hash: return `409
  IDEMPOTENCY_KEY_REUSED`; perform no mutation.
- A key is independent across Departments and operation scopes. Within one scope,
  reusing it for another path entity conflicts because the path ID is in the hash.
- Only a successfully committed 2xx mutation creates a record. Validation,
  authorization, domain conflict, and rolled-back/5xx attempts create no record.
- The business mutation, its Audit/OutreachEvent where applicable, and the
  idempotency record commit in one transaction.
- On a concurrent unique violation, roll back the entire losing transaction, read
  the winning record in a new transaction, then apply the same hash comparison.
  The existing Member/Target/Task/Event unique constraints remain independent
  final guards.

Create scopes have a fixed `201` success status; Campaign Member bulk-add has a
fixed `200` success status. This mapping is part of result schema version 1 and is
not inferred from mutable entity state.

## E. Bulk result persistence

`result_payload` is required. Without it, a later replay would recompute counts
against changed membership and would not be the original result.

For `CAMPAIGN_MEMBER_BULK_ADD`, schema version 1 stores exactly this summary:

```json
{
  "campaign_id": "uuid",
  "source_pool_run_id": "uuid-or-null",
  "requested_count": 0,
  "added_count": 0,
  "restored_count": 0,
  "already_active_count": 0,
  "active_count_after": 0
}
```

`requested_count` is the canonical unique Influencer count. The three outcome
counts describe the original transaction and sum to `requested_count`. No
per-member array is stored. Invalid input is rejected atomically before a record is
committed. Re-add restores the existing soft-removed `campaign_members` row; it
does not insert a second row. Thus replay needs no Member mutation or recount.

Other create scopes store their exact, redacted create-response DTO. These DTOs
are small and versioned by `result_schema_version`; they never contain raw Contact
values, message bodies, policy/evidence documents, cookies, or credentials.

## F. AuditAction additions

Add only these truthful Phase 3A values:

- `CANDIDATE_POOL_CREATED`
- `CANDIDATE_POOL_UPDATED`
- `TARGETING_POLICY_CREATED`
- `CANDIDATE_POOL_RUN_REQUESTED`
- `CANDIDATE_POOL_RUN_COMPLETED`
- `CANDIDATE_POOL_RUN_FAILED`
- `CAMPAIGN_CREATED`
- `CAMPAIGN_UPDATED`
- `CAMPAIGN_MEMBERS_ADDED`
- `CAMPAIGN_MEMBERS_REMOVED`
- `OUTREACH_TARGET_CREATED`
- `OUTREACH_TARGET_UPDATED`
- `OUTREACH_TASK_CREATED`
- `OUTREACH_TASK_TRANSITIONED`

Policy is immutable, so there is no Policy-updated action. Generic Campaign update
covers its truthful lifecycle/config transitions through redacted before/after.
The plural Member actions cover set-based add and one-or-many soft removals, with
counts in Audit. Task transition details remain in the append-only OutreachEvent;
the Audit record adds actor/request/security context without pretending to be the
business event history. No Email, Sequence, Inbox, provider, or AI action is added.

Each original state change writes one matching Audit in the same transaction:
successful changes use `SUCCESS`, while a persisted terminal run failure uses
`FAILED`. A successful bulk no-op (all members already active), a same-key replay,
or a rejected request writes no mutation Audit; operational/access logging may
record it but is not a fact source. Worker completion/failure uses the existing
`ip="worker"` and a specific worker user-agent convention. Audit payloads contain
IDs, versions, states, counts, reason codes, and canonical hashes only where needed.
They exclude raw Contact values, member-ID lists, policy/evidence JSON, message
content, Idempotency-Key values, and request bodies.

## G. Migration plan

Use additive `0007_phase3a_persistence_amendment` with
`down_revision = "0006_phase3a_persistence"`. Do not rewrite `0006`.

`0007` creates `phase3a_operation_scope`, the table, constraints, and immutable
trigger. It extends PostgreSQL `audit_action` in an Alembic autocommit block using
`ALTER TYPE ... ADD VALUE IF NOT EXISTS`, matching existing migrations. Downgrade
must refuse while idempotency rows exist, then drop the trigger/table/scope enum;
it does not rebuild `audit_action` or remove historical enum labels.

This is safe and minimal for WO2/WO3 worktrees already branched from WO1: land the
amendment migration once on the shared foundation, then rebase/merge both
worktrees. Neither worktree creates an alternative `0007` or edits `0006`.

## H. Security and privacy

- Authorization and current Department scope are checked on both first execution
  and replay; possession of a key never grants access.
- Accept a non-empty opaque key of at most 255 characters. Do not place business
  data or credentials in it, return it in bodies, or write it to Audit/application
  logs.
- Store only the canonical request hash, never the request body. Hash construction
  uses strict typed inputs and stable canonical JSON.
- `result_payload` is an allowlisted response snapshot, capped at 16 KiB and
  redacted before persistence. Bulk results are counts only.
- Target replay snapshots contain IDs/channel/version and an already-masked
  display at most; never plaintext Email, WeChat, Phone, or message content.
- Records have no application delete/update path and no time-based expiry in
  Phase 3A. Destructive retention policy requires a later explicit contract because
  expiry would weaken durable replay.

## I. Required WO2 changes

- Use the shared record for Candidate Pool create and TargetingPolicy create;
  continue using `candidate_pool_runs` for run idempotency.
- Emit the six Candidate Pool/Policy/Run Audit actions above with redacted,
  bounded metadata. Run request Audit is atomic with the PENDING run; completion
  or failure Audit is atomic with the terminal run state.
- Add service/repository/concurrency tests for replay, hash conflict, cross-
  Department isolation, and the unique-race loser path. No targeting policy or
  evidence semantics change.

## J. Required WO3 changes

- Use the shared record for Campaign create, Campaign Member bulk-add, and
  OutreachTarget create. Keep OutreachTask create and transition on their existing
  task/event seams.
- Persist the bulk summary and mutation Audit atomically with set-based Member
  insert/restore. A replay returns the stored summary without touching Members.
- Emit the eight Campaign/Member/Target/Task Audit actions above. Mask endpoint
  data before both Audit and idempotency-result persistence.
- Test same/different payload replay, 10k-item bounded summary, concurrent unique
  races, restore-versus-already-active counts, no duplicate Audit on replay, and
  rollback of the losing transaction.

## K. Frozen business semantics

No change. Campaign lifecycle/review/duplicate policy, permanent Member uniqueness
and restore-on-readd, Target endpoint rules, Task state machine/CAS, Event history,
RBAC, and channel enablement remain exactly frozen. A1 only supplies missing
durable request/result facts and truthful Audit labels.

## L. Decision

**READY.** The gap is confirmed and the additive design is sufficient for WO2 and
WO3 to implement the frozen semantics after `0007` is landed. This amendment does
not authorize implementation, migration creation, or a commit.

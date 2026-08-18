# Phase 3A HTTP and Today Query Design

Date: 2026-08-18

Status: SEALED Phase 3A HTTP contract for WO-3A-4, amended 2026-08-18.

## Scope

Implement only the Phase 3A HTTP contract and Today read model on top of the
integrated WO-3A-2 Candidate Pool and WO-3A-3 Campaign/Outreach domains.
There is no UI, provider execution, Email, Sequence, Inbox, AI, migration, or
new domain model in this work order.

The frozen foundation remains
`docs/superpowers/specs/2026-08-17-phase-3a-sales-growth-foundation-contract-freeze.md`.
This document records the approved details needed to make its HTTP contract
unambiguous.

## Architecture

Use dedicated, closed HTTP routers and Pydantic DTOs. Extend the existing
Candidate Pool router for missing immutable reads. Add Campaign and Outreach
routers plus a dedicated Today repository and read service. Route adapters
translate HTTP inputs into existing domain inputs and never contain business
state-machine or idempotency logic.

The Today projection is read-only. It does not reimplement task transitions,
endpoint validation, idempotency, or duplicate-history policy. It uses one
bounded `limit + 1` projection query and typed DTO assembly, not per-row point
reads.

## Department Scope and RBAC

All new Department-scoped Phase 3A Candidate, Campaign, and Outreach routes
accept optional `X-Department-ID`.

- When omitted, scope is the authenticated session Department.
- A Super Admin may explicitly set it to an active target Department.
- A non-Super caller may omit it or supply their own Department. Any other
  Department returns `404`, never `403`.
- An unknown or inactive Department returns `404`.
- `X-Department-ID` is optional but single-valued. Repeating it is rejected as
  `422 VALIDATION_ERROR`; routes never choose a first or last header value.
- The header selects scope only. It never changes DepartmentPermission,
  selected Operator, or authorization.
- Today keeps its frozen closed query fields. It does not gain a
  `department_id` query parameter.

All mutations require the existing CSRF check, a selected Operator, and a
non-Viewer Department role. The resolved target Department is passed to the
existing Campaign/Outreach domain methods. Audit preserves the selected actor,
target Department, cross-Department override flag, and resulting business
owner as distinct facts. The raw header is not persisted as a business value.

Candidate Pool services gain equivalent scoped access checks so their reads and
mutations follow the same no-disclosure behavior. Existing services remain the
source of authorization and validation; HTTP checks do not replace them.

## Candidate Pool Contract

Keep all current Candidate Pool routes and add the following reads:

| Method | Route | Contract |
| --- | --- | --- |
| `GET` | `/api/v1/candidate-pools/{pool_id}/policies/{policy_id}` | Read one immutable policy version. |
| `GET` | `/api/v1/candidate-pools/{pool_id}/runs` | Keyset-paged immutable runs. |
| `GET` | `/api/v1/candidate-pools/{pool_id}/runs/{run_id}/members` | Keyset-paged materialized members with optional `result=MATCH|UNKNOWN`. |

The existing policy list, pool list/read/create, policy create, run reservation,
run read, and member-list routes remain formal API contract. Candidate member
results can only be `MATCH` or `UNKNOWN`; `NOT_MATCH` is never individually
addressable or emitted. Evidence stays redacted, Viewer contact-derived
evidence remains masked, and raw Contact values are never returned.

`CandidatePoolCreateInput` gains optional `owner_operator_id`:

- Same-Department create defaults a missing owner to the selected Operator.
- A supplied owner must be active and belong to the resolved Department.
- A cross-Department Super Admin create requires an explicit active
  target-Department owner.
- The owner participates in the canonical idempotency request hash. Reusing a
  key with a different owner returns the existing idempotency conflict.
- Audit records the selected actor independently from the resulting owner.

The closed public create DTO exposes this optional field and the typed Candidate
Pool response always returns its persisted `owner_operator_id`.

No new Candidate persistence field is required; this uses the existing
`CandidatePool.owner_operator_id` relationship.

### Existing Keyset Ordering

The following repository orderings were inspected before freezing the new
public list cursors. A cursor represents the final returned sort tuple; reads
fetch `limit + 1` rows to produce the next cursor.

| Public list | Existing repository ordering | Frozen public ordering and cursor tuple |
| --- | --- | --- |
| Candidate runs | `CandidatePoolRun.id ASC` | `id ASC`; cursor is the final `id` and continues with `id > cursor`. |
| Candidate members | `CandidatePoolMember.id ASC` | `id ASC`; cursor is the final `id` and continues with `id > cursor`. |
| Campaign list | `Campaign.updated_at DESC, Campaign.id DESC` | `updated_at DESC, id DESC`; cursor is `(updated_at, id)` and continues with `updated_at < cursor.updated_at OR (updated_at = cursor.updated_at AND id < cursor.id)`. |
| Campaign members | `list_active_member_ids` uses `CampaignMember.created_at ASC, CampaignMember.id ASC` for active review selection. | Active rows only (`removed_at IS NULL`), ordered `created_at ASC, id ASC`; cursor is `(created_at, id)` and continues with `created_at > cursor.created_at OR (created_at = cursor.created_at AND id > cursor.id)`. It is bound to the resolved Department and `campaign_id`. |

Phase 3A does not add an `include_removed` query parameter. The list returns
only current active Members. The point read
`GET /campaigns/{campaign_id}/members/{member_id}` may return either an active
or removed Member when the resolved Department/Campaign scope permits it.
Internal restore/re-add logic continues to locate removed rows and restores the
same persistent Member identity.

## Campaign and Outreach Contract

All responses use the existing success envelope and all public DTOs use
`extra="forbid"`. Public write DTOs intentionally omit `department_id`; the
router resolves scope from `X-Department-ID` and builds the existing internal
domain input with that resolved value.

| Method | Route | Contract |
| --- | --- | --- |
| `GET`, `POST` | `/api/v1/campaigns` | Keyset-paged Campaign list and create. |
| `GET`, `PUT` | `/api/v1/campaigns/{campaign_id}` | Read and full CAS update. |
| `POST` | `/api/v1/campaigns/{campaign_id}/lifecycle` | CAS lifecycle transition. |
| `GET` | `/api/v1/campaigns/{campaign_id}/members` | Keyset-paged members. |
| `GET` | `/api/v1/campaigns/{campaign_id}/members/{member_id}` | Read one member. |
| `POST` | `/api/v1/campaigns/{campaign_id}/members/bulk-add` | Direct bounded member add. |
| `POST` | `/api/v1/campaigns/{campaign_id}/members/from-candidate-run` | Selected persisted run-member add. |
| `POST` | `/api/v1/campaigns/{campaign_id}/members/{member_id}/remove` | CAS soft removal. |
| `POST` | `/api/v1/campaigns/{campaign_id}/outreach-targets` | Create target. |
| `GET`, `PUT` | `/api/v1/outreach-targets/{target_id}` | Read and CAS update target. |
| `POST` | `/api/v1/outreach-targets/{target_id}/tasks` | Create task. |
| `GET` | `/api/v1/outreach-tasks/today` | Today read model; registered before `/{task_id}`. |
| `GET` | `/api/v1/outreach-tasks/{task_id}` | Read task. |
| `POST` | `/api/v1/outreach-tasks/{task_id}/transitions` | CAS task transition. |
| `GET` | `/api/v1/outreach-tasks/{task_id}/events` | Safe append-only event history. |

All create, run, bulk-add, task-create, and task-transition operations require
exactly one opaque `Idempotency-Key`. Existing expected-version DTO fields stay
required for every versioned update, lifecycle transition, target update,
member removal, and task transition. Direct Campaign ownership keeps the WO-3
actor-versus-owner behavior; a Super Admin's selected Operator is not silently
made the target Department Campaign owner.

### Direct Campaign Member Add

The direct bulk-add body contains only bounded typed pairs:

```json
{
  "members": [
    {
      "influencer_id": "uuid",
      "preferred_platform_account_id": "uuid"
    }
  ]
}
```

`members` contains 1 through 10,000 items. `preferred_platform_account_id` is
required. The route does not accept an Influencer-only item and does not select
an account automatically. `influencer_id` must occur exactly once in the
request: duplicate entries for the same Influencer, whether they name the same
or different preferred accounts, reject the entire body as `422
VALIDATION_ERROR`. The service never silently deduplicates. Direct adds do not
accept or set `source_pool_run_id`.

### Mutable Field Boundaries

`PUT /campaigns/{campaign_id}` is a full CAS update of only the existing WO-3
mutable Campaign configuration: `name`, `owner_operator_id`, `review_mode`,
`review_count`, `duplicate_history_policy`, `duplicate_window_days`, and
`expected_version`. `status` is not a PUT field and is rejected by the closed
DTO as `422 VALIDATION_ERROR`; the lifecycle route is the only status path.
Department and creator identity remain immutable.

`PUT /outreach-targets/{target_id}` is a CAS replacement of exactly one
endpoint reference (`contact_id` or `platform_account_id`) plus
`expected_version`. It cannot change Department, Campaign, Member, Influencer,
or channel identity. Those fields are omitted from the closed PUT DTO and are
therefore rejected as `422 VALIDATION_ERROR`. No new mutable domain fields are
introduced.

### Add From Candidate Run

The provenance-bearing route accepts:

```json
{
  "run_id": "uuid",
  "member_ids": ["uuid"]
}
```

The selected member ID set must have 1 through 10,000 distinct IDs. Every ID
must be a persisted `CandidatePoolMember` belonging to `run_id`; both `MATCH`
and `UNKNOWN` are eligible. `UNKNOWN` is never automatically included.
`NOT_MATCH` cannot be selected because it has no materialized row.

The service validates that the selected rows contain at most one account for
each Influencer. A conflict rejects the whole request; there is no
preferred-account tie-breaker. It inserts new Members and restores removed
Members set-wise from the selected persisted rows. For each selected row:

- `influencer_id` comes from `CandidatePoolMember.influencer_id`.
- `preferred_platform_account_id` comes from
  `CandidatePoolMember.platform_account_id`.
- `source_pool_run_id` is `run_id`.

Existing active Members contribute `already_active_count`. Removed Members are
restored in place and may receive the explicitly selected preferred account
after normal ownership validation. Candidate evidence is never copied. The
operation preserves the existing A1 durable idempotency record and its bounded
bulk result summary.

## Today Query Contract

The frozen route is `GET /api/v1/outreach-tasks/today`. Its only query fields,
all non-repeatable, are:

`work_kind`, `channel`, `campaign_id`, `owner_operator_id`, `track`,
`followers_min`, `followers_max`, `contact_filter`, `priority`, `cursor`, and
`limit`.

The Pydantic query DTO is closed and validates effective defaults:

- `work_kind` is `FIRST_TOUCH`, `FOLLOW_UP`, or `ALL`, defaulting to `ALL`.
- `limit` defaults to 50 and is at most 100.
- follower bounds are nonnegative and `followers_min <= followers_max`.
- `contact_filter` reuses `has_contact`, `has_email`, and `no_contact`.
- No offset pagination or undocumented query field is accepted.
- Every listed query field is single-valued. Unknown or repeated fields are
  rejected as `422 VALIDATION_ERROR` before DTO construction; the API never
  applies last-value-wins parsing.

Eligibility is exactly the resolved Department, non-removed CampaignMember,
Task `READY`, and `due_at < next_business_day`. Overdue work is included. No
Campaign-status predicate is added. `FOLLOW_UP` may be empty until a later
Sequence phase creates it.

### Business-Day Resolver

The resolver uses only `Asia/Shanghai` and returns the next weekday boundary
at `00:00:00`:

- Monday through Thursday resolve to the next calendar day.
- Friday, Saturday, and Sunday resolve to Monday.
- Business days are Monday through Friday only.
- No statutory-holiday, make-up-workday, or company calendar applies.
- The comparison is strict: a Task due exactly at the boundary is excluded.

The response contains the local `business_date`, literal
`timezone = "Asia/Shanghai"`, timezone-aware `as_of`, typed `items`, and
`next_cursor`. Timestamps are stored and compared as timezone-aware instants;
no server-local timezone is used.

`business_date` is the Asia/Shanghai local calendar date of `as_of`. It is not
derived from `due_at` or `next_business_day`; the latter remains only the strict
eligibility cutoff. For Friday `2026-08-21 15:00+08:00`, `business_date` is
`2026-08-21` and `next_business_day` is `2026-08-24T00:00:00+08:00`. A Saturday
or Sunday query similarly reports its own local calendar date while using
Monday `00:00:00+08:00` as the cutoff.

### Filter Semantics

- `owner_operator_id` filters `OutreachTask.assigned_operator_id`, not Campaign
  ownership or global Influencer ownership.
- `track` exact-matches one value in the preferred CampaignMember account's
  persisted `source_tags`. It does not use substring matching, free text,
  target accounts, or another Influencer account. Missing tags do not match.
- The account summary, `track`, and follower summary all use that same
  preferred CampaignMember account.
- `followers_count` reads only that account's current `HUITUN` metrics row.
  It is `null` for no Huitun row or an absent/malformed/non-integer follower
  value. No other source is selected, merged, or used as fallback.
- Follower bounds apply to the same Huitun value. A null value does not match
  when either bound is present, but remains eligible without bounds.
- PostgreSQL JSONB conversion is guarded before numeric casting so malformed
  historical values produce null rather than a database cast error.

### Item Truthfulness

Each strict item DTO may contain safe Campaign, Member, preferred-account,
assigned-operator, Task, channel, due, priority, follower/tag,
contact-availability, history-warning, and masked-target-display fields.
Global CRM state is named `crm_stage` only. Current Contact availability is a
derived safe summary, never a raw Contact value. Viewer reads retain the
required masking. No field reports a new reply, unread state, Inbox intent, AI
score, raw target/contact value, endpoint fingerprint, idempotency key,
request hash, or ORM internals.

### Current History Warning

Today derives its warning from canonical `OutreachEvent` history for the
company-level Influencer and channel, matching the frozen cross-Campaign
duplicate-history rule. The projection calculates the current latest sent
fact set-wise, through a CTE, lateral subquery, or equivalent aggregate; it
does not make per-row point reads.

The warning exposes only the safe frozen facts (`channel` and `last_sent_at`).
It never exposes a cross-Department Campaign ID, Task ID, Event ID, or raw
Contact data. `TASK_CREATED` metadata retains its historical warning snapshot
for explainability but is not Today's sole or canonical source.

## Cursor Contract

Today ordering is fixed as `priority_rank DESC, due_at ASC, id ASC`, where
priority rank is derived deterministically from the frozen priority enum.

The cursor is a versioned, HMAC-signed opaque token using existing application
signing material. It contains the complete sort tuple, a canonical hash of the
normalized parsed query DTO excluding `cursor`, including effective defaults
such as `limit`, plus the resolved Department, and the resolved Department
scope itself. The opaque cursor token is never an input to its own hash. It
does not use raw query-string ordering or text. Cursor verification rejects
malformed/tampered tokens, changed query values, and a different resolved
Department. It does not claim cross-request MVCC snapshot isolation; mutations
require a fresh pagination session.

No new production secret gate is introduced. Production reuses the existing
application master key. Non-production derives the signing key from existing
application name and environment configuration so tests are deterministic and
no new secret setting is required.

## Implementation Preflight

Before cursor code is written, verify without logging its value that the
existing production configuration supplies non-empty HMAC material through
`Settings.app_master_key`; production startup already rejects a missing
`APP_MASTER_KEY`, although its current invariant does not reject a blank value.
The Today signer therefore fails closed on a blank value before HMAC use. It
uses this existing material in production and the existing name/environment
configuration only for the already-approved non-production derivation. This
does not add a WO-3A-4 secret setting or fall back to an empty key.

Before route adapters are written, add regression coverage that sends repeated
Today query keys and repeated `X-Department-ID` headers. Both must produce the
closed `422 VALIDATION_ERROR` response before scope selection, query hashing,
or projection execution. This verifies that framework scalar binding cannot
silently choose a last value.

## Error and OpenAPI Contract

Route adapters map `TargetingError` and `CampaignOutreachError` to the
existing error envelope. Expected mappings include validation (`422`),
not-found/no-disclosure (`404`), stale expected version (`409` with only safe
`current_version` detail), illegal transition (`409`), idempotency conflict
(`409`), duplicate constraint (`409`), disabled channel (`409`), invalid
target/contact (`422`), and no raw database exception reaches the response.

The existing WO-2 Buyer taxonomy mapping is route-specific and exact:
`BUYER_TAXONOMY_UNREVIEWED` is `422` while Candidate Pool or policy creation
validates a submitted definition, and is `409` while a run reservation resolves
an otherwise persisted policy. OpenAPI lists the applicable response on each
route; it does not describe this as "422 or 409".

Cursor failures are deterministic: a malformed token, unsupported cursor
version, or invalid HMAC signature returns `422 CURSOR_INVALID`; a valid cursor
whose normalized query hash or resolved Department differs returns `409
CURSOR_MISMATCH`. Both failures are rejected before the Today projection query
and reveal neither the expected hash nor scope. Malformed non-Today keyset
cursors and all repeated/unknown closed query fields use the existing `422
VALIDATION_ERROR` envelope.

OpenAPI documents routes, methods, closed request bodies, response envelopes,
enums, required `expected_version` fields, all Today query fields/defaults,
`X-Department-ID`, and error responses. The `/outreach-tasks/today` route is
registered before the UUID task route and receives an explicit routing and
OpenAPI regression test.

## Verification

Focused tests cover:

- Candidate Pool create/replay/conflict, policy/run reads, MATCH/UNKNOWN
  reads, Viewer masking, Department scope, and Candidate ownership rules.
- Campaign CRUD, lifecycle/version conflicts, direct typed pair bulk-add,
  duplicate direct-Influencer rejection (same and different account), selected-run
  MATCH/UNKNOWN behavior, wrong-run and duplicate-Influencer rejection, restore,
  active-member counting, A1 replay, active-only member lists, direct removed
  Member reads, restore identity/order retention, stable `(created_at, id)`
  pagination, and Department/Campaign-bound member cursors.
- PUT boundaries: Campaign status is lifecycle-only, and OutreachTarget identity
  fields cannot be changed through the endpoint update route.
- Target, Task, transition, history, cross-Department scope/audit, and
  selected-operator target-scope validation.
- Today eligibility, all filters, exact tag behavior, Huitun metrics null and
  bounds behavior, latest-event warning redaction, weekday boundaries,
  stable ordering, keyset pagination, cursor mismatch/scope binding, 50/100
  limits, Department isolation, Viewer masking, invalid/mismatched cursor
  mappings, repeated Today query/header rejection, weekday `business_date`, and
  Friday/Saturday/Sunday `business_date` versus Monday-cutoff behavior.
- Projection query-count or query-shape checks proving no per-item Contact,
  Metrics, Event, Campaign, or Member loads.
- OpenAPI route/parameter/schema/enum/error assertions, including `/today`
  route precedence.

Run targeted HTTP and Today tests, Candidate/Campaign/Outreach regressions,
Ruff, Black check, mypy for backend/API/worker, and `git diff --check`. Use
isolated PostgreSQL for SQL semantics where available, but do not run the WO-5
100k release or EXPLAIN/load gate.

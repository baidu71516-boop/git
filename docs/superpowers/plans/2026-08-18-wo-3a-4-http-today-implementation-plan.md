# WO-3A-4 HTTP and Today Implementation Plan

Date: 2026-08-18

Status: Contract sealed. Implementation may begin immediately.

Design source:
`docs/superpowers/specs/2026-08-18-phase-3a-http-today-design.md`

## Sealed Decisions

1. `GET /api/v1/campaigns/{campaign_id}/members` returns only active Members
   (`removed_at IS NULL`) and has no `include_removed` option. Its ordering is
   `created_at ASC, id ASC`; its cursor tuple is `(created_at, id)` and is bound
   to the resolved Department and `campaign_id`. Point reads may return removed
   Members, and restore/re-add retains the same persistent identity.
2. Today `business_date` is the Asia/Shanghai local calendar date of `as_of`.
   It is not the next-business-day boundary date and is independent of `due_at`.

No migration is planned. Current tables, foreign keys, and queue/history
indexes support the approved scope.

## Ordered Work

### 1. Establish the Phase 3A HTTP boundary

Files: add a small shared Phase 3A scope dependency under
`apps/api/app/http/`; update `apps/api/app/http/dependencies.py`,
`apps/api/app/http/errors.py`, and `apps/api/app/main.py`.

- Resolve `X-Department-ID` once for the new Candidate, Campaign, and Outreach
  routers. Inspect `request.headers.getlist("x-department-id")` before scalar
  header binding; zero or one value is allowed and repeated values return the
  existing `422 VALIDATION_ERROR` envelope.
- Preserve the frozen rules: omitted means session Department; Super Admin may
  target an active Department; non-Super cross-Department and unknown/inactive
  Departments return `404`; header scope never changes role or selected
  operator.
- Pass the resolved Department scope into services rather than relying on
  router-only filtering. Candidate services need this explicitly because their
  current Super Admin reads are global and writes are session-Department-only.
- Retain existing CSRF and selected-Operator checks for mutations. Audit must
  receive actor, target Department, and cross-Department override separately.
- Add an HTTP-safe adapter for `CampaignOutreachError`. Preserve only
  `current_version` on a stale `409 VERSION_CONFLICT`; never expose its
  `entity_id`. Keep the existing error envelope compatible for all other
  routes.
- Register the new routers from `main.py` without changing unrelated Phase 2
  route behavior.

### 2. Complete the Candidate Pool public surface

Files: `packages/backend_core/src/backend_core/growth/schemas.py`,
`packages/backend_core/src/backend_core/growth/repository.py`,
`packages/backend_core/src/backend_core/growth/service.py`,
`apps/api/app/http/candidate_pools.py`, and Candidate service/API tests.

- Add API-facing owner handling to Candidate Pool create: scope first, default
  same-Department owner to selected operator, require a target-Department
  active owner for a cross-Department Super Admin request, and include the
  resolved owner in the canonical idempotency hash and response DTO.
- Keep actor attribution separate from resulting owner in Audit. Validate an
  inaccessible or wrong-Department owner with the existing safe `404` path.
- Add the immutable single-policy read, a `limit + 1` Candidate run page, and
  the optional `result=MATCH|UNKNOWN` member filter. Keep `NOT_MATCH`
  unaddressable. Reuse `id ASC` plus `id > cursor` for both new list cursors.
- Add closed query validators to the newly public Candidate list endpoints;
  reject unknown or repeated keys instead of accepting FastAPI's scalar
  last-value behavior.
- Keep Viewer evidence masking and raw Contact exclusion in the service layer,
  including the new reads and resolved Department scope.

### 3. Add the Campaign HTTP contracts and paged Campaign list

Files: add `apps/api/app/http/campaigns.py` and API-only strict DTOs; extend
`packages/backend_core/src/backend_core/campaigns/schemas.py`,
`packages/backend_core/src/backend_core/campaigns/repository.py`, and
`packages/backend_core/src/backend_core/campaigns/service.py`.

- Expose only typed, `extra="forbid"` public request/response DTOs. Public
  bodies omit `department_id`; the router injects the resolved scope into
  existing domain inputs.
- Add a bounded `Campaign` list repository/service page using the established
  `updated_at DESC, id DESC` order, `limit + 1`, and the frozen two-column
  keyset predicate.
- Keep Campaign PUT limited to existing mutable fields. Its public DTO rejects
  `status`; lifecycle remains the only status transition path. Keep existing
  optimistic concurrency and Audit behavior.
- Make direct bulk-add accept only `1..10,000` typed
  `{influencer_id, preferred_platform_account_id}` items. Update the core
  `CampaignMemberBulkAddInput` validator itself to reject every duplicate
  `influencer_id`, including identical pairs, so non-HTTP callers cannot
  silently deduplicate. Remove the exact-duplicate behavior from
  `_canonical_members`; it may still deterministically sort an already unique
  set for hashing.
- Keep `source_pool_run_id` out of the direct HTTP DTO and pass `None` to the
  existing direct-add service path. Preserve A1 result-summary replay fields.
- Add the public Campaign member page with `removed_at IS NULL`,
  `created_at ASC, id ASC`, `limit + 1`, and the frozen two-column keyset
  predicate. Bind its cursor to resolved Department and `campaign_id`; reject a
  mismatched cursor. Keep direct point reads able to resolve active or removed
  Members in the same scoped Campaign.

### 4. Implement selected persisted-run Campaign member addition

Files: extend `packages/backend_core/src/backend_core/campaigns/schemas.py`,
`packages/backend_core/src/backend_core/campaigns/repository.py`, and
`packages/backend_core/src/backend_core/campaigns/service.py`; add the
corresponding route in `apps/api/app/http/campaigns.py`.

- Define a dedicated request with `run_id` and `1..10,000` distinct persisted
  `member_ids`; do not reuse the direct pair body and do not accept Candidate
  evidence.
- Lock and validate the source run in the resolved Department and require its
  completed state. Resolve the selected IDs set-wise from
  `CandidatePoolMember`; a missing ID or an ID belonging to another run is a
  whole-request safe validation rejection.
- Restrict source rows to materialized `MATCH` and `UNKNOWN` records. Check in
  SQL that no selected Influencer has more than one selected platform account;
  reject the entire request with no account tie-breaker.
- Use CTE/Core multi-row operations or equivalent bounded chunks to validate
  account ownership, identify active/removed/nonexistent Campaign members,
  insert new members, and CAS-restore removed members. Populate only
  `influencer_id`, selected `preferred_platform_account_id`, and
  `source_pool_run_id=run_id`; active rows count as `already_active` and a
  restored persistent row may update its selected account.
- Preserve the durable A1 idempotency key/hash/result summary and canonicalize
  the already-validated selected ID set for replay. Do not copy candidate
  evidence and do not assemble mutations via one ORM query per selected row.

### 5. Expose existing Outreach operations through a dedicated router

Files: add `apps/api/app/http/outreach.py` and API-only strict DTOs; extend
`packages/backend_core/src/backend_core/outreach/schemas.py` and service
adapters only where an HTTP-specific boundary is required.

- Add typed read/create/update routes for OutreachTarget, task creation/read,
  transitions, and safe event history. Reuse current Campaign/Outreach domain
  services for state transitions, idempotency, endpoint validation, and Audit.
- Keep OutreachTarget PUT to exactly one endpoint reference plus
  `expected_version`; reject Campaign, Member, Influencer, channel, and
  Department identity fields through the closed DTO.
- Require exactly one `Idempotency-Key` on the frozen create/transition paths,
  preserve existing replay status behavior, and translate domain errors through
  the shared safe adapter.
- Register the literal `/outreach-tasks/today` route before
  `/outreach-tasks/{task_id}` and include an OpenAPI/router precedence test that
  proves `today` reaches the Today handler rather than the UUID detail route.

### 6. Build Today primitives and typed API contract

Files: add a focused Today module and repository under
`packages/backend_core/src/backend_core/outreach/`; add Today request/response
DTOs to the API-only contract module and route wiring in
`apps/api/app/http/outreach.py`.

- Implement an injectable Asia/Shanghai weekday resolver. It returns the next
  local weekday `00:00:00`, Monday-Friday only, and compares aware instants with
  strict `<`. Do not use server-local time, holiday calendars, or an implicit
  `Campaign.status` predicate.
- Set response `business_date` to `as_of` converted to Asia/Shanghai and then
  reduced to its local calendar date. Keep `as_of` timezone-aware and the
  literal timezone field fixed; do not derive this field from the cutoff or
  `due_at`.
- Define a closed query DTO for exactly the frozen Today fields. Validate
  effective default `limit=50`, `limit <= 100`, enum/value ranges, paired
  follower bounds, no unknown query fields, and no repeated values using
  `query_params.getlist` before DTO parsing.
- Build a versioned opaque cursor codec using existing Settings/application
  signing material. In production, validate a non-empty existing
  `app_master_key` before signer construction; do not add a secret setting. In
  non-production, retain the approved deterministic derivation from existing
  app name/environment inputs.
- Sign a payload containing the complete `(priority_rank, due_at, id)` tuple,
  the normalized parsed query DTO excluding `cursor` but including effective
  defaults, and resolved Department. Return `422 CURSOR_INVALID` for malformed,
  unsupported, or invalid-signature tokens, and `409 CURSOR_MISMATCH` for valid
  payloads whose query hash or Department differs.

### 7. Implement the one bounded Today projection query

Files: the new Today repository and its read service tests, with no changes to
the Outreach state machine or idempotency code.

- Start from Department-scoped `OutreachTask`, then join its target, active
  CampaignMember, Campaign, Influencer, and the Member's preferred platform
  account through explicit composite conditions. Eligibility is only resolved
  Department, active Member, `READY`, and `due_at < next_business_day`; overdue
  rows remain included. Do not add Campaign status, Influencer state, preferred
  account activity, endpoint validity, or channel-enablement filters.
- Implement owner filtering solely against `OutreachTask.assigned_operator_id`.
  Use the existing owner-queue/index path when that filter is present; never
  substitute Campaign or Influencer owner.
- Apply `track` as an exact source-tag element test on that preferred account.
  Apply follower bounds to a left join of only that account's current `HUITUN`
  metrics row. Reuse the guarded `followers_count_expression(dialect)` behavior
  so missing, malformed, or non-integer JSON produces `null`, not a PostgreSQL
  cast failure. Do not merge sources or fall back to another account.
- Derive contact availability with set-based `EXISTS` or aggregate predicates
  so it neither filters unrelated tasks nor fans out rows. Apply Viewer masking
  in projection DTO assembly and never select raw contact values.
- Derive `history_warning` from a set-based CTE, lateral, or aggregate over
  `OutreachEvent.event_type=OUTREACH_SENT` by company Influencer and channel.
  It may cross Department history for duplicate-warning truthfulness, but
  returns only safe channel and `last_sent_at`; never Campaign/Task/Event IDs or
  contacts. Do not use `TASK_CREATED.metadata` as the current source.
- Order exactly by `priority_rank DESC, due_at ASC, id ASC`, fetch `limit + 1`,
  and use the mixed-direction keyset predicate
  `rank < cursor.rank OR (rank = cursor.rank AND due_at > cursor.due_at) OR
  (rank = cursor.rank AND due_at = cursor.due_at AND id > cursor.id)`.
  Return typed rows directly from the projection, with no per-item ORM loads.

### 8. Cover contracts with focused tests

Files: extend `apps/api/tests/test_candidate_pools_http.py`; add Campaign,
Outreach, and Today HTTP test modules; extend core service tests; add an
isolated PostgreSQL Today projection/query-shape test where SQL behavior matters.

- Scope and ownership: omitted/own/cross Department headers, non-Super other
  Department and unknown Department `404`, repeated header `422`, target-scope
  operator validation, actor-versus-owner Audit, Candidate owner defaults,
  inactive/wrong owner, and idempotency replay/conflict when owner changes.
- Candidate and Campaign reads: immutable policy/run/member reads, MATCH and
  UNKNOWN filtering/masking, stable Candidate and Campaign ordering/cursors,
  direct duplicate-Influencer rejection for same and different accounts, and
  PUT mutable-field boundaries, active-only Campaign member listing, direct
  removed-member reads, restore identity/order retention, stable
  `(created_at, id)` pagination, and Department/Campaign cursor binding.
- Selected-run bulk add: selected MATCH, selected UNKNOWN, unselected UNKNOWN,
  mixed results, same-Influencer selected accounts rejection, wrong-run
  rejection, removed-row restore, existing-active count, and idempotent replay.
- Today filters: task-assignee ownership versus Campaign/Influencer owners and
  reassignment; Department/RBAC scope; exact/nonmatching/substring/other-account
  tags; HUITUN-only followers including missing/malformed/another-source cases,
  lower/upper bounds and null behavior; weekday and strict-boundary cases;
  weekday and Friday/Saturday/Sunday `business_date` behavior; and redacted
  latest-event history warning.
- Today pagination/security: all sort ties, `limit + 1`, cursor tuple/query
  hash/Department binding, malformed/version/signature and mismatch mappings,
  repeated Today query values, and no `department_id` query field.
- Router/OpenAPI/query shape: `/today` precedence, closed DTOs/headers/error
  responses, and a fixed query count or statement-shape assertion that rules
  out per-row Contact, metrics, event, Campaign, or Member reads.

### 9. Verify the sealed contract

- Run the focused core, API, and PostgreSQL projection tests first, followed by
  Candidate/Campaign/Outreach regressions.
- Run `make lint`, the relevant `pytest` suites from `Makefile`, and
  `git diff --check`.
- Do not run the WO-5 100k release/performance gate, add migrations, push,
  merge, or begin Phase 3B work.

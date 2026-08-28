# C3A Candidate Pool Custom Rule Builder and Versioning Contract Freeze

Status: **CONTRACT FREEZE PASS**

Baseline: `4661ea0e85a74521a20482f45587b2d36447c7e9` on `codex/candidate-pool-rule-rerun-c3a`

Scope: architecture and product contract only; no application implementation, migration, provider call, production access, or deployment

## 1. Decision

C3A P0 reuses the existing Candidate Pool architecture:

- `candidate_pools.current_policy_id` and `candidate_pools.version` identify the current immutable rule and provide the Pool concurrency token;
- `targeting_policies` stores Pool-owned immutable rule versions;
- `candidate_pool_runs.policy_id` permanently pins every Run to the exact rule version used;
- the worker materializes from `run.policy_id`, never from the Pool's later current-policy pointer.

There is no shared rule library, template library, generic rules engine, duplicate policy snapshot table, or historical Run rewrite.

The lifecycle is:

```text
Candidate Pool
  -> author or adjust current SELLER_V1 conditions
  -> INSERT the next immutable TargetingPolicy version
  -> update the Pool current-policy pointer/version
  -> create a new CandidatePoolRun pinned to that policy
  -> materialize an immutable historical result set
```

Later edits create the next version and a new Run. Version 1/Run 1 remain unchanged after Version 2/Run 2 exists.

## 2. Evidence at the frozen baseline

The integrated baseline already provides the required persistence seam:

- `targeting_policies` has unique `(pool_id, version)`, full typed `definition`, `schema_version`, `canonical_hash`, creator, timestamps, and `RESTRICT` ownership;
- `candidate_pool_runs` has required `(policy_id, pool_id)` referential integrity to the same Pool;
- `candidate_pools.current_policy_id` has a same-Pool composite foreign key;
- Run reservation captures `policy_id`, policy version/hash, `as_of`, and the input watermark;
- worker materialization loads the policy through `run.policy_id`;
- existing policy and Run POSTs already require authentication, CSRF, a selected Operator, non-Viewer authority, Department scope, `Idempotency-Key`, and Audit records;
- the Web already has policy history, Run history, Run detail, polling, and C2 Candidate selection.

The baseline does **not** contain integrated evidence/routing for `LONG_INACTIVITY` or trusted `ACTIVITY_DROP`. Existing `notes_7d`, `notes_60d`, and source-data freshness are not substitutes for either signal.

## 3. P0 rule-family boundary

### 3.1 `SELLER_V1`: authorable

P0 authoring is a full-document write with `extra="forbid"`. `schema_version=1` and `policy_type="SELLER_V1"` are fixed contract values, not user-defined extensibility points.

The closed authorable allowlist is exactly:

| Field                        | Frozen P0 meaning                                                                                                                                               |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `contact_availability`       | optional `has_contact`, `has_email`, or `no_contact` over current canonical Contact types                                                                       |
| `followers`                  | optional inclusive nonnegative integer `minimum`/`maximum`; at least one bound when configured; minimum cannot exceed maximum                                   |
| `tags_exact_any`             | optional exact-any match over canonical account `source_tags`; trimmed, unique, stable-sorted, exact and case-sensitive                                         |
| `notes_7d`                   | optional inclusive nonnegative integer range over the integrated `notes_7d` metric                                                                              |
| `notes_60d`                  | optional inclusive nonnegative integer range over the integrated `notes_60d` metric                                                                             |
| `freshness.allowed_statuses` | optional nonempty unique set of source-observation freshness bands; the Web offers `fresh`, `aging`, `stale`, and `very_stale`, not `unknown` as a match choice |
| `platforms`                  | optional unique allowlist; the integrated enum currently exposes only `xiaohongshu`                                                                             |
| `sources`                    | optional unique allowlist of `huitun`, `generic`, and `manual`                                                                                                  |

New P0 authoring rejects `unknown` inside `freshness.allowed_statuses`: unknown source evidence already produces the evaluator's `UNKNOWN` result and cannot be configured into a match. An existing immutable snapshot containing that enum value remains viewable and may be rerun unchanged if the integrated parser otherwise considers it valid.

Empty optional fields disable that criterion. An entirely empty valid `SELLER_V1` retains the existing evaluator meaning: every otherwise eligible account reduces to `MATCH`. The Web must state this consequence and require explicit confirmation; it must not inject hidden defaults.

Configured criteria are combined by the existing logical AND/tri-state reduction:

```text
any NOT_MATCH -> NOT_MATCH
else any UNKNOWN -> UNKNOWN
else -> MATCH
```

Missing or invalid configured evidence remains `UNKNOWN`; it is never treated as a permissive match. The Web must label `freshness` as **data/source freshness** and `notes_7d`/`notes_60d` as note-count ranges. None may be renamed or presented as publication inactivity or activity-drop evidence.

Unknown keys, future signal keys, `SELLER_V2`, `LONG_INACTIVITY`, `ACTIVITY_DROP`, arbitrary operators, nested boolean groups, free-form field names, and unsupported enum values receive a closed-contract `422`; they are never ignored.

### 3.2 `BUYER_V1`: read-only

P0 may:

- display the complete existing reviewed `BUYER_V1` snapshot, including taxonomy version/review status, categories, aliases, relationship sets, and freshness constraint;
- view the immutable policy associated with a historical Run;
- create an unchanged rerun pinned to that exact valid reviewed policy.

P0 may not create a Buyer Pool, author or edit Buyer taxonomy, replace a Buyer rule, mark a taxonomy reviewed, invent a temporary taxonomy store/API, or convert Buyer policy JSON through the Seller editor. An unreviewed, corrupt, unsupported, or wrong-Pool Buyer policy may remain safely viewable where authorized but is not runnable.

## 4. Persistence and immutability

`TargetingPolicy` is an immutable version record. Application repository/service code must expose INSERT and read operations only; no policy-definition, hash, version, creator, or Pool reassignment UPDATE is permitted.

The authoritative policy snapshot is the full closed definition on `targeting_policies`. `CandidatePoolRun.policy_id` plus the same-Pool composite foreign key is the permanent Run linkage. The Run watermark's policy ID/version/hash is integrity and diagnostic metadata, not a mutable replacement for that relationship.

Historical rendering and worker execution always follow:

```text
CandidatePoolRun.policy_id -> TargetingPolicy
```

They never resolve through `CandidatePool.current_policy_id` after the Run exists. Later policy creation cannot update a completed or failed Run, its members, counts, evidence, `as_of`, watermark policy identity, or pinned policy.

Policy numbering starts at 1 and is gap-free among committed versions for a Pool. The service locks the Pool before assigning the next number. A rolled-back transaction consumes no visible version. A Run-only operation does not increment either the policy version or Pool concurrency version.

`CandidatePool.version` is an aggregate concurrency token, not the displayed policy-version number. Each successful new policy increments it once.

## 5. API contract

All paths remain under `/api/v1/candidate-pools` and use strict, non-repeating headers/query conventions.

### 5.1 Existing reads

- `GET /{pool_id}`: scoped Pool and current-policy pointer/version.
- `GET /{pool_id}/policies`: immutable history in `version ASC`.
- `GET /{pool_id}/policies/{policy_id}`: one same-Pool immutable snapshot.
- `GET /{pool_id}/runs` and `GET /{pool_id}/runs/{run_id}`: Run history/detail.
- `GET /{pool_id}/runs/{run_id}/members`: existing bounded historical result projection.

### 5.2 Create Pool and Rule Version 1

`POST /candidate-pools` keeps the existing atomic Pool + initial-policy creation. The C3A Web exposes Seller creation only and supplies a closed `SELLER_V1` document. A successful commit creates one Pool, Policy Version 1, current pointer/version change, and required audits; no Run is implied.

### 5.3 No standalone P0 policy-edit write

The existing `POST /{pool_id}/policies` append route is not a C3A P0 authoring operation. It must not remain as a public or Web-accessible bypass that can change the current-policy pointer without creating the corresponding Run. C3A Web never calls it for editing. The implementation must retire/disable direct append for P0 with the stable closed error `409 TARGETING_POLICY_RUN_REQUIRED`; internal helpers may remain only to serve atomic Pool creation and Shape C below inside their caller-owned transaction.

Initial Pool + Policy Version 1 creation is the sole no-Run policy creation boundary. Every later P0 rule edit uses Shape C and atomically creates its new Run.

### 5.4 Create a Run

`POST /{pool_id}/runs` accepts exactly one of three closed request shapes.

#### A. Current-rule Run

```json
{}
```

After locking the active scoped Pool, resolve its current valid policy and create a new Run pinned to it. This preserves the existing request.

#### B. Exact unchanged historical-policy rerun

```json
{ "policy_id": "uuid" }
```

The server must resolve the supplied ID through the already authorized Pool:

```text
policy.id = supplied policy_id
AND policy.pool_id = path pool_id
AND Pool is within the resolved company/session/Department authority
```

It must be a valid immutable policy matching the Pool kind. Buyer additionally requires an existing reviewed valid `BUYER_V1` snapshot. A raw UUID never grants authority. Missing, cross-Pool, cross-scope, or inaccessible IDs fail closed as `404 TARGETING_POLICY_NOT_FOUND` without disclosing whether the UUID exists elsewhere. This operation never changes the current-policy pointer or creates a policy version.

#### C. Adjust current Seller rule and rerun

```json
{
  "base_policy_id": "uuid",
  "expected_pool_version": 2,
  "policy": { "schema_version": 1, "policy_type": "SELLER_V1" }
}
```

After PostgreSQL locks the active scoped Pool, both conditions are mandatory:

```text
expected_pool_version == pool.version
base_policy_id == pool.current_policy_id
```

The base policy must be the valid current same-Pool `SELLER_V1`. P0 does not branch from arbitrary historical versions. A stale version or base pointer returns `409 VERSION_CONFLICT` and creates no policy, Run, pointer change, or success Audit record. A same-hash definition returns `422 TARGETING_POLICY_UNCHANGED` and directs the caller to exact unchanged rerun.

On success, one PostgreSQL transaction performs, in order:

1. lock/revalidate the Pool and current base policy;
2. INSERT the next immutable `TargetingPolicy`;
3. update `current_policy_id` and increment `CandidatePool.version`;
4. capture the new policy identity/hash, server `as_of`, and input watermark;
5. INSERT one durable `PENDING` `CandidatePoolRun` referencing the new policy;
6. stage the required Pool-updated, policy-created, and Run-requested success audits;
7. commit once.

Any failure before commit rolls back all seven effects.

### 5.5 Response and idempotency

`Idempotency-Key` remains required for every write. Request hashing includes the operation shape, scoped Pool, selected/base policy identity, expected version where present, and canonical policy content where present.

For Run writes, the compatible HTTP response remains `CandidatePoolRunPublic`; `run.id` and `run.policy_id` are the Run and policy identities. Exact replay returns the same `run.id` and `policy_id` (and therefore the same policy + Run), normally with the existing replay marker/status behavior. Full policy content remains available through the policy GET. The same key with different semantic content returns `409 IDEMPOTENCY_CONFLICT`/the existing equivalent and never performs a second mutation.

Replay lookup must occur before rejecting an otherwise stale expected version, so an exact retry after a successful unknown response returns the committed result rather than creating or reporting a second edit.

## 6. Concurrency and invalid writes

Pool row locking uses fresh lock-acquired state (`populate_existing=True`). The frozen outcomes are:

- two different edit requests opened from the same current version: one may commit; the other returns `409 VERSION_CONFLICT` and writes nothing;
- two concurrent deliveries of the same semantic request/key: one policy/Run/audit set commits and both callers resolve to the same identity;
- same key with different body, policy selection, or Pool path: conflict;
- base policy exists but is historical rather than current: `409 VERSION_CONFLICT` for an edit, but it remains eligible for authorized unchanged rerun;
- wrong-Pool direct UUID: fail-closed `404`;
- archived Pool: no new policy or Run (`409 CANDIDATE_POOL_INACTIVE`);
- Buyer authoring/adjustment: rejected by the C3A authoring contract; unchanged authorized rerun only;
- unknown/unsupported Seller field: `422`, with no silent discard;
- stale Web editor: retain the draft locally, show that the current rule changed, refetch Pool/current policy, and require the user to reopen/reapply deliberately; never auto-merge or auto-resubmit.

No rejected stale request may write `CANDIDATE_POOL_UPDATED`, `TARGETING_POLICY_CREATED`, or `CANDIDATE_POOL_RUN_REQUESTED` with `SUCCESS`.

## 7. Post-commit dispatch and worker safety

The API publishes `targeting.materialize_candidate_pool_run` only after the database transaction commits. The task payload contains only the durable Run ID.

If immediate dispatch fails:

- the committed policy, pointer, audits, and `PENDING` Run remain unchanged;
- the API/service does not create a replacement policy or Run;
- retry uses the same idempotency key/Run identity;
- the existing bounded pending-Run reconciler republishes the durable Run;
- the worker locks the Run and claims only `PENDING`, so duplicate delivery cannot materialize it twice;
- worker execution reads the policy pinned by `run.policy_id`.

No enqueue failure is repaired by rewriting historical policy or Run records.

## 8. Historical Web flow

### Candidate Pool creation/detail

- A permitted user may create a Seller Pool with an inline custom `SELLER_V1` builder; owner defaults through the existing selected-Operator behavior.
- Rule history shows version, current marker, creator/time, hash metadata as appropriate, and `查看规则`.
- Only the current Seller policy exposes `调整规则`; historical versions are view/rerun-only.

### Run result page

Every Run page fetches/displays its policy via `run.policy_id`, even if a later policy is current.

- `查看规则`: available for every authorized Run with a resolvable policy.
- `调整规则并重新运行`: available only when the Run policy is the Pool's current valid `SELLER_V1` and the user can mutate. Submission uses Shape C.
- `使用此规则重新运行`: uses Shape B and is available for valid historical Seller rules and valid reviewed Buyer rules.
- A noncurrent historical Run never offers branch editing. It explains that only the current rule can be adjusted.
- Buyer pages have no create/edit/taxonomy controls.
- Unsupported activity controls do not render disabled placeholders that imply availability; they are absent.

After any newly created Run, navigate to its distinct Run URL. Existing C2 selection state is scoped by Pool/Run and therefore starts clean for the new Run.

## 9. Permissions, CSRF, scope, and Audit

Reads require an authenticated session and existing company/Department read scope. Viewer remains read-only. Writes require:

- valid session and CSRF cookie/header equality plus session validation;
- selected active Operator;
- non-Viewer mutation authority;
- existing same-Department or explicitly authorized Super Admin target-Department resolution;
- exactly one nonempty `Idempotency-Key`;
- scoped Pool lookup before policy lookup.

Cross-Department override never permits mixing a target Pool with a policy from another Pool/Department. Policy UUIDs are not capability tokens.

Success Audit records reuse existing actions and store safe metadata only: Pool/policy/Run IDs, policy version/schema/hash, Pool version/current pointer, actor, Department, IP/user agent, and cross-Department override marker. They do not store raw contact values, provider payloads, secrets, or an unbounded policy/evidence dump.

## 10. Activity signals and capacity seam

### C3A P0 capability result

- `LONG_INACTIVITY`: **NOT ACTIVE / NOT EXPOSED**.
- `ACTIVITY_DROP`: **NOT ACTIVE / NOT EXPOSED**.

The current note-count ranges remain authorable under their truthful names. Source freshness remains a data-observation constraint. Neither proves publication inactivity or a trend drop.

### Future closed `SELLER_V2`

Activity authoring, after its backend capability and governance gate pass, must use a new closed typed `SELLER_V2`. It must not add fields silently to immutable `SELLER_V1` semantics and must not introduce a generic DSL.

`LONG_INACTIVITY` and `ACTIVITY_DROP` remain separate typed criteria:

- `LONG_INACTIVITY` accepts approximate Grey Dolphin inactivity evidence when it safely proves the configured coarse threshold; source is visible as Grey Dolphin, API verified, or unknown. Usable Grey Dolphin evidence prevents an API call made only for greater timestamp precision.
- `ACTIVITY_DROP` requires trusted detailed `CURRENT_PUBLIC_VISIBLE` work evidence across the complete frozen window. Xiaohongshu counts include normal/image-text and video works. Required recent/prior window counts, latest-publication evidence, observation time, source, and completeness/trust state are retained. Incomplete coverage, first-page-only works, provider failure, or video-only coverage yields `UNKNOWN`/`INSUFFICIENT_DATA`, never `MATCH`.
- Thresholds are explicit configurable rule values. Tuning defaults may be examples but are not hidden permanent business truth.

Provider use is capacity-driven:

- reuse trusted local cache first;
- never refresh or backfill the full library;
- never trigger enrichment on page open;
- never enrich all `UNKNOWN` candidates simply because a rule ran;
- use bounded API fallback only where evidence materially affects the current assignment target;
- stop expensive enrichment when enough assignable candidates are found or the per-Run call budget is exhausted;
- remain compatible with future Department/day budgets and controlled selected-candidate manual refresh.

Planned assignable-candidate target and maximum provider-call budget are Run execution inputs, never immutable rule fields. C3A P0 does not accept or expose them. The forward-compatible persistence seam is a closed `execution_plan` object in the existing Run watermark when activity execution is later approved; it must be captured before dispatch and must not change historical rule meaning.

## 11. Migration decision

**No migration is required for C3A.** The current schema already provides immutable policy rows, sequential same-Pool versions, current pointer/concurrency token, same-Pool Run linkage, durable PENDING Run identity, request hash/idempotency storage, and Audit persistence.

C3A must not create or reserve a speculative `0008` revision and must not depend on an unmerged Content Activity/D1A migration. A future activity implementation allocates its revision against the then-current Alembic head only if integrated evidence or execution-plan persistence proves the existing schema insufficient.

## 12. Acceptance contract

These are implementation gates, not evidence executed by this docs-only freeze.

### PostgreSQL 16

1. Pool creation commits exactly one Policy V1/current pointer/audit set or none.
2. Exact same-key concurrent policy or adjusted-Run requests commit once and replay the same identities.
3. Two distinct edits with the same `expected_pool_version` produce one winner and one `VERSION_CONFLICT`; policy versions remain committed-gap-free and the loser writes no Run/pointer/success audit.
4. Injected failure after policy INSERT, pointer update, watermark capture, Run INSERT, and audit staging proves total rollback at every pre-commit boundary.
5. A later policy/Run leaves the earlier policy, Run, members, counts, `as_of`, evidence, and watermark policy identity byte/semantically unchanged.
6. Exact historical rerun pins the supplied same-Pool policy without changing current pointer/version.
7. Cross-Pool UUIDs cannot create Runs; same-Pool composite FKs and scoped lookup both hold.
8. Dispatch failure preserves one committed PENDING Run; reconciliation/duplicate delivery materializes it at most once from `run.policy_id`.
9. No policy UPDATE path is used; `created_at`, definition, hash, creator, and version remain unchanged after use.

### API/service

1. All three Run request shapes pass; mixed/partial/extra fields fail `422`.
2. The complete Seller allowlist round-trips canonically; every unknown activity/future key is rejected.
3. Seller kind/type, ranges, enums, exact tags, Buyer read-only, reviewed Buyer rerun, inactive Pool, missing policy, and unchanged-policy errors are covered.
4. Same-Pool/Department/company authorization, Super Admin target scope, Viewer rejection, selected Operator, CSRF, single idempotency header, replay, and key-content conflict are covered.
5. Current-base adjustment succeeds; historical/stale base and stale Pool version return `VERSION_CONFLICT` with zero mutation.
6. Run reads and worker materialization resolve the pinned policy, not current policy.
7. Error envelopes do not disclose cross-Pool policy existence or raw provider/contact data.
8. Direct `POST /{pool_id}/policies` cannot change the current pointer without a Run and returns `TARGETING_POLICY_RUN_REQUIRED` for P0 callers.

### Web

1. Seller builder renders only the eight frozen fields and serializes the exact backend shape, including string `contact_availability`.
2. Data freshness/note counts retain truthful labels; no inactivity/drop controls or claims appear.
3. Buyer snapshot is complete/read-only; Buyer exact rerun works; Buyer create/edit controls are absent.
4. Every Run shows `查看规则`; current Seller Run supports atomic adjust/rerun; historical Seller/Buyer supports exact unchanged rerun only.
5. Stale conflict preserves the draft, explains the conflict, refetches current state, and never auto-overwrites.
6. Dispatch ambiguity retries the same idempotency key and navigates only to the durable returned Run.
7. Page render/open and ordinary P0 Run creation make zero activity-provider calls.
8. Existing polling, terminal-count display, failure-code allowlist, privacy/redaction, and direct Run URL behavior remain unchanged.
9. Full Web regression plus focused builder/history/rerun tests must pass under the required gate command; targeted passes do not waive a failing required full gate.

### C2 selection non-regression

`EXPLICIT`, `ALL_MATCH`, exclusions, cross-page selection, page-size handling, and Pool/Run-scoped reset remain unchanged. `ALL_MATCH` continues to store count + exclusions in the browser and server-resolve authorized `MATCH` rows from one completed Run in bounded chunks. `UNKNOWN` is explicit-only and `NOT_MATCH` remains ineligible/count-only. Creating a new Run changes the selection scope; it never transfers selection from the prior Run.

## 13. Explicitly out of scope

- application code, migrations, deployment, production access, or real provider calls in C3A;
- shared rules/templates, save-as-template, rule marketplace, cloning/branching historical policies, rollback-as-mutation, or generic rule DSL;
- Buyer Pool creation, Buyer taxonomy management, Buyer rule editing, or temporary taxonomy persistence;
- active `LONG_INACTIVITY`, `ACTIVITY_DROP`, `SELLER_V2`, TikHub routing, Content Activity/D1A dependency, full-library enrichment/backfill, page-open refresh, or blind `UNKNOWN` completion;
- Run assignment planner, provider budget enforcement, Department/day budget administration, or selected-candidate manual refresh UI;
- changes to targeting candidate-universe semantics, Campaign membership, C2 selection, taxonomy contracts, RBAC model, or production queue configuration.

## 14. Contract verdict

The smallest safe design is coherent on the verified baseline, uses the existing immutable persistence architecture, closes current/stale edit races, preserves historical Run traceability and C2 semantics, and fails closed for unsupported activity capabilities.

`CANDIDATE_POOL_RULE_BUILDER_C3A_CONTRACT_FREEZE_PASS`

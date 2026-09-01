# Permissions V1 Contract Freeze

Status: **CONTRACT FROZEN**

Date: 2026-09-01

Repository baseline: `codex/permissions-v1@f749115fa4df728585b4e51c976dd7a924f38443`

Scope: contract only. This artifact authorizes no runtime implementation, migration, database change, merge, rebase, commit, push, deploy, or production mutation.

## Executive decision

Permissions V1 replaces the current attribution-only Operator role with a bounded effective-role contract after Operator selection. Department authorization remains the login authority and maximum role ceiling. Business access is the intersection of an active selected Operator, a valid effective role, Department scope, and a closed module grant. V1 does not add action-level ACLs, custom roles, permission groups, Department CRUD, or an Audit viewer.

The selected design is the minimum relational module-grant model. Reusing `department_permissions` cannot express per-Operator module access, and JSON or dynamic-string permissions would weaken validation and tenant integrity. One future `operator_module_permissions` table is therefore the only planned permission table. It must not be created until the Douyin integration candidate and its real Alembic head are frozen.

### Supersession boundary

At the frozen baseline, the existing Phase 1A/Phase 3A rule remains runtime truth: Department role authorizes the Session and Operator role is attribution only. This document changes no current behavior by itself. When the complete Permissions V1 implementation and migration pass their release gate, this contract supersedes only the earlier clauses that make `department_permissions.role` the business effective role or allow business reads without a selected Operator. Existing CSRF, Department scope, Super Admin cross-Department business access, Viewer masking, resource guards, idempotency, and Audit contracts remain in force unless this document explicitly narrows them.

## A. Effective-role contract

### Role order

The only V1 role order is:

`viewer < operator < manager < super_admin`

There are no aliases, custom roles, inherited groups, or parallel capability ranks.

### Department ceiling

- `department_permissions.role` remains the Department authorization ceiling and login authority.
- It is not the selected Operator's business role and is not a module grant.
- A Department must remain active and have exactly one permission row for authentication to succeed.
- Permissions V1 adds no API or Web flow for changing the Department ceiling.

### Effective Operator role

- Before Operator selection, there is no business effective role.
- After selection, `operator.role` is the business effective role only when all of the following are true:
  - the Operator exists and is `active`;
  - the Operator belongs to the authenticated Department;
  - the Department remains `active`;
  - `rank(operator.role) <= rank(department_permissions.role)`.
- The effective role is loaded from current database state on every request. It is not copied into or cached by the Session.
- The ceiling is a validity predicate, not a clamping operation. If `operator.role` is above the Department ceiling, the request fails closed; the server must not silently reduce the role and continue.
- Operator selection must reject an above-ceiling Operator without binding it to the Session.
- If an already-bound Operator later becomes above-ceiling, authentication must reject business authorization and revoke that bound Session. No stale or ceiling-exceeding authority may survive.

### No selected Operator

Without an active selected Operator, the caller may use only:

- public health and Department-login discovery;
- login;
- `GET /api/v1/auth/me`;
- `POST /api/v1/auth/logout`;
- the active same-Department Operator selection directory;
- `POST /api/v1/auth/select-operator`.

All business module reads and writes are denied with `OPERATOR_REQUIRED`. Viewer is not an exception. A viewer without a selected Operator has no per-Operator module grants to evaluate and no reliable business actor for attribution; permitting reads would bypass the V1 permission boundary.

### Immediate authority changes

- Operator status, role, Department ceiling, and module grants are evaluated from current database state on every request.
- Disabling an Operator revokes every non-revoked Session bound to that Operator in the same transaction.
- Role and module-grant changes do not require Session recreation, but the next request must observe the new values.
- Re-enabling an Operator does not revive revoked Sessions.

### Auth response contract

The public auth shape must stop overloading one `role` field with two meanings:

- login and current-auth responses expose `department_role_ceiling`;
- current-auth responses expose `effective_role: Role | null`;
- `effective_role` is `null` until a valid active Operator is selected;
- after selection, `effective_role` is the validated current `operator.role`;
- clients must never infer business authority from `department_role_ceiling` or `operator.role` returned by a directory entry.

The existing ambiguous auth `role` field is replaced by these explicit fields in the V1 contract. Operator directory records continue to expose their stored role for selection and administration, but directory data is not authorization evidence.

## B. Closed module keys and route ownership

The V1 `ModuleKey` set is closed:

- `today_outreach`
- `campaigns`
- `candidate_pools`
- `influencer_library`
- `data_collection`
- `import_history`
- `data_updates`
- `admin`

No API may accept or persist an arbitrary permission string. Persistence must use a closed application enum plus a database allow-list constraint. Adding a module key after V1 requires a new contract and migration.

| Module key | Real or planned Web route | API operation family |
|---|---|---|
| `today_outreach` | `/outreach/today` | `GET /api/v1/outreach-tasks/today`; `GET /api/v1/outreach-tasks/{task_id}`; `POST /api/v1/outreach-tasks/{task_id}/transitions`; `GET /api/v1/outreach-tasks/{task_id}/events` |
| `campaigns` | `/campaigns`, `/campaigns/{id}` | all `/api/v1/campaigns*`; all `/api/v1/outreach-targets*`, including Task creation from a target |
| `candidate_pools` | `/candidate-pools`, `/candidate-pools/{poolId}`, `/candidate-pools/{poolId}/runs/{runId}` | all `/api/v1/candidate-pools*` |
| `influencer_library` | `/influencers`, `/influencers/{id}`, and the production drawer routes | all `/api/v1/influencers*` |
| `data_collection` | `/` and its real data-collection/bulk workspace states | all `/api/v1/collection-jobs*`; all Import Job mutations; shared Import Job detail/file/row reads |
| `import_history` | `/import-jobs` | `GET /api/v1/import-jobs`; shared Import Job detail/file/row reads |
| `data_updates` | `/refresh-queues`, `/refresh-queues/{id}` | all `/api/v1/refresh-queues*` |
| `admin` | planned `/admin/permissions` | planned `/api/v1/admin/operators*`; existing `/api/v1/admin/departments/{id}/reset-password`; existing and Douyin-candidate `/api/v1/admin/content-activity*`, except the capture-token ingest exception defined below |

Shared and cross-module boundaries are frozen as follows:

- `GET /api/v1/import-jobs/{id}`, its file list, and its row list accept `data_collection OR import_history`; all Import Job mutations require `data_collection`.
- Refresh-return processing navigates from `data_updates` into the `/` data-collection workspace and uses Import Job mutations. It therefore requires both `data_updates` and `data_collection`; V1 does not add a payload-dependent permission exception.
- Adding a Candidate Run to a Campaign calls a Campaign API and therefore requires both `candidate_pools` for the source view and `campaigns` for the destination mutation.
- Today-filter lookups that call Campaign or Influencer APIs require the corresponding `campaigns` or `influencer_library` grant. The Web must omit unavailable optional filters rather than bypass the target module guard.
- `/api/v1/auth*`, public Department discovery, health, and the active Operator selection directory are infrastructure operations and are not business modules.

## C. Access matrix and Operator-admin rules

### Module and write matrix

| Effective role | Module access | Existing ordinary write capability | Admin/operator management |
|---|---|---|---|
| `super_admin` | implicit access to all eight modules | yes, subject to existing resource, scope, CSRF, state-machine, and idempotency guards | yes, same Department only |
| `manager` | explicitly granted non-admin modules only | yes, within granted modules; no new action ACL | no |
| `operator` | explicitly granted non-admin modules only | yes, within granted modules; no new action ACL | no |
| `viewer` | explicitly granted non-admin modules only | never | no |

Module access does not grant an action capability. Existing endpoint/domain rules continue to determine which ordinary mutations exist. Viewer write denial is unconditional and is evaluated after module access; a grant can never turn a Viewer mutation into an allowed request.

`admin` is not grantable to Manager, Operator, or Viewer in V1. Super Admin receives it implicitly. Super Admin has no persisted module-grant rows; the Web shows all modules as fixed and non-editable for that role.

### Operator-management API contract

Only an authenticated, active, selected same-Department Super Admin with a valid ceiling may call the management API. The minimal API surface is:

- `GET /api/v1/admin/operators`: list all same-Department Operators, including disabled Operators, with role, status, grants, and concurrency token.
- `POST /api/v1/admin/operators`: create one same-Department Operator with complete initial role, status, and grant state.
- `PUT /api/v1/admin/operators/{operator_id}`: atomically replace the target's mutable name, role, status, and complete grant set.

The update contract requires the target's current `updated_at` as an optimistic concurrency token. The service locks the target, compares the token, applies all changes and Session revocations, writes Audit, and commits once. Grant changes must also advance the target Operator's `updated_at`. A stale token returns `409 VERSION_CONFLICT` with no partial mutation or Audit success record.

The management service must enforce all of the following in the same transaction:

- actor and target belong to the same authenticated Department;
- cross-Department Operator mutation is forbidden even for Super Admin;
- target role may not exceed the Department ceiling;
- Manager, Operator, and Viewer cannot call any Operator-management mutation or elevate any Operator;
- an existing non-Super-Admin Operator cannot be promoted to `super_admin` in V1;
- an additional Super Admin may be added only by creating a new Operator directly as `super_admin`, by an existing active Super Admin, while the Department ceiling is `super_admin`;
- a caller cannot elevate its own role;
- the last active same-Department Super Admin cannot be disabled or demoted;
- self-disable or self-demotion is allowed only when another active same-Department Super Admin remains; self-disable revokes the current Session as part of the successful transaction;
- there is no hard-delete endpoint;
- names remain unique within the Department using the existing database constraint;
- disabled Operators cannot be selected and have no business access.

The last-Super-Admin invariant requires a deterministic lock order over the same-Department active Super Admin set before the count and mutation. An application-only unlocked count is not an acceptable gate.

### Role/grant canonicalization

- New or updated Manager, Operator, and Viewer records carry a complete set of explicitly granted non-admin modules.
- Super Admin has an empty persisted grant set because all modules are implicit.
- Changing a target to Super Admin is not supported for existing Operators in V1.
- Changing a Super Admin to a lower role requires the request to supply the complete new non-admin grant set atomically; no former implicit access or stale grant is inherited.
- Omitting a module from the replacement set revokes it.

## D. Unified backend guard contract

Permission policy is centralized. Routers select a closed `ModuleKey`; they do not implement role, grant, or ceiling logic locally.

The implementation must expose one canonical effective-authorization resolver and two reusable guard forms:

- `require_module_read(ModuleKey | closed any-of set)`;
- `require_module_mutation(ModuleKey | closed any-of set)`.

The canonical resolver performs, in order:

1. authenticate the Session and active Department;
2. require one active selected Operator from that Department;
3. load the current Department ceiling and Operator role;
4. reject an above-ceiling role without clamping;
5. resolve the existing Department resource scope, preserving non-disclosure behavior and explicit Super Admin cross-Department rules;
6. authorize the closed module: implicit all for Super Admin, otherwise an exact same-Department grant row;
7. for mutation, require CSRF and unconditionally reject Viewer;
8. return one immutable authorization context containing Department, actor Operator, ceiling, effective role, resolved scope, and authorized module.

Services that persist or dispatch mutations must accept this canonical context or repeat the same centralized service-level predicate; they may not trust a UI flag or a client-supplied role/module. Existing resource state machines, idempotency, contact masking, Department predicates, and Audit requirements remain additive and may not be removed.

Stable failures are:

- `401 AUTH_REQUIRED` for no valid Session;
- `409 OPERATOR_REQUIRED` for no active selected Operator;
- `403 ROLE_CEILING_EXCEEDED` for an Operator role above the Department ceiling;
- `403 MODULE_ACCESS_DENIED` for a missing module grant;
- `403 PERMISSION_DENIED` for Viewer mutation or a role-specific denial;
- existing 404 non-disclosure behavior for unauthorized cross-Department resource identifiers;
- `403 CSRF_FAILED` for mutation CSRF failure.

UI navigation and buttons may reflect these decisions for usability, but backend guards are the security boundary.

## E. Persistence and backfill contract

No migration is created by this freeze. After the Douyin candidate is frozen, the planned table is `operator_module_permissions` with exactly:

- `operator_id`;
- `department_id`;
- closed `module_key`;
- `created_at` and `updated_at`;
- primary key or equivalent unique constraint on `(operator_id, module_key)`;
- tenant-safe composite foreign key `(operator_id, department_id) -> operators(id, department_id)`;
- a database allow-list constraint for the eight frozen module keys.

Rows are positive grants; absence is denial. There is no `allow/deny`, action, wildcard, JSON policy, condition, resource ID, or permission-group column. Existing no-hard-delete Operator lifecycle remains authoritative.

The migration/backfill must be fail-closed and preserve existing active Operators' business-module visibility:

1. Before any irreversible enum or DDL change, validate every Department permission row and active Operator.
2. Abort if an active Operator has no Department permission row, belongs to an inactive/missing Department, has an unknown role, or has a role above the Department ceiling.
3. Do not auto-change or promote any existing Operator role.
4. For every active non-Super-Admin Operator that passes preflight, insert grants for all seven existing non-admin business modules. This preserves pre-upgrade module visibility; Viewer remains read-only.
5. Active Super Admin Operators receive no rows because their eight-module access is implicit.
6. Disabled Operators receive no automatic grants because they have no current business access. Re-enablement requires an explicit complete grant set.
7. Produce a preflight report comparing the old Department authority with every active Operator role. If switching to the stored Operator role would remove write capability or otherwise change an active Operator's effective business capability, production migration is blocked until the mapping is explicitly approved. The migration must not conceal this difference by copying the Department ceiling into Operator roles.
8. Backfill is idempotent and count-verifiable; post-migration checks must prove every active non-Super-Admin has the seven expected rows and no cross-Department or unknown-module row exists.

New Operators default to no business access unless their create request explicitly supplies a complete valid grant set. Database defaults must not grant modules.

## F. Audit contract

Permissions V1 adds these closed Audit actions in the later migration:

- `OPERATOR_CREATED`
- `OPERATOR_UPDATED`
- `OPERATOR_ROLE_CHANGED`
- `OPERATOR_STATUS_CHANGED`
- `OPERATOR_MODULE_GRANTS_CHANGED`

Each successful security mutation records, atomically with the mutation:

- actor Operator ID;
- target Operator ID as `entity_id`, with `entity_type=operator`;
- Department ID;
- action and result;
- minimal, security-relevant `before` and `after` values;
- request IP, user agent, and timestamps through the existing Audit model.

Module sets are serialized in the frozen module order. A combined full-state update may emit multiple action records in the same transaction when multiple change classes occur. A denied attempt records `result=denied` when the authenticated actor and safe same-Department target identity are known, without disclosing cross-Department existence. Failed transactions must not leave a success Audit record.

Audit payloads must never contain Department passwords, password hashes, Session cookies, Session tokens or hashes, CSRF tokens or hashes, capture tokens, provider credentials, or raw request headers.

## G. V1 Web scope

Permissions V1 adds one production area at `/admin/permissions`, visible only after the current effective context resolves to Super Admin. The area contains:

- all same-Department Operators, including disabled;
- create Operator;
- edit name;
- role;
- active/disabled status;
- the eight-column module matrix;
- one explicit save operation using the full-state API and concurrency token.

For Super Admin rows, all module cells are checked, fixed, and non-editable. For Manager, Operator, and Viewer, `admin` is fixed off. Viewer module rows do not expose or imply write access. The UI must refetch authoritative state after a successful save and must surface `VERSION_CONFLICT`, last-Super-Admin rejection, ceiling rejection, and Session revocation truthfully.

Out of scope:

- Department CRUD or ceiling editing;
- action-level `create/edit/delete/run` ACLs;
- Audit viewer;
- custom roles;
- permission groups, templates, inheritance, wildcards, or row-level ACLs;
- hard delete;
- preview/fixture-only administration as release evidence.

## H. Douyin composition rule

Permissions work must not create a migration before the Douyin integration candidate is frozen.

At composition time:

1. verify the exact Douyin candidate SHA and clean worktree;
2. read the candidate's real sole Alembic head;
3. create the Permissions migration with that exact head as its parent;
4. retain one and only one Alembic head;
5. run the combined migration and PostgreSQL gates from the production baseline through Douyin and Permissions;
6. compare the executed migration identity and release manifest with the committed artifacts.

Permissions guards are additive and must not weaken:

- the existing and Douyin-candidate `super_admin` guard on `/api/v1/admin/content-activity/*` launch/admin operations;
- Candidate Pool module, Viewer-write, selected-Operator, Department-scope, idempotency, and state-machine guards;
- `POST /api/v1/admin/content-activity/douyin/runtime-captures`, which remains selected-Super-Admin-only and `admin`-module protected;
- the Huitun ingest endpoint, which remains a narrowly scoped one-time capture-token capability path and must not be converted into Session/module authorization or accept provider cookies, Authorization headers, or Operator Session material.

The composition gate must cover auth-shell Operator selection, Candidate Pool and long-inactivity flows, Douyin capture launch/ingest separation, single-head migration lineage, and all Permissions negative cases.

## I. Implementation task freezes

### Task 1 — Authorization core

- Scope: closed ModuleKey, effective-role resolver, ceiling validation, read/mutation guard interfaces, stable errors.
- Likely modules: backend auth enums/service/repository and shared API dependencies.
- Tests: role rank, no-Operator denial including Viewer, ceiling failure, immediate role/status/grant changes, Viewer mutation denial.
- Acceptance gate: no business router contains an independent role/grant algorithm.
- Migration impact: none.

### Task 2 — Persistence and PostgreSQL migration

- Scope: create the one grant table, Audit enum additions, preflight, backfill, count/integrity verification.
- Parent gate: exact frozen Douyin Alembic head.
- Tests: upgrade from combined baseline, rejection atomicity, idempotent backfill, tenant FK, unique key, closed module values, single head.
- Acceptance gate: no active Operator is unexpectedly locked out; any old-authority/new-role capability change is explicitly approved.
- Migration impact: one migration, created only after the parent gate.

### Task 3 — Operator-admin service and API

- Scope: list/create/full-state update, row locks, last-Super-Admin invariant, Session revocation, Audit.
- Likely modules: auth schemas/repository/service, admin router, Audit repository/enums.
- Tests: every forbidden actor/target transition, same-Department rule, stale update, concurrent last-admin disable/demote, self-disable, no partial effects.
- Acceptance gate: direct API negatives prove UI cannot bypass the rules.
- Migration impact: uses Task 2 only.

### Task 4 — Module mapping enforcement

- Scope: attach the centralized guards to the frozen operation families, including shared any-of reads and cross-module workflows.
- Likely modules: current HTTP routers and focused domain service entrypoints.
- Tests: one allow/deny/read/write case per role and module, plus shared Import, Candidate-to-Campaign, Today lookup, data-return, and non-disclosure cases.
- Acceptance gate: every business operation has exactly one frozen module owner or closed any-of rule.
- Migration impact: none.

### Task 5 — Permissions Web area

- Scope: `/admin/permissions`, navigation, Operator table/editor, fixed matrix, concurrency/error handling.
- Likely modules: AuthShell/AppShell/navigation, a new admin feature folder and route, Web API client and Vitest.
- Tests: Super Admin visibility, non-admin absence, disabled rows, fixed cells, Viewer wording, save/refetch, conflict and revoked-self flows.
- Acceptance gate: production API-backed UI only; no preview/fixture qualifies as implementation evidence.
- Migration impact: none.

### Task 6 — Combined release gate

- Scope: compose Douyin plus Permissions without weakening either contract.
- Tests: targeted backend/Web/PostgreSQL suites, sole Alembic head, release manifest, production smoke plan.
- Acceptance gate: exact branch/SHA/clean state, migration lineage, backend denial evidence, Audit evidence, and service inventory.
- Migration impact: apply the single Permissions child migration once.

## Contract acceptance checklist

The contract is frozen only if all future implementation evidence proves:

- effective role is the selected active Operator role and never exceeds the Department ceiling;
- no selected Operator means no business access for every role, including Viewer;
- only the eight closed module keys exist;
- Super Admin is implicit-all; other roles use explicit non-admin grants;
- Viewer cannot mutate under any grant combination;
- management is same-Department Super-Admin-only;
- no existing Operator is promoted to Super Admin; a new Super Admin is created directly under the frozen gate;
- last active Super Admin, Session revocation, immediate authority refresh, and Audit rules hold under concurrency;
- the backfill preserves module visibility and blocks unapproved effective-capability changes;
- backend guards, not UI state, decide access;
- the Permissions migration is a child of the real frozen Douyin head and the final graph has one head.

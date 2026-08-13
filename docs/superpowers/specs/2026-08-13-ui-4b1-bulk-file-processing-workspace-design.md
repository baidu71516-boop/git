# UI-4B1 Data Collection Workspace + Bulk File Processing Design

## Status

Design frozen and approved on 2026-08-13. This document defines UI-4B1 only.
Implementation must not begin until the user has reviewed this written
specification.

## 1. Objective

Upgrade `/` from a single-file developer import screen into a professional
data-collection workspace with two modes:

- `单文件导入`: the existing Phase 1B Legacy workflow;
- `批量文件处理`: the real Phase 2 Bulk Job and per-file workflow through the
  point where a unified data preview has been generated.

The page title is `数据采集`. The page description is:

> 上传达人数据，完成文件检查后生成数据预览。

UI-4B1 ends at `数据预览已生成`. It does not render preview contents or allow a
Bulk Confirm.

## 2. Authority and real capability boundaries

The implementation must follow the actual Router, schemas, services, and tests
at `phase-2-bulk-import@b51c9b3`, not planned documentation where the two differ.

Frozen capability boundaries:

- `GET /api/v1/import-jobs` does not exist. The Web must not show Import Job
  history, a task switcher, or a fake list.
- There is no unexclude/re-include endpoint. An excluded file has no restore
  action in this UI.
- There is no task-token polling endpoint. The UI polls Job and File resources.
- UI-4B1 does not call Bulk Confirm and does not show a Confirm button, including
  a disabled placeholder.
- Legacy Retry is not a system capability. Current API tests require Legacy
  `/retry` to return `409 INVALID_STATE_TRANSITION`. UI-4B1 must not invent a
  Legacy Retry button.
- Bulk File Retry and Bulk Job Retry are real capabilities and are in scope.
- The current backend allows acquisition-time mutation only while the Bulk Job
  is `draft` with `preview_revision=0`. After the first Preview it returns
  `IMPORT_BATCH_FROZEN`; therefore this UI disables acquisition-time mutation
  after Preview because of the real backend contract. It must not invent a
  client-only stale transition. If the backend later permits the mutation, the
  backend must authoritatively produce the stale state before the Web changes.
- Every frontend gate is UX only. The backend remains authoritative for state,
  permission, scope, limits, validation, and concurrency.

## 3. Non-goals

UI-4B1 must not implement or expose:

- Unified Preview contents or row tables;
- Preview Summary;
- Screening results;
- Change Summary;
- Category filtering;
- Bulk Confirm or Confirm Result;
- a completed-result page;
- Import Job history;
- localStorage-backed task history;
- unexclude, replace, or delete-file controls;
- fake progress percentages;
- task tokens, plan hashes, manifest hashes, rule hashes, broker IDs, blob IDs,
  internal SHA values, or raw revision hashes;
- UI-4B2, Task 7, Refresh Queue, Campaign, Inbox, CRM, AI, or other placeholder
  modules;
- changes to the frozen UI-1 shell, Sidebar, theme, typography, Influencer list,
  or Influencer detail.

## 4. Approved architecture

Use an isolated Bulk feature instead of expanding the existing 871-line Legacy
component or rewriting Legacy around React Query.

```text
DataCollectionWorkspace
├── LegacyImportWorkspace
└── BulkImportWorkspace
    ├── BulkJobSummary
    ├── BulkFileUploader
    ├── BulkFileTable
    └── shared pure ImportFieldMappingEditor
```

### 4.1 Ownership

`DataCollectionWorkspace` owns only:

- active Tab presentation;
- visited-pane bookkeeping required to lazily mount and then preserve a pane;
- synchronization of the active mode with URL query parameters.

Legacy continues to own its existing collection, job, file, mapping, rows,
polling, busy, notice, and error state. Its request order and state machine are
not migrated to React Query in UI-4B1.

Bulk independently owns its collection, current Bulk Job, files, local upload
queue, mapping draft, React Query queries and mutations, polling decisions,
busy state, notices, and errors.

The two modes must not share a unified import job, mapping, polling, busy, or
error state. They may share only:

- the existing `apiRequest` client;
- exact domain/API types where the contract is genuinely common;
- existing Design System primitives and Ant Design;
- pure, stateless presentation components.

### 4.2 Mapping Editor hardening

The shared Mapping Editor reuses only the editing presentation. Its props are
limited to source fields, canonical choices, a mapping value, an `onChange`
callback, and disabled/read-only presentation.

It must not own or share:

- a Query key;
- a mutation;
- a Job ID or endpoint;
- Legacy or Bulk mapping state;
- busy, notice, or error state;
- polling or submission behavior.

Legacy submits to the Legacy mapping endpoint with Legacy state. Bulk submits to
the per-file mapping endpoint with Bulk state.

## 5. App Shell, navigation, and visual hierarchy

The production page reuses the frozen UI-1 `AppShell`, Sidebar, theme tokens,
spacing, borders, radii, status colors, and typography.

The Sidebar remains exactly the current real navigation:

- `达人库` -> `/influencers`;
- `数据采集` -> `/`.

It must not add Workbench, Settings, Import History, or any other unavailable
destination.

The content hierarchy is fixed:

```text
数据采集
上传达人数据，完成文件检查后生成数据预览。

[ 单文件导入 ] [ 批量文件处理 ]

当前采集任务                         [新建采集任务]
────────────────────────────────────────────────
文件处理                                  [添加文件]

文件 | 状态 | 行数 | 数据取得时间 | 问题 | 操作
────────────────────────────────────────────────
                              gate explanation [生成数据预览]
```

The Bulk pane uses one primary surface and a compact table. It must not create a
card per file, a statistics-card wall, a hero, a gradient, or decorative
illustrations.

The four explanatory cards shown in the temporary brainstorming companion
(`真实导航 / 真实入口 / 真实摘要 / 真实操作`) are design annotations only. They
must not appear in the production page or development preview harness.

## 6. Tabs, URL recovery, and background activity

### 6.1 Tab behavior

- With no relevant query parameters, `单文件导入` is the default.
- `workspace=bulk` selects `批量文件处理`.
- A pane mounts on first visit and stays mounted during the current page
  session, preserving user-entered state.
- Switching Tabs must not reset an existing Legacy or Bulk workspace.
- The active pane value may be reflected with `router.replace` so Tab changes do
  not create noisy browser history.

### 6.2 Current Bulk Job URL

The current recoverable Bulk task is represented by:

```text
?workspace=bulk&bulk_job_id=<uuid>
```

After a successful Bulk Job creation, the Web writes the returned ID to this
URL. On refresh, it loads:

- `GET /api/v1/import-jobs/{id}`;
- `GET /api/v1/import-jobs/{id}/files`;
- `GET /api/v1/collection-jobs/{collection_job_id}` for the real summary.

When switching temporarily to Legacy, the current `bulk_job_id` may remain in
the URL while `workspace` reflects the visible pane, so returning to Bulk can
recover the same task. Clearing the current Bulk task explicitly removes the
ID.

A malformed, missing, forbidden, non-Bulk, or not-found Job produces a formal
error state with an action to clear the current URL. The UI must not infer a Job
from a Collection Job and must not call a nonexistent list endpoint.

No current Bulk Job is persisted to localStorage or presented as history.

### 6.3 Hidden-pane request hardening

Keeping a pane mounted does not authorize unnecessary polling.

- A Bulk Job query polls only while its last real status is an active async
  status such as `previewing`, `confirm_queued`, or `importing`.
- The Bulk File query polls only while at least one real file is `uploaded` or
  `parsing`.
- In-flight uploads or other already-started real mutations continue.
- A hidden, idle Bulk pane does not poll.
- Legacy retains its existing behavior: it polls only the active statuses in
  its current real state machine. Hiding the pane does not cancel a genuine
  running Legacy task.
- Static Draft, Ready, Failed, Excluded, Preview Ready, Preview Stale,
  Completed, or Cancelled data does not receive an interval merely because the
  pane is mounted.

Use fake timers in tests to prove both the ongoing-task and idle-hidden cases.

## 7. Current collection summary

The summary uses only values returned by the real Collection/Auth APIs. The
production default is deliberately compact:

- Collection Job name;
- industry;
- translated Collection Job status.

Current operator identity remains owned and shown by the existing App Shell;
the Bulk summary does not create another operator store.

Although fixtures may contain additional plausible values, the formal summary
must not add fixture-only information. In particular, the approved production
layout does not add the design fixture's `目标 500 位达人` copy. Screening rules,
platform rules, tags, and follower ranges are not rendered because those belong
to later Preview/Screening scope in this UI sequence.

The only header action is the real `新建采集任务` entry. There is no `切换采集任务`
or Import history affordance.

## 8. Empty states and two-stage creation

### 8.1 Empty states

With no current Bulk Job:

- title: `还没有批量采集任务`;
- description: `创建采集任务后，即可添加并处理多个达人数据文件。`;
- action: `新建采集任务` for roles that may mutate.

With a Bulk Job and no files:

- title: `还没有文件`;
- description: `添加 CSV / XLSX 文件开始处理。`;
- action: `添加文件` for roles that may mutate.

Viewer receives the read-only state without an enabled mutation action.

### 8.2 Real creation sequence

The new-collection form preserves the current real Collection Job fields and
submission contract. Creation is deliberately two separate server operations:

1. `POST /api/v1/collection-jobs`;
2. `POST /api/v1/import-jobs/bulk` with the returned
   `{collection_job_id}`.

Both mutations have automatic retry disabled.

After step 1 succeeds, the returned Collection Job and ID are retained in a
dedicated `pendingCollection` state. Step 2 must never resubmit step 1. Closing
and reopening an error presentation must not recreate the Collection Job.

If step 2 returns a deterministic response proving that no Bulk Job was
created, the page may offer `继续创建批量任务`; this action retries only step 2
with the retained Collection ID.

If step 2 has an ambiguous network/transport or server outcome, the UI must not
automatically retry and must not display a one-click retry that could create a
duplicate Bulk Job. The fixed safe copy is:

> 采集任务已创建，但系统无法确认批量文件处理任务是否创建成功。为避免重复创建，系统不会自动重试。请保留当前页面并联系管理员核对。

This limitation exists because Bulk creation has no client idempotency key and
there is no Import Job list/reconciliation API. It must be recorded in the final
report.

## 9. Exact Web API surface used by UI-4B1

All paths are assembled and URL IDs encoded in the feature API module, never in
presentation components.

| Capability | Method and path | Request | Success |
|---|---|---|---|
| List collections | `GET /collection-jobs` | none | `CollectionJobPublic[]` |
| Read collection | `GET /collection-jobs/{id}` | none | `CollectionJobPublic` |
| Create collection | `POST /collection-jobs` | current JSON form | `201 CollectionJobPublic` |
| Create Bulk Job | `POST /import-jobs/bulk` | `{collection_job_id}` | `201 ImportJobPublic` |
| Read Job | `GET /import-jobs/{id}` | none | `ImportJobPublic` |
| List files | `GET /import-jobs/{id}/files` | none | `ImportJobFilePublic[]` |
| Upload one file | `POST /import-jobs/{id}/files` | multipart `file`, `client_file_id`, optional `source_acquired_at` | `201/200 ImportJobFileUploadResult` |
| Update acquired time | `PATCH /import-jobs/{id}/files/{file_id}` | `{source_acquired_at}` | `200 ImportJobFilePublic` |
| Save file mapping | `PUT /import-jobs/{id}/files/{file_id}/mapping` | `{mapping}` | `202/200 ImportJobFilePublic` |
| Retry file | `POST /import-jobs/{id}/files/{file_id}/retry` | no body | `202 ImportJobFilePublic` |
| Exclude file | `POST /import-jobs/{id}/files/{file_id}/exclude` | no body | `200 ImportJobFilePublic` |
| Request/rebuild Preview | `POST /import-jobs/{id}/preview` | `{rebuild:false}` or `{rebuild:true}` | `202/200 ImportDispatchResult` |
| Retry Bulk Job | `POST /import-jobs/{id}/retry` | no body | `202/200 ImportDispatchResult` |

The Bulk feature must never call:

- `GET /import-jobs`;
- `POST /import-jobs/{id}/confirm`;
- an invented unexclude endpoint;
- a task-token endpoint.

Mutations use the existing API client so Session credentials, JSON content type,
FormData boundaries, CSRF cookie/header behavior, and unified-envelope errors
remain centralized.

## 10. Web types and presentation mapping

`types.ts` mirrors the real API, including nullable Legacy fields and all public
fields needed to discriminate a Bulk Job. It does not rename backend enums into
a second state machine and must not use `any`.

Required raw enum coverage:

- Job: `draft | uploaded | parsing | mapping_required | previewing |
  preview_ready | preview_stale | confirm_queued | importing | completed |
  failed | cancelled`;
- File: `uploaded | parsing | mapping_required | ready | failed | excluded`;
- acquisition origin: `server_default | user_confirmed | legacy_unknown`;
- detected type: `csv | xlsx`.

`formatters.ts` translates raw values into Chinese presentation and safe error
copy. Components consume the raw typed data and formatter output; they do not
duplicate mappings.

## 11. Multiple-file upload

`添加文件` accepts multiple `.csv` and `.xlsx` files for the current
`manual_huitun_export` flow.

For every selected file:

- create one stable client-generated `client_file_id`;
- keep that ID across a user-triggered replay while the local File object still
  exists;
- send one multipart request;
- omit `source_acquired_at` unless the user explicitly supplied a value;
- do not derive an acquisition time from filename, mtime, browser clock, or a
  file cell;
- invalidate/update the real file list after success.

The upload scheduler has a hard maximum of two in-flight file requests. Upload
mutations have automatic retry disabled. Local upload presentation may say
`正在上传` or show a safe local upload error, but it must not masquerade as a
backend file enum or show a fabricated percentage.

The UI may block obvious client-known file-count/byte violations for UX, but the
backend remains authoritative for 25 MiB/file, 20 occurrences, 100 MiB/batch,
10,000 included rows, MIME/content validation, idempotency, and concurrency.

### 11.1 Deterministic concurrency test

Use deferred upload promises and an active-request counter. Select at least four
files, prove that only two requests start before either deferred promise is
resolved, then resolve them in a controlled order and prove the remaining
requests start without the counter ever exceeding two. Also prove that a failed
request receives no automatic replay.

## 12. File table and display truth

Columns are fixed:

1. 文件;
2. 状态;
3. 行数;
4. 数据取得时间;
5. 问题;
6. 操作.

The desktop action copy is `修改数据取得时间`. On a narrow formal layout it may
be shortened to `修改时间` only when context remains unambiguous.

### 12.1 File status labels

| API status | Label |
|---|---|
| `uploaded` | 已上传 |
| `parsing` | 处理中 |
| `ready` | 已就绪 |
| `mapping_required` | 需要字段映射 |
| `failed` | 处理失败 |
| `excluded` | 已排除 |

Color is a restrained secondary cue. Text remains the source of meaning.

### 12.2 Row count

`raw_rows` defaults to zero even before parsing, so zero alone is not proof that
the file truly has zero rows.

- When parsing evidence is unavailable (`detected_fields` is null and the
  default zero may be provisional), display `—`.
- When parsing evidence exists, display the real value, including a real `0`.
- Any real positive `raw_rows` is displayed.

### 12.3 Acquisition time

Display the server value with timezone-safe formatting. When
`source_acquired_at_confirmation_required=true`, display `待确认` and provide a
real confirmation/edit flow. Submitting the same server value still calls PATCH
so the backend can record confirmation and clear the flag.

Do not expose acquisition-origin enum values as employee-facing copy.

### 12.4 Safe issue copy

Known stable error codes map to concise Chinese summaries. Raw Python errors,
SQL, worker/broker language, HTTP dumps, and stack traces never render. An
unknown file error uses a fixed fallback such as:

> 文件处理失败，请重试或排除该文件。

## 13. File actions

All actions are additionally disabled or hidden for Viewer and when the backend
contract has frozen the Batch. The backend still decides the request.

| File status | Production actions |
|---|---|
| `uploaded` | 排除 when the real state permits |
| `parsing` | `—` |
| `ready` | 修改数据取得时间; 排除 before freeze |
| `mapping_required` | 处理字段映射; 修改/确认数据取得时间; 排除 |
| `failed` | 重试; 修改/确认数据取得时间; 排除 |
| `excluded` | `—` |

`排除` uses `Popconfirm` and never says delete. There is no re-include action.
The UI does not rely on a repeated File Retry being idempotent because the
current service returns 409 once the file has already moved to `parsing`.

## 14. Preview gate and Job states

The frontend enables `生成数据预览` only when its last real data clearly shows:

- at least one non-excluded file;
- every included file is `ready`;
- no included file requires acquisition-time confirmation;
- the user is not Viewer;
- no conflicting mutation is in flight.

When disabled, show the specific employee-facing blocker. A backend 409 after
an enabled click is handled normally and proves that the backend is final.

First request:

```json
{"rebuild": false}
```

Stale rebuild:

```json
{"rebuild": true}
```

Job presentation:

| API status | UI-4B1 presentation/action |
|---|---|
| `draft` | 文件准备中; show file workspace |
| `previewing` | 正在生成数据预览; poll Job |
| `preview_ready` | 数据预览已生成; no preview contents or Confirm |
| `preview_stale` | 数据预览需要重新生成; `重新生成数据预览` |
| `failed` | 处理失败; generic `重试` through Job Retry |
| `confirm_queued` | 正在准备导入; read-only and poll |
| `importing` | 正在导入; read-only and poll |
| `completed` | 导入完成; no result page |
| `cancelled` | 已取消; read-only |

The public Job does not expose `failed_stage`; the Web must not infer Preview
versus Confirm failure. Generic Job Retry delegates recovery to the backend. It
does not create a new Confirm authorization.

## 15. Loading, errors, permissions, and data safety

Reuse `AppLoading`, Ant Design `Spin`, `Alert`, `Empty`, Button loading, and the
existing theme. Do not create a second loading/error design language.

Formal loading/error states cover:

- Job recovery loading;
- Collection creating;
- Bulk Job creating;
- file uploading;
- file parsing;
- file mapping;
- file retry;
- Preview requesting;
- Preview polling;
- read and mutation API failures.

Known API codes receive safe Chinese mappings. Read errors provide a real read
retry. Bulk read queries follow the existing Web convention: no retry for an
`ApiClientError` below 500 and at most one retry for a network/5xx failure.
Mutations do not automatically retry. Technical details may be logged to the
console only when they do not expose secrets, raw imported rows, or Contact
data.

Session role controls presentation, but the UI never treats hiding/disabling as
authorization. The existing backend continues to enforce Session, selected
Operator, CSRF, role, department scope, and Audit. Existing AuthShell ownership
of operator selection and logout is unchanged.

## 16. Proposed Web file boundaries

```text
apps/web/src/features/imports/
  types.ts
  api.ts
  queries.ts
  formatters.ts
  data-collection-workspace.tsx
  bulk-import-workspace.tsx
  bulk-import-workspace-view.tsx
  components/
    bulk-job-summary.tsx
    bulk-file-uploader.tsx
    bulk-file-table.tsx
    import-field-mapping-editor.tsx
  preview-fixtures.ts                 # only if the dev harness is needed
```

The exact final split may collapse trivially small files, but must preserve:

- centralized API paths and types;
- React Query ownership for Bulk;
- pure presentation reuse for the harness;
- Legacy/Bulk state isolation;
- no dozens of micro-components.

The existing Legacy component may remain in its current file with a compatible
export. `AuthShell` changes only to mount `DataCollectionWorkspace` and use the
approved title/description; auth logic is not moved.

## 17. Test plan

### 17.1 Legacy regression

Cover:

- default `单文件导入` entry;
- current Collection Job creation and selection;
- single-file upload contract;
- polling through current active states;
- Mapping editor and Legacy mapping request;
- Preview rebuild;
- Confirm with current `preview_revision`;
- Cancel;
- completed result remains available;
- Operator gate and Viewer read-only behavior;
- failed status renders safe Chinese handling and permits the existing real
  recovery path of a new upload/new task;
- no assertion that Legacy Retry exists and no fake Legacy Retry control.

### 17.2 Bulk API contract tests

Assert exact methods, encoded paths, JSON bodies, FormData field names, response
typing, and mutation retry settings for every endpoint in section 9. Assert that
the feature never calls Import Job list, Confirm, unexclude, or a task endpoint.

Existing API-client tests continue to protect credentials, CSRF, JSON content
type, and FormData boundary behavior.

### 17.3 Bulk workspace tests

Cover at minimum:

- no-Job Empty;
- two-stage creation and URL write;
- step-2 deterministic failure retries only Bulk creation;
- step-2 ambiguous result shows the exact frozen safe copy and performs no
  automatic replay;
- the Collection POST count remains exactly one after any step-2 failure path;
- Job-with-no-files Empty;
- multiple local files and deterministic maximum concurrency of two;
- no upload automatic retry and no percentage UI;
- all six backend file statuses and Chinese labels;
- unknown row count versus a real zero;
- acquisition time, pending confirmation, exact PATCH, and frozen behavior;
- the pure Mapping Editor reused with isolated Legacy and Bulk owner state;
- exact per-file Mapping request;
- File Retry;
- Exclude confirmation and absence of unexclude/delete language;
- UX Preview blockers and backend-conflict handling;
- first Preview request;
- generic Job Retry;
- stale Preview rebuild;
- Preview Ready endpoint state;
- `confirm_queued`, `importing`, `completed`, and `failed` compatibility;
- no Preview contents, Summary, Screening, Change Summary, Category Filter,
  Confirm request, Confirm button, or Confirm result.

### 17.4 URL, polling, auth, and safety tests

Cover:

- no query parameters defaults to Legacy;
- `workspace=bulk&bulk_job_id=<uuid>` recovers Job and Files;
- malformed, 403, 404, and Legacy/non-Bulk IDs;
- clearing the current ID;
- no localStorage access and no Import Job list request;
- Tab state is preserved after first mount;
- an idle hidden pane performs no interval requests;
- a hidden pane with a genuine running task continues only the required polling;
- Viewer mutation controls;
- existing Operator requirement;
- safe error mappings and no raw technical output.

## 18. Development-only visual preview

If real data cannot safely and deterministically construct the necessary Bulk
states, add:

```text
/dev-ui-preview/data-collection
```

Requirements:

- route returns `notFound()` outside `NODE_ENV=development`;
- uses in-memory fixtures only;
- performs zero Auth requests and zero business API requests;
- writes no DB data;
- is not linked from the formal Sidebar;
- reuses the real Bulk presentation components;
- contains no brainstorming annotation cards;
- may expose clearly development-only fixture selection outside the production
  presentation surface.

Tests must explicitly prove both production `notFound()` and zero Auth/API
requests. The harness is committed separately as:

```text
chore(web): add bulk workspace visual preview
```

## 19. Browser and quality verification

Browser verification is required in addition to tests:

- open the real `http://localhost:3000/`;
- verify the frozen UI-1 shell and only the two real navigation destinations;
- verify the approved title, description, default Legacy Tab, Bulk Tab, Empty,
  representative file table, mapping/time actions, Preview blockers, failed,
  stale, ready, and Task 6 compatibility states;
- verify no fatal console errors;
- inspect desktop-first layout and a narrower viewport;
- use the development harness for states that cannot be safely created against
  a real backend.

Run:

```bash
pnpm --filter @influencer-outreach/web exec prettier --check src tests
pnpm --filter @influencer-outreach/web lint
pnpm --filter @influencer-outreach/web typecheck
pnpm --filter @influencer-outreach/web test
pnpm --filter @influencer-outreach/web build
git diff --check
```

Review against Hallmark Philosophy, Hierarchy, Execution, Specificity,
Restraint, and Consistency; each must score at least 3. Confirm that the
production page contains no design-only annotation cards and that all displayed
summary fields are real API values.

## 20. Commit and final-report requirements

Product implementation is one independent commit:

```text
feat(web): add bulk file processing workspace
```

The optional development preview is a second independent commit using the exact
message in section 18.

The final report must explicitly state:

- Legacy Retry is not a current system capability; this is an existing contract
  limitation, not a UI-4B1 defect;
- no Import Job list exists;
- no unexclude exists;
- no task-token polling exists;
- UI-4B1 does not implement Bulk Confirm;
- an ambiguous Bulk-create outcome cannot be reconciled safely with the current
  API and therefore is never automatically retried;
- whether the development preview harness was added;
- all validation results and the final clean `git status`;
- whether UI-4B2 may start.

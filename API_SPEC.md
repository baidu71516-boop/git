# API 规范

## 0. 状态与优先级

Base：`/api/v1`

- Phase 1A–1C 接口已实现；以 Router、Schema 和测试为运行事实源。
- 当前开发分支已实现 Phase 2 Task 1–6 的 Bulk Import、Structured Screening、Unified Preview 与 Atomic Confirm/Recovery 接口；仍以 Router、Schema 和测试为运行事实源。
- Task 7 Freshness 与 Task 8 Refresh Queue 已实现；Task 9 Refresh Return 及后续 Phase 2 接口仍为 **Phase 2 Planned**，不能据此宣称服务端当前可调用。
- Phase 2 详细语义以 `docs/PHASE_2_SCOPE.md` 为准。
- 旧的 `/imports` 草案路径已废弃；正式导入资源前缀为 `/import-jobs`。

统一成功/失败 Envelope：

```json
{
  "success": true,
  "data": {},
  "error": null,
  "request_id": "..."
}
```

页面分页：

```json
{
  "items": [],
  "page": 1,
  "page_size": 50,
  "total": 1000
}
```

所有 Phase 2 业务 Mutation 都要求有效 Session、已选 Operator、CSRF 和后端 RBAC；Phase 2 GET 只要求有效 Session。公开 `GET /departments`、`POST /auth/login` 以及尚未选择 Operator 的 select/logout 认证流程按 Auth 专用规则处理，不受这句通用业务规则覆盖。Operator 只提供 Audit 归属，不改变 Session 权限。

---

## 1. Auth（Implemented）

- `GET /departments`
- `POST /auth/login`
- `POST /auth/select-operator`
- `GET /auth/me`
- `POST /auth/logout`
- `GET /operators`
- `POST /admin/departments/{id}/reset-password`

登录输入：

```json
{
  "department_id": "uuid",
  "password": "user input",
  "remember_me": false
}
```

---

## 2. Collection Jobs

### Implemented

- `POST /collection-jobs`
- `GET /collection-jobs`
- `GET /collection-jobs/{id}`

### Phase 2 Tasks 1–6 Implemented

- 现有 create/read 响应增加 `screening_rules` 与 `screening_rules_revision`。
- `PUT /collection-jobs/{id}/screening-rules`

MVP 规则结构：

```json
{
  "schema_version": 1,
  "platforms": ["xiaohongshu"],
  "source_tags_exact_any": ["美妆"]
}
```

Followers 继续使用 CollectionJob 现有 `follower_min/follower_max` 列，不在 JSON 中复制；`schema_version`、完整 JSON、`screening_rules_revision` 与这两个范围列共同进入 rule hash。

PUT 必须携带 `expected_revision`；初始 `screening_rules_revision=1`，成功后单调加 1，revision 冲突返回 409。更新不覆盖已经进入 Import Preview 的 rule snapshot/hash。

Screening 结果只允许 `MATCH / NOT_MATCH / UNKNOWN`，只读取本 Batch owner ImportRow 的 Canonical incoming record。数据库旧值不得补齐本次缺失字段；industry、subdirection、purpose、notes 不参与规则推断，缺少真实数据时必须 UNKNOWN，不调用 AI。

---

## 3. Import Jobs

### Phase 1B Implemented

- `POST /import-jobs`：单文件 multipart 上传并创建 ImportJob。
- `GET /import-jobs/{id}`
- `GET /import-jobs/{id}/rows`
- `PUT /import-jobs/{id}/mapping`
- `POST /import-jobs/{id}/preview`
- `POST /import-jobs/{id}/confirm`
- `POST /import-jobs/{id}/cancel`

### Phase 2 Tasks 1–6 Implemented — Bulk Batch

- `POST /import-jobs/bulk`：创建 Draft Batch，一个 ImportJob 对应一个 Bulk Batch。
- `GET /import-jobs`
- `GET /import-jobs/{id}`
- `POST /import-jobs/{id}/files`：上传一个文件 occurrence。
- `GET /import-jobs/{id}/files`
- `PATCH /import-jobs/{id}/files/{file_id}`：仅在首次 Preview 前修改 `source_acquired_at`。
- `PUT /import-jobs/{id}/files/{file_id}/mapping`
- `POST /import-jobs/{id}/files/{file_id}/exclude`
- `POST /import-jobs/{id}/files/{file_id}/retry`
- `POST /import-jobs/{id}/preview`
- `GET /import-jobs/{id}/rows`
- `POST /import-jobs/{id}/confirm`
- `POST /import-jobs/{id}/retry`
- `POST /import-jobs/{id}/cancel`

冻结设计中的 `POST /import-jobs/{id}/files/{file_id}/replace` 当前尚未实现，不能按现有接口调用；当前可通过 exclude 旧 occurrence 后上传新 occurrence 完成显式替换流程。该状态说明不授权 Task 7 或其他新范围。

`0004` 与单文件兼容桥必须同版本上线：现有 `POST /import-jobs` 的新 Job 也创建 position=1 的 ImportJobFile，其 acquisition 为 NULL/origin `legacy_unknown`、`source_acquired_at_confirmation_required=false`，但不创建或伪造 client-ID alias；Parse/Mapping/Preview 同步 occurrence 且新 Row 写入 file FK。这是 Phase 1B 兼容路径，不得用它伪造 observed time；需要 Freshness observation 的新流程使用 Bulk Draft endpoint。

Task 8 的 `POST /import-jobs/bulk` JSON 仍只接受 `collection_job_id`，传入 `refresh_queue_id` 按 extra-forbid 返回 422。`0005` 只先建立 nullable 同部门 FK；可选 `refresh_queue_id` 请求、状态校验与回流处理属于 Task 9，尚未实现。一个 Job 最多关联一个 Queue、一个未完成 Queue 可接收多个回流 Job仍是 Task 9 冻结契约。

`POST /import-jobs/{id}/files` multipart 至少包含：

- file
- client_file_id
- source_acquired_at（可选；缺失时由服务端使用接受该文件的时间）

新 Bulk Job 的顶层 legacy 单文件字段 `stored_file_id/original_filename/mime_type/file_size/sha256/detected_fields/field_mapping/mapping_hash` 返回 null；文件事实只从 `/import-jobs/{id}/files` 返回。Phase 1B legacy Job 继续返回原值。

同一 Job 内相同 SHA 不创建第二个 occurrence，返回现有 occurrence 和幂等标识。不同 Job 允许相同 SHA、允许复用 Storage Blob，但必须重新 Parse/Preview。

`import_job_file_client_ids` 是 `(import_job_id, client_file_id) → import_job_file_id` 的唯一权威持久化映射。一个 occurrence 可有多个 aliases；同 Job 内 client ID 永久绑定，同 client ID 可跨 Job 使用。alias 使用 Job 内 unique 和 `(import_job_file_id, import_job_id)` 复合 FK；Redis、Audit JSON 和内存状态均不是幂等事实源。

上传幂等真值：

| 请求/已有状态 | API 结果 |
|---|---|
| A+SHA-X；A/X 均不存在 | 创建或复用 StoredImportFile X，创建 occurrence X 和 alias A→X；返回 created |
| A+SHA-X；alias A→X | 返回 occurrence X，`idempotent=true`，不创建新记录 |
| A+SHA-Y；alias A→X | 409 `IDEMPOTENCY_CONFLICT`；不修改绑定、不覆盖文件 |
| B+SHA-X；X occurrence 已存在、B 未出现 | 新增 alias B→X，返回同一 occurrence/idempotent SHA result，不创建第二 occurrence |
| B+SHA-Y；alias B→X | 409 `IDEMPOTENCY_CONFLICT`；不修改既有记录 |

并发 A+X/A+X 必须收敛为一个 occurrence/一个 alias；A+X/B+X 必须收敛为一个 occurrence/两个 aliases；A+X/A+Y 必须只有一个 A binding，其中一个成功、另一个 deterministic 409。不得出现 500、duplicate occurrence、分叉 alias 或 silent overwrite；实现使用 transaction、PostgreSQL constraints 与 Job 范围锁，不使用全局锁。

任何幂等重试都不得刷新第一次记录的 `source_acquired_at`。人工修改 acquisition time 只能在 Draft/首次 Preview 前完成，并产生专用 Audit。

文件响应必须返回 `source_acquired_at_confirmation_required`。初次上传显式提供合法 `source_acquired_at` 时 origin=`user_confirmed`、confirmation required=false；普通首次上传缺省值时 origin=`server_default`、confirmation required=false。跨历史 Job 复用 SHA 且未显式提供 acquisition time 时，新 occurrence 为 origin=`server_default`、confirmation required=true；用户必须显式 PATCH 确认/修正时间后变为 origin=`user_confirmed`、confirmation required=false，即使时间值不变也要留下 Audit，之后才可 Preview。

`source_acquired_at_confirmation_required` 是 occurrence 待确认状态的唯一权威持久化事实源。API/Service 不得通过动态查询其他 Job、`error_code`、Redis、Audit JSON 或内存状态推导或保存该事实；Audit 只记录动作，不充当当前状态。

`source_acquired_at` 必须带时区，不得晚于服务器接受时间加 validated clock-skew（默认 5 分钟）。

MVP validated limits：每文件仍为 25 MiB；每 Batch 最多 20 occurrences、累计原始文件 100 MiB、included rows 10000。文件/字节超限返回 413；行数超限返回稳定业务错误。

Batch File 状态：

- uploaded
- parsing
- mapping_required
- ready
- failed
- excluded

`uploaded/parsing/mapping_required/failed` 都是 blocking；只有至少一个 included file 且所有 included files 都为 ready 才能 Preview，否则 Batch 保持 Draft 并返回稳定冲突错误。当前 API 支持用户在 Preview 前 exclude 或 retry；冻结设计中的原位 replace endpoint 尚未实现。第一次成功 Preview 后，文件集合、Mapping 与 `source_acquired_at` 永久冻结。

Job 级 `POST /retry` 只按持久化 `failed_stage` 恢复：Preview 失败回 `previewing`；已人工确认的 Confirm 失败回 `confirm_queued` 并重用同一 revision；未知 legacy stage 返回 409。

Rows 保持 Phase 1B 兼容协议：

- `offset`
- `limit`
- `action`
- Phase 2 additive `category`

`action` 与 `category` 同传返回 422。多文件 Row 按 file position、row_number、row id 稳定排序。不引入第二套 page/page_size 协议，也不允许同一请求混用两套分页方式。

Preview 响应至少包含：

- preview_revision
- file_count/occurrence_count/excluded_file_count
- raw_rows
- unique_rows
- create/update/no_change/skip/error/manual_review
- internal_duplicate_rows
- warning_rows
- possible_duplicate_contact_rows
- screened_rows and screening MATCH/NOT_MATCH/UNKNOWN counts
- change_summary

Preview 不写达人业务表。Confirm 必须显式携带 revision，整批重新计算 Plan Hash，在一个 PostgreSQL transaction 中提交；MVP 不提供 auto-confirm。

Task 6 Confirm 请求体：

```json
{
  "preview_revision": 3
}
```

Task 6 Confirm/Recovery 契约：

- Confirm 只接受当前 `preview_ready` revision；首次请求把 Job 转为 `confirm_queued`，并将 Job 状态、confirmed revision、唯一持久 task token 和首次 dispatch reservation 在一个数据库事务中提交，提交后才投递 Broker。
- Broker payload 只携带 canonical UUID 形式的 Job ID、`preview_revision` 和持久 task token；Worker 必须用这三个值精确 claim PostgreSQL task request，不能把 Broker/Celery metadata 当作事实源。
- 相同 Job/revision 在 `confirm_queued`、`importing` 或 `completed` 重放时返回原 task token 和 HTTP 200，不创建第二个 active Confirm；不同或旧 revision 返回 409 `PREVIEW_STALE`。
- Worker 写入前重建完整统一 Plan，并核对 file manifest/SHA、Mapping、acquisition、Screening、normalized row、owner/identity、行级 plan hash、locator 与汇总。任一差异使整批进入 `preview_stale`，不写达人业务表。
- Confirm 对所有 included files 只使用一个 PostgreSQL business transaction；业务结果、ImportRow committed lineage、Job completed、task completed 和成功 Audit 同事务提交。`MANUAL_REVIEW`、`ERROR`、`SKIP` 行保留 committed lineage，但不自动写达人业务实体。
- API 在业务事务提交后投递 Broker；投递异常不会撤销已持久化 request。Scheduler 通过到期 reservation 和 `FOR UPDATE SKIP LOCKED` reconciliation 重投，因此 API/Redis/Broker 短暂故障不会形成永久 crash gap。
- `POST /import-jobs/{id}/retry` 只根据持久 `failed_stage` 恢复失败的 Preview 或已人工确认的 Confirm；Confirm retry 复用原 confirmed revision，并为已终结的旧 request 创建新的持久 token，不创建或隐式确认新 Preview。
- Cancel 只取消尚未进入数据库业务执行的 `requested/retry_wait` task；已有 `running` task 时返回 409 `IMPORT_TASK_RUNNING`，不执行不安全的进程内 kill。

---

## 4. Influencers

### Phase 1C Implemented（只读）

- `GET /influencers`
- `GET /influencers/filter-options`
- `GET /influencers/{influencer_id}`
- `GET /influencers/{influencer_id}/metric-snapshots`

不得增加 Influencer POST/PUT/PATCH/DELETE 作为 Phase 2 范围。Influencer 继续公司级读取，不按 Owner、Import Department 或 Refresh Queue Department 分片。

### Phase 2 Task 7 Implemented — Freshness additive fields

`GET /influencers` additive filters：

- freshness_status
- requires_refresh
- last_huitun_observed_before
- last_huitun_observed_after

账号级 additive response fields：

- last_huitun_observed_at
- last_huitun_imported_at
- freshness_status
- freshness_age_days

达人级 list/detail summary additive fields：

- freshness_status
- requires_refresh

Legacy 没有可靠 acquisition time 时 `last_huitun_observed_at=null`。不得用 ImportRow.committed_at、Influencer.updated_at 或文件 mtime 伪造 observed time。

筛选语义固定为：`freshness_status` 精确匹配任一 eligible active Huitun account/source；`requires_refresh` 精确匹配达人汇总布尔值；before/after 分别为 inclusive `<=`/`>=`，缺失 observation 不匹配日期范围。所有四个参数为单值，重复参数、无时区 datetime、非法布尔值或 after > before 均返回 422。四个正式角色继续进行公司级读取，GET 不写 business Audit。

---

## 5. Refresh Queues（Phase 2 Task 8 Implemented）

- `POST /refresh-queues`
- `GET /refresh-queues`
- `GET /refresh-queues/{id}`
- `GET /refresh-queues/{id}/items`
- `POST /refresh-queues/{id}/export`
- `POST /refresh-queues/{id}/cancel`

Queue 由 Department 拥有；创建与导出要求非 Viewer、selected Operator、CSRF、RBAC 和 Audit。候选从公司级 Influencer Library 计算。

创建参数至少包含：

```json
{
  "department_id": "optional target department UUID",
  "requested_limit": 100,
  "today_total_limit": 200,
  "refresh_limit": 100
}
```

`department_id` 缺省为 Session Department；只有 `super_admin` 可显式指定其他部门，manager/operator 跨部门请求返回 403。`as_of`、`policy_version` 和 `criteria_snapshot` 由服务器生成，不接受客户端传入；MVP 也不接受任意 `criteria` JSON。三个 limit 必须为严格正整数，并满足 `requested_limit <= refresh_limit <= today_total_limit`，其中 `requested_limit <= 2000`。额度只是本 Queue 的用户输入参数，系统不宣称知道灰豚真实剩余额度。

Task 8 固定 `policy_version=1`，priority tier 为整数 1–5，reason codes 为 `FRESHNESS_UNKNOWN / VERY_STALE / STALE / AGING / FOLLOWERS_MISSING`。Queue creation、priority、baseline 与 Identity snapshot 使用同一个 server UTC `as_of` 和同一 candidate statement；不接受 `priority_score`、自定义权重或客户端 reason。

Queue/Item GET 使用 `offset=0`、`limit=50`、最大 200。Queue 按 `created_at DESC, id DESC`；Item 按创建时冻结字段 `priority_tier ASC, baseline_last_observed_at ASC NULLS FIRST, influencer_id ASC, platform_account_id ASC, id ASC`，不用 live Freshness 导致翻页漂移。

导出成功返回 `text/csv` attachment，是统一 JSON success Envelope 的明确例外；错误仍使用 Envelope。CSV 只包含数据库真实存在的公开 Identity，必须防公式注入且不得包含 Contact。真实灰豚批量定位输入尚未完成产品流程验证，因此当前契约不承诺导出 CSV 能直接被灰豚消费。

---

## 6. Audit

- `GET /audit-logs`：仅管理员（后续接口，按实际 Router 为准）。

Phase 2 Task 1–6 已实现 AuditAction：

- IMPORT_BATCH_CREATED
- IMPORT_BATCH_PREVIEW_CREATED
- IMPORT_BATCH_CONFIRM_REQUESTED
- IMPORT_BATCH_COMPLETED
- IMPORT_BATCH_FAILED
- IMPORT_BATCH_RETRIED
- IMPORT_BATCH_CANCELLED
- IMPORT_FILE_UPLOADED
- IMPORT_FILE_EXCLUDED
- IMPORT_FILE_RETRIED
- IMPORT_FILE_MAPPING_UPDATED
- IMPORT_FILE_SOURCE_ACQUIRED_AT_UPDATED
- COLLECTION_SCREENING_RULES_UPDATED

Stale Confirm 复用现有 `IMPORT_PREVIEW_STALE`。`IMPORT_FILE_REPLACED` 仍属后续 planned action：

- IMPORT_FILE_REPLACED

Task 8 已实现：

- REFRESH_QUEUE_CREATED
- REFRESH_QUEUE_EXPORTED
- REFRESH_QUEUE_CANCELLED

Audit 不记录原始行、Contact、密码、Session/Auth/Access Token、Secret 或 Storage 内容；persisted import task token 只可作为 Task 6 的必要 task identity 白名单字段。

---

## 7. 后续阶段 API（不属于当前 Phase 2）

AI、Playbook、Campaign、Campaign Lead、Email、Inbox、CRM、Analytics 和 Mailbox 的 API 需在对应阶段重新冻结后实施。旧草案 endpoint 不构成当前实现授权，也不属于 Phase 2 Task 0–13。

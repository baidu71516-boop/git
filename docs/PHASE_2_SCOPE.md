# Phase 2 — Huitun Bulk Acquisition & Freshness Management

文档状态：**DESIGN FROZEN — Phase 2 Task 0**
冻结日期：2026-08-11
实现基线：`main@4c973c1`，tag `phase-1c-final`

本文是 Phase 2 的唯一权威实施与验收协议。旧的 Phase 2 Browser Automation 方案以及“Phase 2 = SOP + Campaign + AI”的排期均已人工废弃。本次设计冻结不代表代码、Schema、Migration、API、Worker 或 Web 已实现，也不授权自动开始 Task 1。

若本文与旧版 Phase 2 描述冲突，以本文和已确认的 Phase 1A–1C 决策为准。Phase 1A–1C 的认证、权限、导入、去重、非破坏性合并、Contact 保护和不可变 Snapshot 语义不得被 Phase 2 改写。

---

## 1. 阶段目标

Phase 2 的业务路线固定为：

```text
灰豚批量筛选与人工导出
→ 多文件上传
→ 逐文件解析与标准化
→ 批内与数据库去重
→ 确定性 Screening
→ 统一 Preview
→ 人工 Confirm
→ 现有 Merge
→ Influencer Library
→ Freshness 管理
→ Refresh Queue
→ 人工从灰豚重新取得数据
→ 回到同一 Preview / Confirm / Merge
```

核心目标：

1. 一次处理多个灰豚 CSV/XLSX，并形成一个统一 Preview。
2. 让较大的达人池在系统内完成确定性二次筛选。
3. 可靠区分数据取得时间、导入时间、来源声明时间和指标历史。
4. 将有限的每日导出额度优先用于最值得刷新的达人。
5. 刷新结果仍通过 Phase 1B 的同一套 Planner、Confirm 和 Merge 回流。

---

## 2. 明确非目标

Phase 2 MVP 不包含：

- Browser Automation 或 Playwright 灰豚采集。
- 灰豚登录、验证码处理、登录态托管或多账号池。
- 自动绕过灰豚额度、风控或访问限制。
- 未经验证的灰豚 API、Token 或批量定位能力。
- AI semantic matching、AI 达人判断或 AI 排序。
- Playbook、Campaign、邮件、Follow-up、Inbox、Outreach。
- CRM 工作流、完整 Analytics 或需求池。
- 抖音、快手、视频号或其他平台 Connector。
- Influencer 通用 Mutation、Owner/Contact/Tag 批量维护。
- Auto Confirm。
- Freshness Policy 数据表。
- DailyQuotaPlan 数据表。
- 原始文件 archive/delete lifecycle。
- 达人库整体视觉重构。

未来若存在正式官方 Connector，它也只能输出既有 Canonical Contract，并进入相同的 Preview → Confirm → Merge。

---

## 3. Phase 1B/1C 可复用基线

必须直接复用：

- `StoredImportFile`
- `CollectionJob`
- `ImportJob`
- `ImportRow`
- CSV/XLSX Parser
- Huitun/Generic Adapter
- Canonical Normalization Contract
- `ImportPlanner`
- Preview Revision / Plan Hash
- Confirm Revalidation
- `Influencer`
- `InfluencerPlatformAccount`
- `PlatformAccountSourceIdentity`
- `InfluencerSourceState`
- `InfluencerCurrentMetrics`
- `InfluencerMetricSnapshot`
- `InfluencerContact`
- Audit、Session、RBAC、CSRF 和 Department Scope

禁止重新实现或分叉：

- Hard Identity Matcher
- Email possible-duplicate 规则
- Non-destructive Merge
- Newer/Same/Older/Unknown 语义
- CurrentMetrics 更新规则
- Snapshot Key 与不可变历史
- Manual Contact 保护
- SourceIdentity 匹配

所有最终达人写入都必须继续经过现有 Planner 和人工 Confirm。

---

## 4. 核心聚合与术语

### 4.1 Aggregate

正式选择：**一个 `ImportJob` = 一个多文件 Bulk Batch**。

不新增：

- `ImportBatch`
- `BatchRow`

`ImportJob` 继续承担：

- Department/Operator/CollectionJob 归属
- 状态机
- Preview Revision
- Plan Hash
- 汇总
- Confirm task
- 原子事务
- Audit entity

### 4.2 文件 occurrence

新增 `ImportJobFile` 表示一个文件在一个 ImportJob 中的 occurrence。它和 `StoredImportFile` 的区别是：

- `StoredImportFile` 是按 SHA-256 去重的不可变内容对象。
- `ImportJobFile` 保存本批次中的顺序、文件名、Mapping、状态、取得时间和错误。

`ImportJobFileClientId`（物理表 `import_job_file_client_ids`）保存上传客户端幂等键。它是
`(import_job_id, client_file_id) → import_job_file_id` 的唯一权威持久化映射：

- 一个 occurrence 可以拥有多个 client-ID aliases。
- 同一 ImportJob 内一个 `client_file_id` 永久绑定到一个 occurrence。
- 不同 ImportJob 可以使用相同 `client_file_id`。
- Legacy single-file occurrence 没有客户端幂等键，可以拥有 0 个 alias；Migration 不得伪造 alias。
- Redis、Audit JSON、内存状态和 occurrence 元数据都不得作为幂等事实源。

### 4.3 Row Locator

`ImportRow.row_number` 继续表示原文件物理行号。多文件中的唯一定位为：

```text
(import_job_file_id, row_number)
```

Planner、Plan Hash、Warning 和 UI 都必须使用文件感知的 Row Locator，不能再假设 Job 内行号全局唯一。

---

## 5. 状态机

### 5.1 ImportJob 状态

Phase 2 为新 Bulk Job 增加 `draft`：

```text
draft
→ previewing
→ preview_ready
→ confirm_queued
→ importing
→ completed
```

辅助状态继续保留：

- `mapping_required`（只保留 Phase 1B legacy Job 兼容；新 Bulk Job 使用文件级状态并保持 `draft`）
- `preview_stale`
- `failed`
- `cancelled`

Phase 1B 的 legacy `uploaded` 状态保留兼容，不强行伪造历史状态。

规则：

- 多文件收集期间 Job 保持 `draft`。
- 文件损坏或 Mapping 未解决时 Job 仍为 `draft`，不是可 Confirm 的半批次。
- 只有 Preview/Confirm 的系统级任务在自动重试耗尽后才进入 Job `failed`；文件级错误留在 Draft/File 状态。
- `draft` 可取消。
- 已产生第一版成功 Preview 后，文件集合、Mapping 和 `source_acquired_at` 永久冻结。
- 冻结条件使用 `preview_revision > 0`，不增加空的 `files_frozen_at` 字段。
- `preview_stale` 只允许重建当前冻结文件集，不能借机改变文件或取得时间。

Bulk Job 系统失败必须保存 `failed_stage=preview|confirm`。自动重试耗尽后进入 `failed`；人工 `POST /retry` 的转换固定为：

- `failed_stage=preview`：`failed → previewing`，重新读取当前文件状态并重新生成 Preview。
- `failed_stage=confirm`：`failed → confirm_queued`，只重投已经人工确认的同一 revision；不得生成新的 Confirm 授权。
- Legacy `failed_stage=null`：不允许自动判断阶段，返回 409 并保留人工排障证据。

文件级 parse failure 不把 Bulk Job 置为 failed，而是把对应 ImportJobFile 置为 `failed`，Job 继续为 `draft`。

### 5.2 ImportJobFile 状态

文件级状态固定为：

- `uploaded`
- `parsing`
- `mapping_required`
- `ready`
- `failed`
- `excluded`

Blocking file 精确定义为非 excluded 的 `uploaded`、`parsing`、`mapping_required`、`failed`，以及 `source_acquired_at_confirmation_required=true` 的文件；只有所有 included files 都为 `ready` 且没有 acquisition confirmation blocker 才可请求统一 Preview，否则返回 409。

Batch 必须至少包含一个 included + ready file；全部 excluded 的空 Batch 也必须返回 409。

文件状态转换固定为：

```text
uploaded → parsing → ready | mapping_required | failed
mapping_required → parsing      (mapping/retry)
failed → parsing                (retry)
ready → parsing                 (Draft mapping update)
uploaded | ready | mapping_required | failed → excluded   (revision=0 only; parsing 中需等待任务结束)
excluded → parsing              (Draft replace 恢复相同 SHA only)
```

文件 Parse 采用现有 Worker 异步执行。每个 ImportJobFile 保存 `parse_task_id`、`parse_attempts`、`parse_started_at`、`parse_completed_at`；Scheduler 根据持久状态恢复 broker dispatch 丢失，不依赖 Job 级单一 task id 跟踪多个文件。

---

## 6. 多文件上传与文件处理

### 6.1 上传方式

Web 可以一次拖入多个文件，但 API 每个请求只流式上传一个文件：

- 保持既有每文件 25 MiB 应用硬限制。
- 不把多个 25 MiB 文件塞入一个受 Nginx 总 body 限制的 multipart。
- 浏览器同时最多上传两个文件。
- 每个文件使用 `client_file_id` 支持网络重试幂等。
- Batch 安全上限使用 validated Settings；MVP 默认最多 20 个 occurrence、累计原始文件 100 MiB、included rows 10000。超出文件/字节限制返回 413，超出行限制返回稳定业务错误；excluded occurrence 仍计入已占用存储字节。

幂等真值规则（alias 表是唯一权威映射）：

| Case | 当前 Job 状态 | 结果 |
|---|---|---|
| A + SHA-X，A/X 均不存在 | 无 alias A、无 X occurrence | 创建或复用 StoredImportFile X；创建 occurrence X；创建 alias A → X；返回 created |
| A + SHA-X，alias A 已指向 X | alias A → occurrence X | 返回同一 occurrence，`idempotent=true`；不创建 alias/occurrence |
| A + SHA-Y，alias A 已指向 X | alias A → occurrence X，Y≠X | 返回 409 `IDEMPOTENCY_CONFLICT`；不修改 alias/occurrence，不覆盖文件 |
| B + SHA-X，B 不存在但 X occurrence 已存在 | alias A → occurrence X | 创建 alias B → 同一 occurrence X；返回 existing/idempotent SHA result；不创建第二 occurrence |
| B + SHA-Y，alias B 已指向 X | alias B → occurrence X，Y≠X | 返回 409 `IDEMPOTENCY_CONFLICT`；不修改任何既有绑定 |

并发最终状态必须由 PostgreSQL transaction、unique/composite FK 约束和 Job 范围锁共同保证，不使用全局锁：

- `A+X / A+X`：一个 occurrence、一个 alias；一个创建，其余稳定幂等返回。
- `A+X / B+X`：一个 occurrence、两个 aliases；两者都指向同一 occurrence。
- `A+X / A+Y`：client A 最终只绑定一个 occurrence；其中一个成功，另一个稳定返回 409，不得出现 500、静默覆盖或分叉 alias。
- IntegrityError 重试/重读只能用于约束冲突 reconciliation，数据库约束始终是最终事实源。

幂等重试保持第一次 `source_acquired_at`，不得按重试时间刷新；初次上传显式提供的值记为 `user_confirmed`，未提供时才使用服务器接受时间并记为 `server_default`。

### 6.2 相同 SHA

同一个 ImportJob 内：

- 相同 SHA 第二次上传不得创建第二个 occurrence。
- 返回已有 `ImportJobFile`，并标记 `idempotent=true`。
- 不生成第二套 ImportRows，不抬高 Raw count。
- 普通重复上传命中 excluded occurrence 时仍返回该 excluded occurrence；不会隐式恢复或创建新行。

不同历史 ImportJob：

- 允许相同 SHA。
- 可以复用 `StoredImportFile` blob。
- 必须重新 Parse、Normalize、Match 和 Preview。
- 禁止复用历史 Preview 或历史 Plan Hash。
- 若 SHA 在更早 Job 已存在，初次上传未显式提供 acquisition time 时，新 occurrence 使用服务器接受时间、origin=`server_default`、`source_acquired_at_confirmation_required=true`，并作为 blocking file；必须在 Preview 前通过显式 PATCH 确认/修正时间（即使数值不变），使 origin 变为 `user_confirmed` 且 confirmation required 变为 false。初次上传已显式提供合法 acquisition time 时直接记为 origin=`user_confirmed`、confirmation required=false；未复用历史 SHA 的普通 server-default 上传也为 false。该 occurrence-level Boolean 是待确认状态的唯一权威持久化事实源，禁止通过动态查询其他 Job、`error_code`、Redis、Audit JSON 或内存状态推导/保存该事实。两种确认路径都写 Audit，但 Audit 不是事实源。

### 6.3 损坏、替换、排除和重试

Preview 前允许：

- replace：multipart 上传替代内容，保留旧 occurrence/Audit，排除旧文件并增加新的 position。若替代内容 SHA 与被 excluded 的旧 occurrence 相同，则只重新启用原 occurrence 并重新 Parse，不创建第二行。
- exclude：不进入本批次 Preview，但保留上传事实。
- retry：仅重试可重试的解析/基础设施阶段。
- 更新 Mapping。
- 修改 `source_acquired_at`。

Preview 后以上操作全部禁止。

### 6.4 Schema 不一致

- 每文件保存独立 detected fields 和 Mapping。
- 任一 included file 需要 Mapping 时，整个 Job 不得生成统一 Preview。
- 不要求多个文件 Header 完全相同，只要求每个文件能映射到同一 Canonical Contract。

---

## 7. 解析、标准化与统一 Preview

Draft 文件 Worker 先执行：

1. 验证 Storage 大小与 SHA-256。
2. 使用现有安全 Parser 完整扫描，检测 Header/编码/MIME、行数和结构错误；只保存文件级计数/诊断，不提前创建缺少 action 的 ImportRow。
3. 能使用显式/默认 Mapping 时置 ready；需要用户 Mapping 时置 mapping_required；文件级失败置 failed。

统一 Preview 再执行：

1. 重验所有 included ready file 的 Storage 完整性，并使用同一安全 Parser 重新读取原始行。
2. 使用每文件 Mapping 和 Source Adapter 标准化 raw rows。
3. 将所有 included files 的 Canonical Records 组成统一 Batch Context。
4. 进行批内 hard identity grouping。
5. 使用现有 Planner 与数据库 Matcher 规划每个 owner row。
6. 在一个事务中持久化本次 Preview Revision、normalized data、merge plan 和 summary。

Preview 不写 Influencer、PlatformAccount、Contact、Metrics 或 Snapshot 业务表。

生成 Preview 时必须锁定 Job 与 ordered file manifest，事务内重查至少一个 included ready file 且无 blocking file。Batch Plan Hash/Revalidation 输入覆盖全部 occurrence（包括 excluded），至少包含：

- import_job_id / preview_revision
- ordered `file_id + position + StoredImportFile.sha256`
- 每文件 `mapping_hash + source_acquired_at + source_acquired_at_origin + source_acquired_at_confirmation_required + status/included`
- originating collection_job_id + screening rule_hash
- optional refresh_queue_id（只在 `0005` 部署后进入 manifest；`0004`/Task 2 阶段不接受该字段）

所有上传、replace、exclude、Mapping 和 acquisition mutation 也必须锁定 Job，并在 `preview_revision=0` 时才可提交。Confirm 必须重新计算同一 manifest/hash；任何差异进入 `preview_stale`，不得使用半套文件继续。

---

## 8. 三层去重

### Layer 1

同一个文件内部重复。

### Layer 2

同一个 ImportJob 不同文件之间重复。

### Layer 3

与数据库现有 PlatformAccount 重复。

所有层只使用 Phase 1B 已冻结的 hard identities：

1. `platform + platform_account_id`
2. `source + platform + external_source_id`
3. `platform + normalized_profile_url`

禁止使用：

- Email
- nickname
- handle
- bio
- MCN
- AI 相似度

批内必须检查所有已提供 hard identity keys 的连通关系，不能只比较单个 primary key。

Owner 规则：

1. 完全相同 payload：文件 position、row_number 最早者。
2. Canonical `source_updated_at` 明确不同时，按 Phase 1B 既有来源新鲜度语义选择较新记录。
3. Canonical `source_updated_at` 相同或缺失且字段冲突时：整组 `manual_review`，不得猜测或拼接两行。
4. Hard keys 已落到不同数据库账号：`manual_review`。

`ImportJobFile.source_acquired_at` 只描述 observation 与 Refresh fulfillment，不参与 owner 选择，也不得改变 Phase 1B Current Profile/CurrentMetrics 的 Newer/Same/Older 合并顺序。

非 owner 行保存 raw/provenance，action 为 `skip`；其 `merge_plan` 中保存稳定的 owner ImportRow ID、file_id、position 和 row_number，不新增另一套 BatchRow/duplicate relation 表。

同 Email 不同达人继续各自存在，只标记 `possible_duplicate_contact`。

---

## 9. Preview 汇总、分类与分页

统计不变量：

```text
raw_rows =
  create + update + no_change + skip + error + manual_review

internal_duplicate_rows =
  带 BATCH_DUPLICATE reason 的 skip rows

unique_rows = raw_rows - internal_duplicate_rows

existing_rows = changed_rows + no_change_rows
```

说明：

- `raw_rows` 和所有 action/screening counts 只统计当前 manifest 中 included files；excluded occurrence 只进入文件统计和 lineage。
- `occurrence_count = included_file_count + excluded_file_count`；Summary 的 `file_count` 固定等于 `included_file_count`。
- Warning、Possible Duplicate Contact 是叠加维度，不能与 action counts 相加。
- `unique_rows` 表示批内 hard identity 去重后的行，不等于最终成功写入主体数。
- `changed_rows` 对应有效 `update` 计划。
- `invalid_rows` 对应 `error`。

统一 Summary 至少返回：

- file_count
- occurrence_count
- excluded_file_count
- raw_rows
- unique_rows
- internal_duplicate_rows
- existing_rows
- new_rows
- changed_rows
- no_change_rows
- warning_rows
- error_rows
- manual_review_rows
- possible_duplicate_contact_rows
- screened_rows
- screening match/not_match/unknown counts

行级 category：

- `attention`
- `error`
- `manual_review`
- `warning`
- `changed`
- `new`
- `no_change`
- `duplicate`
- `all`

默认优先级：

```text
error
→ manual_review
→ warning
→ changed
→ new
→ no_change
→ duplicate
```

Action counts 是互斥统计；Warning 与 Possible Duplicate Contact 仍是叠加维度。`attention` 是 `error OR manual_review OR warning` 的便捷查询；其他 category 按对应 action/reason predicate 查询，因此一行可以同时出现在 `warning` 与 `new/changed/no_change` 视图。请求同时提交 `action` 与 `category` 时返回 422，禁止隐式组合。

Rows 继续服务端分页，并保持 Phase 1B 的 `offset=0`、`limit=50`、最大 `200` 兼容契约。不得默认向浏览器返回或渲染全部 2000 行。

稳定排序：普通 `all` 按 `ImportJobFile.position ASC, ImportRow.row_number ASC, ImportRow.id ASC`；需要处理的组合视图先按上述 category priority，再使用同一 file/row/id tie-breaker；单 category 也使用 file/row/id。多文件后禁止只按 row_number 排序。

---

## 10. CollectionJob Screening

### 10.1 归属

MVP 一个 ImportJob 只绑定一个 originating CollectionJob，只评估该 Job。

Multiple CollectionJob Matching 和持久关系延期到 Full。

### 10.2 Structured Rules

新增 versioned `screening_rules`。Schema 版本与业务修订号是两个独立概念：JSON 内使用 `schema_version`，CollectionJob 列使用单调递增的 `screening_rules_revision`。V1 contract：

```json
{
  "schema_version": 1,
  "platforms": ["xiaohongshu"],
  "source_tags_exact_any": ["科技", "3C数码"]
}
```

Followers 继续使用 CollectionJob 现有 `follower_min/follower_max`，不在 JSON 中复制。

冻结语义：

- 不从 `industry`、`subdirection`、`purpose` 或 `notes` 推断规则。
- Screening 只读取本 Batch owner ImportRow 的 Canonical incoming record；数据库旧 Current Projection 和 Merge 后有效值不得补齐本次缺失字段。
- platform 使用 Enum 精确匹配。
- Source Tag 使用 Canonical incoming `source_tags`，按 trim 后原值、大小写敏感的完整 ANY 匹配。
- Tag rule 未配置则不参与判断。
- Tag rule 已配置但来源数据缺失/为空则 UNKNOWN。
- Followers 必须是真实非负 integer，范围包含上下边界；缺失或非法为 UNKNOWN。
- 不同字段之间 AND。
- 任一明确 false → NOT_MATCH。
- 全部已配置规则 true → MATCH。
- 无 false 且至少一个 unknown → UNKNOWN。
- 不使用 AI 或同义词。

Schema 规则：`platforms=[]` 或 `source_tags_exact_any=[]` 等同对应规则未配置；非空 Tag item 先 trim、不得为空、最大 160 字符，保留大小写和原值，trim 后重复项返回 422。没有任何已配置规则时结果为 UNKNOWN。

Screening 分母 `screened_rows` 是有有效 Canonical Record 的 unique owner rows；internal duplicate skip 与 error 不计入。`MATCH + NOT_MATCH + UNKNOWN = screened_rows`，manual_review 只要 Canonical Record 有效仍可独立得到 Screening 证据。

Screening 结果是 Preview 分类证据，不改变 hard match，也不自动阻止有效达人进入公司级 Library。MVP 不因 NOT_MATCH 自动删除或跳过达人；用户仍通过 Preview 人工 Confirm。

结果进入 `merge_plan.screening`，至少包含：

- result
- rule_schema_version
- rule_revision
- rule_hash
- per-rule evidence

`rule_hash` 覆盖 `schema_version`、完整 `screening_rules` JSON、`screening_rules_revision` 以及 CollectionJob 现有 `follower_min/follower_max`。Preview 和 Confirm 均重新读取并校验同一规则快照。

---

## 11. Freshness 时间语义

Freshness 粒度为：

```text
PlatformAccount + Source
```

不得混淆：

| 字段/概念 | 语义 |
|---|---|
| `Influencer.created_at` | 主体首次入库 |
| `Influencer.updated_at` | 主体行修改 |
| `ImportRow.committed_at` | 行成功 Confirm 的系统时间 |
| `SourceState.source_updated_at` | 来源文件声明时间 |
| `CurrentMetrics.source_updated_at` | 当前指标采用的来源声明时间 |
| `MetricSnapshot.captured_at` | 唯一 Snapshot 首次落库时间 |
| `ImportJobFile.source_acquired_at` | 这份数据实际从灰豚取得的大致时间 |
| `last_huitun_imported_at` | 最近成功导入灰豚行的系统时间 |
| `last_huitun_observed_at` | 最近已确认的 `source_acquired_at` |

计算集合固定为：

- ImportJob `source_type=manual_huitun_export` 且最终 `completed`。
- ImportJobFile included、未 excluded，且关联目标账号的来源为 Huitun。
- ImportRow 已成功 Confirm，`matched_platform_account_id` 指向目标账号，`committed_at` 非空。
- Action 只包括 `create`、`update`、`no_change`。
- `skip`（包括 Batch Duplicate）、`error`、`manual_review` 以及 cancelled/failed Job 均不形成 observation。

`last_huitun_imported_at` 是上述成功行集合中 `committed_at` 的最大值；它即使存在也不能填充 observed time。`last_huitun_observed_at` 只能取关联 included file 中 `source_acquired_at` 非空且 `source_acquired_at_confirmation_required=false` 的最大值。confirmation required=true 不得推进 observed time；Legacy 回填 occurrence 的 acquisition time 为 NULL，因此只能得到 imported time。

### 11.1 source_acquired_at

- 新 Bulk Draft `/import-jobs/{id}/files` 上传默认使用服务器收到/接受该文件的时间；Legacy 单文件兼容 endpoint 保持 acquisition unknown。
- 用户可以在第一次 Preview 前修改为真实导出/获取时间。
- `source_acquired_at_origin` 固定为 `server_default / user_confirmed / legacy_unknown`；上传显式值或显式 PATCH（即使数值不变）把 origin 置为 `user_confirmed`，上传缺省值为 `server_default`，Legacy 回填为 `legacy_unknown`。
- 数据库不变量固定为：`legacy_unknown` 必须对应 `source_acquired_at IS NULL`；`server_default/user_confirmed` 必须对应非空 acquisition time。
- 输入必须是 timezone-aware，且不得晚于服务器接受时间加 validated clock-skew（默认 5 分钟）。
- 人工修改必须记录 before/after、Job/File、Department、Operator 与时间的 Audit；不得记录 Raw Row 或 Contact。
- 第一次 Preview 后冻结。
- 不从文件名、mtime 或灰豚未知字段猜测。
- Legacy Phase 1B 文件保持 NULL，不使用 created_at/committed_at 回填伪造。

### 11.2 Legacy

Legacy 没有 acquisition time 时：

- `last_huitun_observed_at = null`
- Freshness 为 `unknown`
- 可以单独展示真实的 `last_huitun_imported_at`
- 禁止把 imported_at 标为 observed_at

### 11.3 状态阈值

MVP 使用 validated Settings，并按 UTC elapsed duration 判定：

- `fresh`：elapsed `<= 7 * 24h`
- `aging`：`7 * 24h < elapsed <= 30 * 24h`
- `stale`：`30 * 24h < elapsed <= 90 * 24h`
- `very_stale`：`elapsed > 90 * 24h`
- `unknown`：无可靠 observation

阈值必须严格递增，不创建 Freshness Policy 表。Asia/Shanghai 只用于时间展示和业务日期，不参与 elapsed duration 分段。

普通达人库 GET 以服务器处理请求时的 UTC instant 计算；Refresh Queue 在创建事务中固定一个服务器生成的 `as_of`，所有候选与 reasons 使用同一 as_of，客户端不能覆盖。

达人汇总不得使用最新账号掩盖过期账号：任一 eligible active account 为 unknown/stale/very_stale 时，Influencer 标记 `requires_refresh=true`。

账号级响应保留每个 Huitun Source 的状态；Influencer 汇总采用最差顺序 `unknown → very_stale → stale → aging → fresh`。零个 eligible Huitun account 时汇总状态为 unknown，但 `requires_refresh=false`，避免把当前无法进入 Queue 的主体伪装成可刷新候选。`freshness_age_days` 为非负 elapsed 整日向下取整；unknown 返回 null。

列表筛选固定为单值并与既有筛选 AND：`freshness_status` 精确匹配任一 eligible account/source；`requires_refresh` 精确匹配汇总布尔值；before/after 为 timezone-aware UTC instant，分别使用 `<=`/`>=`，两者同传要求 after <= before，缺失 observation 不匹配时间范围。

---

## 12. Refresh Priority 与每日额度

### 12.1 Candidate

Candidate 必须：

- Influencer active 且未软删除。
- PlatformAccount active。
- 该账号存在 Huitun SourceState、Huitun SourceIdentity 或成功 Huitun Import lineage；不得只凭 PlatformAccount 最初 `source` 字段猜测。
- 至少真实存在一个可导出定位字段：platform_account_id、profile_url、account_handle、account_name 或 external_source_id。
- 未被同 Department 另一个 `pending/stale_return/unresolved` Queue Item 占用。

### 12.2 MVP Priority

MVP 不冻结复杂权重，使用 deterministic tier 和 reason codes：

1. `unknown` 且有 identity。
2. `very_stale`。
3. `stale`。
4. `aging`。
5. fresh 但真实 `followers_count` 缺失。

稳定排序：

```text
priority_tier ASC,
last_huitun_observed_at ASC NULLS FIRST,
influencer_id ASC,
platform_account_id ASC
```

不得使用当前不存在的“最近联系”“高价值”或 AI 推断。

### 12.3 Daily Quota

MVP 不创建 DailyQuotaPlan。Queue 创建请求必须包含：

- today_total_limit
- refresh_limit

校验固定为正整数 `today_total_limit > 0`、`refresh_limit > 0`，且 `refresh_limit <= today_total_limit`；Queue 的 `requested_limit` 必须为正整数且 `requested_limit <= refresh_limit`。Queue `as_of` 由服务器生成，不接受客户端伪造未来/过去基线。

系统只做建议：

```text
suggested_new_acquisition = today_total_limit - refresh_limit
```

系统不宣称知道灰豚真实剩余额度，也不硬编码新达人/刷新比例。

---

## 13. Refresh Queue

Refresh Queue 由 Department 拥有，但候选来自 company-level Influencer Library。Queue 不改变 Influencer 的公司级读取语义。

### 13.1 Queue Header

`refresh_queues` 至少保存：

- department_id
- created_by_operator_id
- status
- as_of
- requested_limit
- today_total_limit
- refresh_limit
- policy_version
- criteria_snapshot
- created_at/updated_at/exported_at/completed_at/cancelled_at

`requested_limit` 以 `(PlatformAccount, Source)` Queue Item 数量计，不以 Influencer 主体数计；同一 Influencer 的多个 eligible 账号可以各占一个 Item，UI 必须同时显示 Item 与唯一 Influencer 计数。

`policy_version` 是代码中 validated Freshness/priority config 的版本号，不是 Policy 表 FK；`criteria_snapshot` 由服务器生成，保存创建时实际阈值、limits 和固定 Candidate/Priority 规则。MVP 不接受未冻结的任意客户端 `criteria` JSON；后续 Settings 变化不得改写历史 Queue。

Queue 状态固定为：

```text
open → exported
open | exported → completed
open | exported → cancelled
```

`exported` 不是终态，允许后续回流和幂等重复导出。只有所有 Item 都为 fulfilled 终态时才自动变为 `completed`；存在 pending、stale_return 或 unresolved 时不得完成。取消 Queue 时，所有未 fulfilled Item 在同一事务中变为 cancelled 并释放候选占用；completed/cancelled 均为终态。

### 13.2 Queue Item

`refresh_queue_items` 至少保存：

- department_id
- queue_id
- influencer_id
- platform_account_id
- source
- priority_tier
- priority_reasons
- identity_snapshot
- baseline_last_observed_at
- baseline_source_updated_at
- status
- fulfilled_import_job_id / fulfilled_import_row_id
- fulfilled_at
- last_return_import_job_id / last_return_import_row_id
- created_at / updated_at

Item 状态固定为：

- `pending`
- `fulfilled_changed`（终态）
- `fulfilled_no_change`（终态）
- `stale_return`
- `unresolved`
- `cancelled`（终态）

`pending/stale_return/unresolved` 可被后续有效回流推进为 fulfilled 或由 Queue 取消为 cancelled；fulfilled/cancelled 状态不可回退。

唯一键：

```text
(queue_id, platform_account_id, source)
```

并发与归属约束：

- Queue 增加 unique(id, department_id)，Item 用 `(queue_id, department_id)` 复合 FK。
- Item 使用 `(platform_account_id, influencer_id)` 复合 FK，保证账号属于主体。
- Fulfilled Job/Row 和 Last Return Job/Row 各自必须 all-null 或 all-non-null，并通过 `(row_id, job_id)` 复合 FK 保证同一 Import。
- `fulfilled_changed/fulfilled_no_change` 必须同时拥有 fulfillment Job/Row/at；其他状态的 fulfillment 三字段必须全空。Last Return pair 可为 stale/unresolved 保留最近一次回流证据。
- ImportJob 的 `(refresh_queue_id, department_id)` 复合 FK 保证回流 Job 与 Queue 同 Department。
- 针对 `pending/stale_return/unresolved` 建 partial unique `(department_id, platform_account_id, source)`，禁止两个并发 Queue 占用同一候选。
- Queue 创建按稳定账号顺序取得数据库/advisory lock，并在同一事务内重查 partial unique；冲突候选不重复插入。
- `created_by_operator_id` 是独立 Audit FK；允许 super_admin 以管理部门 Operator 为其他业务部门创建 Queue，Operator 不改变目标 Department Scope。

`identity_snapshot` 只保存创建 Queue 时真实存在的公开定位字段，不保存 Contact 或完整 Metrics。

### 13.3 CSV

CSV 只允许输出数据库真实拥有的字段，例如：

- influencer_id
- platform
- account_name
- platform_account_id
- account_handle
- profile_url
- external_source_id（真实存在时）
- followers_count
- baseline_last_observed_at（Queue 创建时冻结的 observation 快照）
- freshness
- priority
- reasons

CSV 必须防公式注入：任何字符串首字符或前导空白后的首字符属于 `= + - @ TAB CR` 时，在导出值前加单引号并使用标准 CSV quoting；不得改写数据库原值。导出是 Mutation-style business action，必须 Session + selected Operator + CSRF + RBAC + Audit。

---

## 14. Refresh 回流

用户重新从灰豚取得文件后，创建新的多文件 ImportJob，并可关联一个 `refresh_queue_id`。

Queue 必须与 ImportJob 同 Department 且状态为 open/exported；completed/cancelled Queue 不接受新回流。一个 ImportJob 最多关联一个 Queue，一个未完成 Queue 可由多个后续 ImportJob 分批回流。

回流仍按以下顺序：

```text
Adapter
→ Normalize
→ 现有 Hard Matcher
→ Preview
→ 人工 Confirm
→ 现有 Merge
→ Queue fulfillment
```

Queue identity 不能成为新的 hard dedupe 依据。

只有现有 Hard Matcher 已把成功 owner row 解析到 `platform_account_id` 后，才按 `(refresh_queue_id, platform_account_id, source)` 查找 Item；找不到 Item 不影响普通 Merge，但不产生 Queue fulfillment。

NO_CHANGE 可以 fulfill，必须同时满足：

```text
returned ImportJobFile.source_acquired_at
>
queue_item.baseline_last_observed_at
```

保守边界：

- 有有效业务变化的成功 owner row：acquisition 非空且 baseline 为空（首次可靠 observation）或严格晚于 baseline 时进入 `fulfilled_changed`；小于或等于非空 baseline 时进入 `stale_return`。
- `baseline_last_observed_at IS NULL` 时无法证明“严格晚于”，NO_CHANGE 不自动 fulfill，Item 进入 `unresolved`。
- acquisition time 为 NULL 时进入 `unresolved`；小于或等于非空 baseline 时进入 `stale_return`。
- `source_acquired_at_confirmation_required=true` 时视为尚未确认的旧 Blob replay 风险，进入 `unresolved`，不得自动 fulfill。
- acquisition 严格较新且 `source_acquired_at_confirmation_required=false` 的 NO_CHANGE 才进入 `fulfilled_no_change`。
- ERROR、SKIP、MANUAL_REVIEW 不 fulfill，记录 Last Return 并进入/保持 `unresolved`。

上述 acquisition/SHA 规则只判断 Refresh observation，不参与 Phase 1B SourceState/CurrentMetrics 的 Newer/Same/Older Merge。

Queue fulfillment 与现有 Merge 在同一 Confirm 事务中完成，并必须幂等。

---

## 15. Change Summary、Metrics 与 Snapshot

现有 `merge_plan` 增加通用 `change_summary`，不增加逐 Metrics 数据库列：

```json
{
  "effective_changes": [],
  "ignored_changes": [],
  "freshness_changes": [],
  "historical_observations": []
}
```

每项可包含：

- scope
- field
- before
- incoming
- after
- effect

规则：

- 只列真正变化的字段。
- Metrics 按通用 JSON key 比较，不在 Service 中写 Huitun-specific 分支。
- Source Tag 显示 added/removed。
- Older/Same conflict 进入 ignored，不伪装成 Current change。
- `freshness_changes` 只记录 `source_acquired_at` observation 的推进；Canonical `source_updated_at` 仍按 Phase 1B 规则出现在 effective/ignored/historical 语义中，两种时间不得互换。
- Snapshot-only 输入进入 historical_observations。
- Contact 不输出原始值。
- Decimal string 不转换成 float。
- Summary 进入 Plan Hash，Confirm 重新计算。

Phase 1B Snapshot 语义完全保持：

- 同 Metrics + 同 source time：不新增 Snapshot。
- 同 Metrics + 新 source time：新增 Snapshot。
- Newer Metrics：更新 Current 并新增 Snapshot。
- Older Metrics：Current 不覆盖，但保存唯一历史输入。
- source time 为空时仍遵守 Phase 1B fill-only/历史规则。

---

## 16. Provenance 与原始文件保留

完整 lineage：

```text
Influencer / PlatformAccount
→ SourceState / CurrentMetrics / Snapshot / Contact
→ ImportRow
→ ImportJobFile
→ StoredImportFile
→ ImportJob
→ originating CollectionJob
→ optional RefreshQueueItem
```

任何仍被 `ImportJobFile` 引用的 `StoredImportFile` 不得自动物理删除。

现有 `expires_at`：

- 不得被解释为“到期即可破坏 lineage”。
- 只能作为未来归档/复核候选时间。
- MVP 不实现 archive/delete lifecycle。

---

## 17. Schema Delta

### 17.1 0004 所需结构

`import_job_files`：

- id
- import_job_id
- stored_file_id
- position
- original_filename
- declared_mime
- status
- source_acquired_at nullable
- source_acquired_at_origin（server_default/user_confirmed/legacy_unknown）
- source_acquired_at_confirmation_required BOOLEAN NOT NULL DEFAULT false
- detected_fields JSONB
- field_mapping JSONB
- mapping_hash
- raw_rows / warning_rows / error_rows
- error_code / error_message
- parse_task_id / parse_attempts
- parse_started_at / parse_completed_at
- excluded_at nullable
- created_at / updated_at

约束：

- unique(import_job_id, position)
- unique(import_job_id, stored_file_id)
- unique(id, import_job_id)
- position >= 1
- `source_acquired_at_confirmation_required=true` 时，`source_acquired_at` 必须非空且 origin 必须为 `server_default`
- 为 false 时不增加额外组合限制：仍允许冻结的 server_default、user_confirmed 和 legacy_unknown 时间语义

`import_job_file_client_ids`：

- id
- import_job_id
- import_job_file_id
- client_file_id
- created_at / updated_at

约束：

- unique(import_job_id, client_file_id)
- 复合 FK `(import_job_file_id, import_job_id)` → `import_job_files(id, import_job_id)`，由数据库保证 alias occurrence 属于同一 Job
- index(import_job_file_id)
- 一个 ImportJobFile 可有多个 aliases；不同 ImportJob 可重复使用相同 client ID
- alias 表是 authoritative idempotency mapping；`import_job_files` 不保留 competing `client_file_id` 字段

`import_rows`：

- 新增 import_job_file_id
- 删除并替换现有 `uq_import_rows_job_number(import_job_id, row_number)`；旧约束不得保留
- unique(import_job_file_id, row_number)
- 复合 FK `(import_job_file_id, import_job_id)`
- 新增 Freshness 查询索引 `(matched_platform_account_id, committed_at DESC, import_job_id)`

`0004` 的 NOT NULL Row FK 必须与单文件兼容桥同一个 Task 1 交付，不允许只部署 Migration：

- 现有 `POST /import-jobs` 创建新 Legacy single-file Job 时同时创建 position=1 的 ImportJobFile，但不创建或伪造 client-ID alias。
- 该兼容 occurrence 的 `source_acquired_at=NULL`、origin=`legacy_unknown`、`source_acquired_at_confirmation_required=false`，因为旧 API 没有 Draft 期 acquisition 确认；它不得推进 `last_huitun_observed_at`。
- 现有 Parse/Mapping/Preview 路径同步该 occurrence 的最小文件状态/Mapping，新 ImportRow 必须写入该 `import_job_file_id`。
- Phase 1B 单文件全回归必须在 `0004` head 通过，然后 Task 1 才可独立部署/验收。

`import_jobs`：

- 新增 draft 状态
- 新增 failed_stage nullable（preview/confirm）
- legacy 单文件字段 `stored_file_id`、`original_filename`、`mime_type`、`file_size`、`sha256`、`detected_fields`、`field_mapping`、`mapping_hash` 在回填 occurrence 后变为 nullable/deprecated；新 Bulk Job 的文件事实只来自 `/files`
- legacy `parse_task_id` 继续兼容单文件 Job；Bulk 的 per-file parse task 存在 ImportJobFile，Job 级任务用于统一 Preview/Confirm
- 继续只绑定一个 originating CollectionJob

`collection_jobs`：

- 新增 `screening_rules JSONB`，其内 `schema_version=1`
- 新增 `screening_rules_revision INT NOT NULL DEFAULT 1`，并 CHECK `>=1`

Legacy `source_acquired_at` 必须保持 NULL 且 confirmation required=false，不得在 Migration 中用 created_at、mtime 或 filename 回填。

### 17.2 0005 所需结构

- refresh_queues
- refresh_queue_items
- ImportJob 可选 refresh_queue_id
- Queue/Item status enum 与本文件第 13 节状态机
- Queue/Item Department 复合 FK、Account/Influencer 复合 FK、fulfillment/last-return Job+Row 复合 FK 和 all-or-none CHECK
- ImportJob/Queue Department 复合 FK；Operator 保持独立 Audit FK，允许 super_admin 跨部门管理
- unique(queue_id, platform_account_id, source)
- active Item partial unique(department_id, platform_account_id, source)
- Queue status/priority 索引

### 17.3 明确不新增

- ImportBatch
- BatchRow
- FreshnessPolicy
- DailyQuotaPlan
- Influencer.last_observed_at
- Metrics 单字段投影列

---

## 18. Migration Plan

迁移顺序正式冻结：

1. `0004_phase2_bulk_import`
2. `0005_phase2_refresh_queue`

禁止把全部 Phase 2 Schema 塞入一个 Migration，也禁止为空满足阶段编号创建 Migration。

### 18.1 0004

1. 新建 ImportJobFile 与 `import_job_file_client_ids` alias 结构；alias 表使用 Job 内 client ID 唯一约束和 `(import_job_file_id, import_job_id)` 复合 FK，ImportJobFile 本身不保存 `client_file_id`。
2. 预检并安全拒绝仍处于 uploaded/parsing/previewing/confirm_queued/importing 的活动 Legacy Job；部署前先让任务完成或人工处理。
3. 每个历史 ImportJob 回填 position=1 的 occurrence，但不回填任何 alias；`source_acquired_at=NULL`、`source_acquired_at_origin=legacy_unknown`、`source_acquired_at_confirmation_required=false`；completed/preview_ready/preview_stale 映射 ready，mapping_required 映射 mapping_required，failed 映射 failed，cancelled 映射 excluded。
4. 每个历史 ImportRow 关联该 occurrence。
5. 删除 `uq_import_rows_job_number(import_job_id, row_number)`，建立 `unique(import_job_file_id, row_number)`、复合 FK 与索引。
6. 将 Row file FK 变为 non-null。
7. 将明确列出的 legacy ImportJob 单文件列变为 nullable/deprecated，并调整 file_size CHECK 允许 NULL。
8. Legacy acquisition time 保持 NULL。
9. 为历史 CollectionJob 回填空规则 `{"schema_version":1,"platforms":[],"source_tags_exact_any":[]}` 与 `screening_rules_revision=1`。
10. 增加 Draft/File status、failed_stage 与 Batch Audit enum。
11. 同版本交付 Legacy single-file 兼容桥；不得在旧 create/parse/preview 仍会生成无 file FK Row 时单独上线 Migration。
12. `0004` 尚未发布，alias 与 occurrence-level acquisition confirmation Boolean/CHECK 直接完善同一 `0004_phase2_bulk_import`；不得另建 Migration，`0005` 仍专用于 Refresh Queue。

### 18.2 0005

1. 新建 Refresh Queue/Items。
2. 建立 Department、Operator、Account、Source 和 fulfillment 约束。
3. 增加 Queue Audit enum 和索引。

### 18.3 Downgrade

- `0004 → 0003` 只有在“不存在任何 Phase 2 创建的 Bulk Job/新 file occurrence，所有 CollectionJob 仍为 Migration 默认空规则且 revision=1”时允许；只由 Migration 回填的 Legacy 单 occurrence 可安全投回 0003。
- 即使 Bulk Job 只有一个 occurrence，其 Draft/File status、acquisition/origin、per-file Mapping/task metadata 也无法无损回到 0003，必须安全拒绝；不得只按 `file_count > 1` 判断。
- `0005 → 0004` 只有在不存在 Queue/Item 数据且 ImportJob 无 Queue 引用时允许；不得静默删除业务证据。
- PostgreSQL 已使用 Enum 值不得直接破坏。

---

## 19. Frozen API Contract

所有接口继续使用 `/api/v1` 和统一 Envelope。

### 19.1 ImportJob/Bulk

- `POST /import-jobs/bulk`
- `GET /import-jobs`
- `GET /import-jobs/{id}`
- `POST /import-jobs/{id}/files`
- `GET /import-jobs/{id}/files`
- `PATCH /import-jobs/{id}/files/{file_id}`：仅 Draft 修改 source_acquired_at
- `PUT /import-jobs/{id}/files/{file_id}/mapping`
- `POST /import-jobs/{id}/files/{file_id}/replace`
- `POST /import-jobs/{id}/files/{file_id}/exclude`
- `POST /import-jobs/{id}/files/{file_id}/retry`
- `POST /import-jobs/{id}/preview`
- `GET /import-jobs/{id}/rows`
- `POST /import-jobs/{id}/confirm`
- `POST /import-jobs/{id}/retry`
- `POST /import-jobs/{id}/cancel`

现有单文件 `POST /import-jobs` 保留兼容。

`0004` 与单文件兼容桥必须同版本上线：该旧 endpoint 的新 Job 也创建 position=1 的 ImportJobFile，其 acquisition 为 NULL/origin `legacy_unknown`，Parse/Mapping/Preview 同步 occurrence 且新 Row 写入 file FK。这是 Phase 1B 兼容路径，不得用它伪造 observed time；需要 Freshness observation 的新流程使用 Bulk Draft endpoint。

`GET /import-jobs` 使用 `offset=0`、`limit=50`、最大 200，稳定排序 `created_at DESC, id DESC`。

Rows 保留 Phase 1B 的 `offset/limit/action`，并 additive 增加 `category`；`action` 与 `category` 同传返回 422。Rows 使用第 9 节的 file-aware 稳定排序，不得静默混用两套分页或过滤协议。

`PATCH source_acquired_at` 必须写专用 Audit。`replace` 是单文件 multipart；`POST /retry` 只执行第 5 节按 `failed_stage` 冻结的恢复转换。静态 `/bulk` 路由必须先于 `/{id}` 注册。

`POST /import-jobs/{id}/files` 的 `client_file_id` 由 `import_job_file_client_ids` 持久化；同 Job 内 alias 永久绑定 occurrence，且 alias ownership 由复合 FK 保证。API 按第 6.1 节完整真值表处理：不同 client ID 命中相同 SHA 时必须新增 alias，后续该 alias 上传不同 SHA 必须稳定返回 409。Legacy endpoint 不创建伪造 alias。

`0004`/Task 2 阶段的 `POST /import-jobs/bulk` 请求只接受 `collection_job_id`，对 `refresh_queue_id` 按 extra-forbid 返回 422。`0005` 部署后才 additive 接受可选 `refresh_queue_id`，并执行同 Department 与 Queue status 校验。

### 19.2 CollectionJob

- 现有 create/read 增加 `screening_rules` 和 `screening_rules_revision`。
- `PUT /collection-jobs/{id}/screening-rules`

更新请求必须携带 `expected_revision`；初始 revision=1，成功后 `screening_rules_revision + 1`。revision 冲突返回 409，旧 ImportJob Preview 中的 rule snapshot/hash 保留。规则并发变化会让未 Confirm Preview stale，重建 Preview 使用新规则但不能改变已冻结文件 manifest。

### 19.3 Influencer Freshness

扩展现有 `GET /influencers`：

- freshness_status
- requires_refresh
- last_huitun_observed_before/after

响应增加账号级：

- last_huitun_observed_at
- last_huitun_imported_at
- freshness_status
- freshness_age_days

### 19.4 Refresh Queue

- `POST /refresh-queues`
- `GET /refresh-queues`
- `GET /refresh-queues/{id}`
- `GET /refresh-queues/{id}/items`
- `POST /refresh-queues/{id}/export`
- `POST /refresh-queues/{id}/cancel`

Queue list 与 Item list 都使用 `offset=0`、`limit=50`、最大 200。Queue 按 `created_at DESC, id DESC`；Item 只使用创建时冻结字段排序：`priority_tier ASC, baseline_last_observed_at ASC NULLS FIRST, influencer_id ASC, platform_account_id ASC, id ASC`，不读取会随回流改变的 live Freshness。

Queue create 的 `department_id` 可选；缺省为 Session Department。只有 `super_admin` 可以显式指定其他部门，manager/operator 传入其他部门必须 403。目标 Department 只决定 Queue/Item 归属，不把公司级 Influencer 切成部门 ACL；selected Operator 仍只是 Audit 归属。

Export 成功响应是统一 JSON Envelope 的唯一 Phase 2 例外：返回 `text/csv` attachment；错误仍使用统一 Envelope 与 request_id。CSV 必须防公式注入且不含 Contact。

不新增 Influencer POST/PUT/PATCH/DELETE。

---

## 20. Backend / API / Worker 边界

- 唯一业务核心继续是 `packages/backend_core`。
- `apps/api` 只负责 HTTP、依赖注入、参数和响应转换。
- `apps/worker` 只负责 Celery task entrypoint。
- Parser/Adapter/Matcher/Planner/Merge/Freshness/Queue 规则只存在一份。
- 不创建根目录 `services/`。
- 不在 `apps/api` 复制 Service/Repository。
- Huitun 中文列名只存在于 Huitun Adapter/Mapping。
- 通用 Planner、Freshness 和 Queue 不出现 Huitun-specific field branch。

---

## 21. Worker、并发与恢复

当前七服务不增加新服务。

4C/8GB 配置：

- Celery worker concurrency = 2。
- worker prefetch = 1。
- 同时最多一个 Heavy Import Preview/Confirm。
- Heavy gate 使用 PostgreSQL session-level advisory lock 的固定命名 key；任务未取得锁时进入可重试等待，不并发执行。连接/Worker 退出自动释放，并在 finally 主动释放；不得只用进程内 semaphore 或会因 Redis 重启失效的裸锁。
- Import tasks 继续 acks_late + reject_on_worker_lost。
- task payload 只传 Job/File ID 和 revision，数据库是事实源。
- API 状态提交后 Broker dispatch 丢失由 scheduler reconciliation 修复。
- 已由人工 Confirm 的 revision 重投属于恢复，不是 Auto Confirm。

必须移除 2000 行路径中的逐行 N+1：

- 批量加载所有 identity candidates。
- 批量加载 SourceState、CurrentMetrics、SourceIdentity、Contact。
- 批量查询 Snapshot keys 和 Contact duplicate values。
- advisory locks 稳定排序并批量/分块取得。
- 批量持久化 ImportRows 和新实体。

---

## 22. Minimal Web UX

### 22.1 Bulk Import

- 一次选择多个 CSV/XLSX。
- 文件上传并发最多 2。
- 每个文件显示 upload/parse/mapping/ready/failed/excluded。
- Blocking file 未处理时隐藏或禁用 Preview。
- Preview 显示统一 Summary。
- 默认 Tab 为“需要处理”。
- 服务端分页，不渲染全部行。
- Batch ID 存入 URL，刷新后恢复。
- Confirm 明确 revision 和有效写入数量。
- 禁止 Auto Confirm。

### 22.2 Influencer Library

- 增加最近灰豚数据、Freshness、需要刷新筛选。
- 多账号时显示最差状态/待刷新账号数量。
- Legacy imported time 与 observed time 使用不同文案。

### 22.3 Refresh Queue

- 今日总额度与刷新预算。
- Queue 数量和候选摘要。
- Priority reasons。
- 生成、查看、导出。

### 22.4 UI Backlog

- `UI-BACKLOG-001`：粉丝数人类可读，例如 `206572 → 20.66万`，Tooltip/详情保留精确整数；可在 Phase 2 Web Task 顺带实现。
- `UI-BACKLOG-002`：达人库整体视觉和信息层级重构延期。

---

## 23. RBAC、CSRF、Audit 与安全

权限来源始终是 Session `DepartmentPermission.role`，不是所选 Operator role。Operator 只承担 Audit 归属。

| 能力 | super_admin | manager | operator | viewer |
|---|---:|---:|---:|---:|
| 查看 Batch/Preview | 全部门 | 本部门 | 本部门 | 本部门只读 |
| 上传/替换/排除/重试文件 | 是 | 是 | 是 | 否 |
| 生成 Preview / Confirm / Cancel | 是 | 是 | 是 | 否 |
| 查看 Freshness | 是 | 是 | 是 | 是 |
| 创建 Refresh Queue | 是 | 是 | 是 | 否 |
| 查看 Refresh Queue | 全部门 | 本部门 | 本部门 | 本部门只读 |
| 导出 Refresh Queue | 是 | 是 | 是 | 否 |
| 修改 Freshness Policy | N/A | N/A | N/A | N/A（MVP 无表/API） |

规则：

- 所有 Mutation：Session + selected Operator + CSRF + backend RBAC。
- GET 不产生无意义业务 Audit。
- Influencer 继续公司级读取；Owner/Import Department/Queue Department 不是 Influencer ACL。
- Queue 不导出 Contact。
- CSV 防公式注入。
- Audit 不记录原始文件内容、完整行、Contact、Token 或 Secret。
- Export 必须 Audit。

新增 AuditAction 至少覆盖：

- IMPORT_BATCH_CREATED
- IMPORT_BATCH_PREVIEW_CREATED
- IMPORT_BATCH_CONFIRM_REQUESTED
- IMPORT_BATCH_COMPLETED
- IMPORT_BATCH_FAILED
- IMPORT_BATCH_RETRIED
- IMPORT_BATCH_CANCELLED
- IMPORT_FILE_UPLOADED
- IMPORT_FILE_REPLACED
- IMPORT_FILE_EXCLUDED
- IMPORT_FILE_RETRIED
- IMPORT_FILE_MAPPING_UPDATED
- IMPORT_FILE_SOURCE_ACQUIRED_AT_UPDATED
- COLLECTION_SCREENING_RULES_UPDATED
- REFRESH_QUEUE_CREATED
- REFRESH_QUEUE_EXPORTED
- REFRESH_QUEUE_CANCELLED

---

## 24. Failure、Retry 与 Atomicity

| 场景 | 冻结结果 |
|---|---|
| 2000 行中少量格式错误 | Preview Ready；Error 行保留，Confirm 只写有效计划 |
| 文件 Schema 不同 | 每文件 Mapping；Blocking 未解决不得 Preview |
| 文件损坏 | Job 保持 Draft；replace/exclude/retry |
| 同 Batch 相同 SHA | 幂等返回已有 occurrence |
| A+X 后 B+X | 返回同一 occurrence，并持久化 A/B 两个 aliases |
| A+X 后 A+Y | 409 `IDEMPOTENCY_CONFLICT`，原 alias/occurrence 不变 |
| 并发 A+X/A+X | 一个 occurrence、一个 alias；无 500 |
| 并发 A+X/B+X | 一个 occurrence、两个 aliases；无 500 |
| 并发 A+X/A+Y | 一个成功、一个 deterministic 409；无覆盖或分叉 alias |
| 跨 Batch 相同 SHA | 允许、复用 blob、重新 Preview |
| 达人跨文件重复 | 统一 Batch Context，单 owner |
| 同 Email 不同达人 | 不 hard merge，只标 possible duplicate |
| 同昵称不同账号 | 各自保留 |
| Metrics 缺失/非法 | 不伪造、不覆盖合法值，保留 Warning/Error |
| Confirm 重复点击 | 同 revision 幂等 |
| Preview 后 DB 变化 | 整批 preview_stale |
| Worker 崩溃 | 事务回滚，安全重投 |
| Redis/API 重启 | persisted state + reconciler 恢复 |
| Confirm 中途失败 | 整批业务写入回滚 |
| 上传中断 | 临时文件清理；已完成 occurrence 保持 Draft |
| Commit 后 ACK 前崩溃 | 重投读取 completed revision 幂等结束 |
| Job 级任务重试耗尽 | 保存 failed_stage；人工 retry 只回到对应 previewing/confirm_queued 状态 |

Confirm 的 PostgreSQL 事务覆盖所有 included files 的业务写入。禁止逐文件 Commit。

---

## 25. Performance Gates

环境：4 CPU、约 8GB RAM、Compose 七服务、单个 Heavy Import。

### 25.1 MVP Release Blocker — 2000 rows

- 37 列 Huitun-compatible 数据，使用 4×500。
- Parse + Normalize 目标 ≤15s。
- Dedupe + Plan + Preview persistence 目标 ≤45s。
- 上传完成到 Preview Ready P95 ≤60s。
- Confirm P95 ≤60s。
- 单 Worker RSS ≤900MB。
- 无逐行 N+1；SQL 数按 chunk 而非 row 增长。
- 重复测试必须稳定，不只通过一次。

Benchmark 协议固定为：同一数据集 SHA、相同 seed 与隔离 Schema、无其他 Heavy Task；先 1 次 warm-up，再至少 5 次 measured run，保存每次原始耗时/SQL/RSS/CPU，按同一算法计算 P95，不能用单次最好结果代替。

### 25.2 Capacity Gate — 5000 rows

- 必须正确完成、无数据错误、无 OOM。
- 记录时间、SQL 数、RSS、CPU 和锁等待。
- 性能结果用于容量观察，不阻塞 MVP 发布。

### 25.3 Soak Gate — 10000 rows

- 必须保持 correctness 和 no-OOM。
- 记录性能，但时间不作为 MVP 发布 blocker。
- 不得为了极端 10000-row case 牺牲 2000-row MVP 周期。

API 状态 Mutation 应快速持久化并返回 202；行分页目标 P95 ≤500ms。

---

## 26. Phase 2 MVP

MVP 最终交付：

1. 一个 ImportJob 支持多个 CSV/XLSX。
2. 每个原始文件、Mapping、行号、SHA 和 acquisition time 可追溯。
3. 文件内、跨文件、数据库三层去重。
4. 统一 Preview Revision 和大批量 Summary。
5. 单 originating CollectionJob 的确定性三态 Screening。
6. Field-level Change Summary。
7. 人工 Confirm 和现有 Merge。
8. 可靠 Freshness 展示和筛选。
9. Deterministic Refresh Priority。
10. Department-owned Refresh Queue。
11. Queue CSV 导出数据库真实 Identity。
12. 回流后的 CurrentMetrics、Snapshot 和 Queue fulfillment。
13. Worker crash/retry/reconciliation。
14. 2000-row 发布 Gate。
15. 最小功能性 Web。

---

## 27. Phase 2 Full / Future

- Multiple CollectionJob Matching
- 持久化 CollectionJob Account Match relation
- Batch normalization reuse across Jobs
- DailyQuotaPlan
- Dynamic/weighted Refresh Priority
- Versioned Company Freshness Policy
- Growth 计算与 richer Analytics
- Refresh scheduling
- 批量业务操作
- 官方 Huitun API Connector
- 其他平台 Adapter
- UI overhaul
- Playbook、AI、Campaign、Email、Inbox、CRM 等未来阶段能力

这些项目不属于 MVP，也不因本文获得实现授权。

---

## 28. Task 顺序与完成标准

| Task | Scope | Schema/API/Worker/Web 影响 | 关键测试与完成标准 | 依赖 | 风险 |
|---|---|---|---|---|---|
| 0 Design Freeze | 本文及根文档同步 | Docs only | 无冲突、唯一 Phase 2 UNKNOWN、独立 commit | 无 | 高 |
| 1 Bulk Domain | ImportJobFile、client-ID alias、acquisition confirmation Boolean/CHECK、Row FK、Draft/File status、Legacy single-file 兼容桥 | `0004`；无 Web | 历史 backfill、alias 复合 FK/unique、confirmation 合法/非法组合、fresh/repeat/check；Legacy 无伪造 alias 且 confirmation=false；`0004` head 的 Phase 1B 单文件回归 | 0 | 高 |
| 2 Multi-file Upload | Draft、单文件上传、replace/exclude/retry | API/Service；文件 Worker 入口 | 完整 SHA/client-ID 真值表、A+X/A+X、A+X/B+X、A+X/A+Y 并发、断网、损坏、RBAC | 1 | 中 |
| 3 Parse/Normalize/Dedup | 每文件 Mapping、统一 Batch Context | Processor/Planner/Worker | 4×500、跨文件 hard duplicate、Email 边界 | 2 | 高 |
| 4 Bulk Repository | 批量预取和写入 | Repository/Planner/Processor | SQL query-count、2k 性能 | 3 | 最高 |
| 5 Unified Preview | Summary、分页、Change、Screening | API/Worker | 统计不变量、Stale、三态 Screening | 3–4 | 高 |
| 6 Atomic Confirm | 单事务、幂等、reconciler | API/Worker/Scheduler | 双击、Worker kill、Redis/API restart | 5 | 最高 |
| 7 Freshness | 时间、状态、Influencer GET | Settings/API；索引 | 边界、多账号、Legacy unknown、GET 无 Audit | 1、6 | 中 |
| 8 Refresh Queue | Queue/Items、Priority、CSV | `0005`、API/Worker | 稳定 selection、RBAC、Audit、CSV 安全 | 7 | 中 |
| 9 Refresh Return | Queue-linked Import、fulfillment | Processor/Service | changed/no_change/stale/error 幂等 | 6、8 | 高 |
| 10 Functional Web | Bulk、Preview、Freshness、Queue | Web only against frozen API | 多文件、分页、恢复、Viewer、无 Auto Confirm | 5–9 | 中 |
| 11 PG/Performance | 真实 PG、并发、2k/5k/10k | Tests/必要修复 | 2000 release、5000 capacity、10000 soak | 1–10 | 高 |
| 12 Docker E2E/Docs | 七服务、Migration、持久化 | Compose/Docs | 完整回流、重启、原文件/Queue 持久化 | 11 | 高 |
| 13 Server Deploy | 备份、迁移、内部测试部署 | Server | 真实 E2E、无公网端口、回滚演练 | 12 + 单独授权 | 高 |

每个 Task 必须独立人工验收，不能自动进入下一 Task。

---

## 29. Test Matrix

| 层级 | 必测内容 |
|---|---|
| Unit | 三层去重、owner、Summary、Change、Freshness、Priority、CSV 安全 |
| Schema | extra-forbid、Revision、时间、分页、limit、重复参数 |
| Repository | Scope、稳定排序、批量预取、Freshness、Queue |
| Service | Viewer Mutation/Export 拒绝、公司级读取、Operator 必选、CSRF、幂等、Stale |
| API | Envelope、Request ID、401/403/409/413/422/503、OpenAPI |
| Worker | route、ID-only payload、acks_late、retry、reconcile |
| PostgreSQL | alias 复合 FK/unique、Row FK/unique、JSONB/locks/单事务 Confirm |
| Concurrency | A+X/A+X、A+X/B+X、A+X/A+Y、双 Confirm、两个 Batch 同 identity、Queue 双建 |
| Multi-file | 4×500、CSV+XLSX、不同 Header、坏文件、跨文件重复 |
| Duplicate | 同 SHA、同/跨文件 Row、同 Email、同昵称 |
| Freshness | acquisition/import/source time、阈值、Legacy unknown |
| Return | changed/no_change/older/error/manual_review |
| Migration | 0003→0004→0005、真实数据副本、repeat/check/downgrade guard |
| Docker | Worker=2、Heavy=1、共享 volume、重启持久化 |
| E2E | Bulk→Preview→Confirm→Library→Queue→CSV→Return |
| Security | 上传攻击、CSV formula、Viewer export denied、Audit 无敏感值 |
| Performance | 2k release、5k capacity、10k soak/no-OOM |

Gate 数据：

- 2000-row synthetic Huitun-compatible，4×500，覆盖 existing/new/changed/no_change/warning/error/duplicate。
- 5000、10000 容量数据。
- 真实 Huitun export 继续由仓库外路径和 SHA 验证，禁止进入 Git。

---

## 30. 回滚与数据安全

- 所有 Schema 变更必须有 Alembic Revision 和部署前备份。
- 0004/0005 先在含真实 Phase 1B 数据的隔离 PostgreSQL 验证。
- Confirm 失败不允许留下部分 Influencer/Metric/Contact 写入。
- 不使用 destructive migration 静默删除 legacy 文件字段或 lineage。
- 原始 blob、ImportRows、Snapshots、Queue fulfillment 作为审计证据，默认 RESTRICT/保留。
- MVP 不删除任何被 lineage 引用的 blob，磁盘只增不减；Task 12 必须记录容量、告警阈值与备份影响，但不能以自动清理破坏来源链。
- Task 13 前必须提供 migration、backup、restore 和 rollback runbook。

---

## 31. 已关闭决策记录

以下均已人工确认，不再作为 blocker：

1. ImportJob 是多文件 Batch aggregate。
2. 不建 ImportBatch/BatchRow。
3. 同 Job 相同 SHA 幂等复用 occurrence；跨 Job 重新 Preview。
4. 坏文件/Mapping failure 保持 Draft，Preview 前可 replace/exclude/retry。
5. MVP 单 originating CollectionJob。
6. Structured screening only，无自由文本/AI 推断。
7. 新增 per-file source_acquired_at，Preview 后冻结。
8. Freshness 阈值 7/30/90，使用 Settings，无 Policy 表。
9. Refresh Queue 部门级、Influencer 候选公司级。
10. NO_CHANGE 仅在非空 acquisition 严格晚于非空 baseline 且 `source_acquired_at_confirmation_required=false` 时 fulfill；baseline 为空或 confirmation required=true 时 unresolved。
11. 被 lineage 引用的原始文件不得物理删除。
12. Daily quota 只是 Queue 参数。
13. Migration 拆分为 0004 和 0005。
14. Worker concurrency=2，Heavy Import≤1。
15. 2000 为发布 blocker；5000 capacity；10000 soak 非性能 blocker。
16. Same-time Metrics 完全保持 Phase 1B。
17. 粉丝人类化可随 Web Task 完成；视觉重构延期。
18. client-ID alias 表是 Job 范围幂等的唯一权威事实源；一个 occurrence 可有多个 aliases，Legacy occurrence 不伪造 alias，直接完善尚未发布的 0004。
19. `source_acquired_at_confirmation_required` 是历史 SHA acquisition 待确认状态的唯一 occurrence-level 持久化事实源；历史 SHA + server-default 为 true，普通首次上传、显式时间、PATCH 确认与 Legacy 均为 false，并由 0004 CHECK 约束 true 的合法组合。

---

## 32. Remaining UNKNOWN

唯一与当前 Phase 2 相关的外部 UNKNOWN：

`PHASE2-UNKNOWN-001 — UNKNOWN / NEED USER VERIFICATION`

尚未通过真实灰豚产品流程验证，灰豚是否支持使用以下一种或多种字段进行批量定位、导入或定向导出：

- platform_user_id
- profile_url
- handle
- nickname
- external_source_id

冻结影响：

- 不阻塞 Task 1–7 的 Bulk Import、Screening 和 Freshness。
- 不阻塞系统创建 Queue 和导出数据库真实 Identity。
- 阻塞“Refresh Queue CSV 可以被灰豚直接批量消费并完成定向刷新”这一外部能力的承诺与验收。
- 未验证前不得虚构灰豚支持任何一种格式。

---

## 33. Task 0 验收清单

- [x] 旧 Browser Automation 方案明确废弃。
- [x] 旧 “Phase 2 = SOP/Campaign/AI” 排期明确被覆盖。
- [x] 17 项人工决定均进入权威文档。
- [x] ImportJob/ImportJobFile/ImportRow 聚合和 provenance 已冻结。
- [x] Screening、Freshness、Queue、Return、Snapshot 语义已冻结。
- [x] 0004/0005 边界已冻结。
- [x] Worker 并发和 2k/5k/10k Gate 已冻结。
- [x] MVP、Full、Out of Scope、Task 0–13 已区分。
- [x] 只保留一个 Phase 2 相关 UNKNOWN。
- [ ] 业务代码实现。
- [ ] Migration 创建。
- [ ] API/Worker/Web 实现。
- [ ] Phase 2 运行门禁。

最终状态：**Phase 2 Task 0 Design Freeze 完成并不代表 Phase 2 已实现。只有独立人工授权后才可进入 Task 1，且不得自动开始后续 Task。**

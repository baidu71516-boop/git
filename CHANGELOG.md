# Changelog

本文档记录达人智能触达系统的可交付变更。

## [Unreleased]

### UI-6 — Collection Screening Rule Editor

#### Added

- “数据采集 → 当前采集任务”新增真实筛选规则摘要与 600px 查看/编辑 Modal，展示小红书平台、来源标签 exact-any、可空粉丝范围和规则 revision；Viewer 保持只读。
- 保存使用真实 `PUT /collection-jobs/{id}/screening-rules` 完整 CAS body，并沿用 Session、selected Operator、CSRF 与 Department scope；409 revision conflict 只允许用户手动重新加载最新规则，不自动重试或覆盖。
- `preview_ready` 保存前显示预览失效提示；成功后只 invalidate/refetch Collection Job 与当前 Import Job，继续复用后端 `preview_stale` UI，不自动重建 Preview 或 Confirm。

#### Verification

- Web 189 tests、Prettier、ESLint、TypeScript、Next production build 与 `git diff --check` 通过。

### Phase 2 Task 9 — Refresh Return Reconciliation

#### Added

- `POST /import-jobs/bulk` additive 接受可选 `refresh_queue_id`，只允许同 Department 的 open/exported Queue；Import Job/detail/OpenAPI 回传该链接，普通 Bulk 请求与 Audit shape 保持不变。
- 新增纯 `refresh.reconciliation` domain：只消费现有 Hard Matcher 已确定的 account/source 与 owner/effective plan facts，不接受 Queue identity snapshot；统一生成 matched/outside/pending、Missing Return、changed/no-change/stale/unresolved、冲突 claimant 与安全 Preview summary。
- Unified Preview 把 Queue ID 绑定进 linked context hash，在 `preview_summary.refresh_return` 与每行 `merge_plan.refresh_return` 冻结 expected evidence；Preview 不修改 Queue，普通 Bulk 不产生 additive key。
- Atomic Confirm 在现有 Merge finalize 后、唯一 commit 前写 Item fulfillment/Last Return lineage 并聚合 Queue completion；completed result replay 直接复用持久结果，不重复核销。
- 新增 `make test-refresh-return-postgres`，覆盖原子回滚/recovery/replay、同 Item 并发唯一赢家、Freshness、confirmation-required unresolved 与真实 2000-item mixed 回流。

#### Reliability and Boundaries

- Queue Item 只能在 Phase 1B Hard Matcher 唯一确定 owner/effective PlatformAccount 后按 `(queue_id, platform_account_id, source)` 关联；repository projection 不读取 `identity_snapshot`，未修改 Hard Matcher。
- 无法唯一确定账号的 ERROR/MANUAL_REVIEW、所有 SKIP/duplicate non-owner 与 Missing Return 保持 pending；只有已唯一匹配后的确定性问题可写 Last Return 并进入 unresolved。
- acquisition 必须严格晚于非空 baseline 才能 no-change fulfill；旧 observation 为 stale_return，confirmation-required/acquisition-null/baseline-null no-change 为 unresolved。Freshness 仍只从 completed Confirm lineage 推导。
- Queue/Items 使用 PostgreSQL row locks 与 frozen plan comparison；Merge、Row lineage、fulfillment、Queue completion、Job/task/Audit 同事务。没有 Redis 最终一致性依赖、新 Audit enum、Migration、Web、Task 10 或部署。

#### Verification

- Task 9 PostgreSQL 16 Gate 为 5 passed；2000 Queue Items/1705 return rows 最终为 800 fulfilled_changed、600 fulfilled_no_change、300 stale_return、300 pending，367 SQL、13.842815s、RSS high-water 322,420,736 bytes。
- 普通 Task 6 Confirm 的 2000-row measured P95 为 6.711925s/55 SQL/RSS 346,669,056 bytes；5000 Confirm 为 16.103438s/106 SQL/RSS 651,329,536 bytes；Task 4 与 Task 5 的 5k/10k opt-in gates 也已实际复跑通过。
- 显式 PostgreSQL 16 的 `make test` 通过：Backend/Integration/Smoke 558 passed、6 skipped（Task 9 与既有 capacity opt-ins 已独立实跑，仓库外真实附件未提供）；API 27、Worker 20、Web 29 passed。
- `make lint`、`make compose-validate`、Task 7/8 PG targets 与 Alembic fresh/current/check 全部通过；唯一 head 仍为 `0005_phase2_refresh_queue`。

### Phase 2 Task 8 — Refresh Queue / Deterministic Export

#### Added

- 新增唯一 Migration `0005_phase2_refresh_queue`：Department-owned Queue、account/source Item、状态/时间/limit CHECK、同部门 active candidate partial unique、Queue/Account/Import 复合 FK、ImportJob nullable Queue FK 与 CREATED/EXPORTED/CANCELLED Audit enum；有 Queue/Item/Import reference 时危险 downgrade 安全拒绝。
- 新增 `backend_core.refresh` domain/repository/service：复用 Task 7 Freshness policy 与 Huitun evidence projection，按 account+source 生成整数 tier 1–5、稳定 reason codes、closed criteria/identity snapshot、同一个 UTC `as_of` 和稳定排序。
- 新增六个 `/api/v1/refresh-queues` endpoint：create/list/detail/paginated items/export/cancel；成功 CSV export 是 raw attachment，其余保持统一 Envelope。
- 新增固定字段 CSV exporter，防 `= + - @ TAB CR` 公式注入并使用标准 quoting；只导出冻结的公开 Identity 和 strict followers scalar，不含 Contact、raw row 或完整 Metrics。
- 新增 `make test-refresh-queue-postgres`，覆盖 0005 Migration、候选/priority/identity、同部门并发补位、跨部门复用、事务回滚、Freshness writer race 与 2000-item Service Gate。

#### Reliability and Boundaries

- 创建先获取 Department transaction advisory lock，再按 Department+Account+Source 稳定锁候选并重选补位；数据库 partial unique 是最终 backstop。Queue、Items 与 Audit 同事务提交，不使用 Redis 一致性锁。
- Huitun eligibility 只能来自 SourceState、SourceIdentity 或成功 Huitun Confirm lineage；账号初始 source 不构成证据。Identity 使用 SQL/Python 共用的完整 Unicode whitespace 定义，多个非空 external ID 稳定选字典序最小值。
- `baseline_last_observed_at` 与 Huitun SourceState `baseline_source_updated_at` 从同一 candidate statement 冻结；重复导出只读取 Item snapshot，不因 live name、followers 或 Freshness 改变。
- Mutation 强制 Session、selected Operator、CSRF、Service RBAC 与最小 Audit；Viewer 只读，普通角色本部门 scope，super_admin 可指定 active target Department。GET 为 0 business Audit。
- Task 8 只建立未来 fulfillment 所需 nullable Schema；没有开放 Queue-linked Import payload，也没有实现 Refresh Return、自动核销、Web、Browser Automation、灰豚登录或部署。

#### Verification

- Task 8 PostgreSQL 16 Gate 为 13 passed；2000-item 真实 Service create 为 16 SQL、4 DML、2 advisory-lock SQL、1.061177s，RSS high-water 145,342,464 bytes。
- 所有既有显式 opt-in PG gates 实跑为 14 passed：Task 6 2000 Confirm measured P95=6.275921s/55 SQL/RSS 349,388,800 bytes，5000 Confirm=16.508568s/106 SQL/RSS 640,516,096 bytes；Task 4 5k/10k prefetch 与 Task 5 5k/10k Preview correctness/no-OOM 通过。
- 显式 PostgreSQL 16 的最终 `make test` 通过：Backend/Integration/Smoke 526 passed、5 skipped（4 个 opt-in 已在独立命令实跑，仓库外真实附件未提供）；API 26、Worker 20、Web 29 passed。
- `make lint`、`make compose-validate` 与真实 PostgreSQL 16 Alembic fresh/current/check 通过；唯一 head 为 `0005_phase2_refresh_queue`。

### Phase 2 Task 7 — Influencer Freshness Domain

#### Added

- 新增 validated Freshness Settings 与唯一纯 domain policy：按 timezone-aware UTC elapsed duration 计算 `unknown/fresh/aging/stale/very_stale`、整日 age 和 account/influencer `requires_refresh`，精确覆盖 7/30/90 天及最小微秒边界。
- 扩展现有 Influencer list/detail DTO 与 typed OpenAPI success envelope：每个 eligible active Huitun PlatformAccount 返回独立 `last_huitun_observed_at`、`last_huitun_imported_at`、Freshness status/age/refresh decision；达人级采用最差账号汇总，零 eligible account 为 unknown 但不可刷新。
- 扩展现有 `GET /api/v1/influencers`，增加 `freshness_status`、`requires_refresh`、`last_huitun_observed_before/after` 四个严格单值筛选；支持时区校验、inclusive range、unknown null semantics、重复参数 422，并与既有筛选和稳定分页组合。
- 新增显式 `make test-freshness-postgres` Gate，覆盖真实 Confirm lineage、Legacy fallback、source isolation、inactive/multi-account、same/older Metrics observation、query count、2k scale 与 EXPLAIN。

#### Reliability and Boundaries

- `last_huitun_observed_at` 只从 completed Huitun Confirm 的成功 committed row 与 ready included occurrence 的可靠 acquisition time 推导；`committed_at` 仅形成独立 imported fallback，Legacy 不伪造 observation。
- Huitun eligibility 必须由 Huitun SourceState、SourceIdentity 或成功 Huitun Import lineage 证明；PlatformAccount 初始 `source` 字段和其他来源 observation 都不能刷新 Huitun Freshness。
- list 使用集合聚合与当前页 batch hydration，无逐达人 lineage N+1；GET 保持公司级读取、四角色 RBAC、Viewer Contact masking 和 0 business Audit/DML。
- 本 Task 不创建 Migration、FreshnessPolicy 表或冗余列；Alembic 保持 `0004_phase2_bulk_import`，未开始 Task 8、`0005`、Refresh Queue/Return、Web 或部署。

#### Verification

- Freshness PostgreSQL 16 Gate 为 3 passed：50/500/2000 Influencer 均固定 6 SQL（5 SELECT、0 DML），本轮 wall 分别为 0.012543s/0.016759s/0.019685s；detail 固定 7 SQL。2000 规模的全部既有筛选 + Freshness 组合路径仍为 6 SQL、0 DML、wall=0.079464s；生产 count statement EXPLAIN 使用现有 `ix_import_rows_job_action`，execution=28.336ms，扫描 2000 行且未移除行，并核验 frozen `ix_import_rows_account_committed_job` metadata。
- 显式 PostgreSQL 16 的全仓 `make test` 通过：Backend/Integration/Smoke 420 passed、API 24、Worker 20、Web 29；仅仓库外真实附件与明确的 5k/10k opt-in capacity benchmark 按设计跳过。
- `make lint`、`make compose-validate`、Alembic unique head/current/check 均通过；真实 PG16 `alembic check` 返回 no new upgrade operations。

### Phase 2 Task 6 — Atomic Bulk Confirm / Revalidation / Recovery

#### Added

- 完善尚未发布的 `0004_phase2_bulk_import`，新增 durable `import_task_requests`：四类 task target/state CHECK、UUID token、四个 active partial unique、dispatch/run attempts、retry time、generation/lease 所需时间戳、复合 Job/File FK 和 reconciliation indexes；有 durable task record 时危险 `0004 → 0003` downgrade 安全拒绝，未创建 `0005`。
- 新增统一 `UnifiedPlanBuilder`，让 Preview 与 Confirm 共用 manifest、Storage SHA、Mapping/acquisition、Canonical normalize、duplicate owner、identity locks、Matcher/Planner、Screening、Change Summary、row plan hash 与 batch summary 的唯一算法；Confirm 使用 non-pruning revalidation，避免重算时删除 revision 已绑定的持久 Row。
- 新增 Atomic Bulk Confirm processor：精确绑定 Job/revision/task token，重建并逐项比较完整 Plan；任一变化整批 `preview_stale`，一致时在一个 PostgreSQL transaction 内完成所有 included files 的 Merge、ImportRow lineage、Job result、task completed 与成功 Audit。
- 抽取 Legacy/Bulk 共用的 `ImportMergeApplier`，保持 Phase 1B Hard Match、non-destructive update、manual Contact protection、SourceIdentity/SourceState、CurrentMetrics 和 immutable MetricSnapshot 唯一写入语义；`MANUAL_REVIEW/ERROR/SKIP` 只提交 lineage，不自动 Merge。
- API/Worker/Scheduler 接入 PostgreSQL task lifecycle：DB-before-publish reservation、ID-only Broker payload、exact claim、持久 run generation/lease/heartbeat、bounded retry/exhaustion、completed replay 和 deterministic `FOR UPDATE SKIP LOCKED` reconciliation。

#### Security and Reliability

- Confirm 双击和 completed replay 返回同一 revision 的已持久 token；不同 revision、manifest/SHA/Mapping/acquisition/Screening/normalized row、owner、identity/current-state、plan hash 或 summary 变化均拒绝旧 Preview，不允许“重算后顺便确认”。
- Heavy Preview/Confirm 在 claim 前使用同一 PostgreSQL session advisory-lock family 串行化；锁竞争不消耗 run attempt，New Identity 与 Existing Account 并发路径通过稳定 identity/account locks 防重复提交和 lost update。
- Broker publish 失败保留 PostgreSQL reservation；Scheduler 在 publish 前提交新的 reservation，Worker crash/lease expiry、Redis/API restart 和 business commit 后 Broker ACK 前的 gap 都从持久 task state 恢复，不依赖 Celery retry count、Audit 或进程内状态。
- Cancel 只允许 requested/retry_wait task 与 Job 同事务取消；running task 返回 409 `IMPORT_TASK_RUNNING`，避免已开始的数据库 transaction 与 cancelled Job 分叉。Audit/日志不记录 raw row、Contact、Storage path、secret 或完整 plan；task token 仅作为必要 task identity 白名单字段使用。

#### Verification

- PostgreSQL 16 Atomic Confirm + durable recovery 组合 Gate 为 25 passed：覆盖 exact frozen payload/Mapping/Screening/SHA stale、completed replay、Row 1999 rollback→lease recovery→generation 2 完成、同/跨 identity-basis 并发、Existing Account 防 lost update、heartbeat/generation/DB-clock fencing、`SKIP LOCKED`、NOWAIT lock inversion rollback 与 cancel race。
- 2000-row mixed correctness run 为 76 SQL（55 SELECT、21 DML、7 advisory lock）、4.005935s、RSS high-water 309,641,216 bytes；最终为 1998 Influencer/Account、1 SourceIdentity、1996 SourceState、2 Contact、1995 CurrentMetrics/Snapshot，逐行 committed lineage 与 Preview 一致。
- 正式 37 列灰豚兼容 all-new 性能数据使用 1 次预热 + 5 次测量：wall P95=4.483997s、CPU P95=3.918085s、55 SQL、RSS high-water P95=343,638,016 bytes；5000-row capacity 为 11.811850s、106 SQL、RSS high-water 673,726,464 bytes。mixed new/existing/changed/no-change/error/manual/duplicate 正确性由独立 2000-row Gate 覆盖。
- 显式 PostgreSQL 16 的最终 `make test` 通过：Backend/Integration/Smoke 368 passed（仅真实附件与已另行实跑的 opt-in 默认跳过）、API 24、Worker 20（含独立 heartbeat 真实 PG）、Web 29；Task 4/5 的 5k/10k opt-in capacity 也实际通过。`make lint`、`make compose-validate`、Alembic head/current/check 均通过，head 保持 `0004_phase2_bulk_import`。
- 最终隔离 Compose v4 使用与宿主 94 个生产/迁移源文件完全一致的镜像（pre/post/run digest `c144764375dd9830c22314f41c05671178f5cdf212b697f153b24ec253e17f1c`），在 PostgreSQL 16.14 / Redis 7.4.10 上验证 Redis 停止时持久 Confirm request、真实 API 进程重启、Redis 恢复后 Beat reconciliation 和 Worker completion：同 token 从 requested/dispatch1/run0 收敛为 completed/dispatch2/run1，业务实体与 lineage 各一次，容器内 `alembic check` 无 drift；隔离容器、网络、卷、镜像与临时文件已清理。

### Phase 2 Task 2 — Multi-file Upload & Storage

#### Added

- 新增 Bulk Draft 与四个文件接口：创建 `ImportJob` Draft、逐文件上传、文件列表、Preview 前 acquisition time 修正和 Draft 文件排除；一次请求只接收一个 CSV/XLSX，不启动多文件 Parse、Preview 或 Confirm。
- 同一 Job 通过持久 client-ID alias 和 SHA 约束实现完整幂等真值；不同 Job 可复用同一 `StoredImportFile` blob，但各自创建独立 occurrence，且不复用历史 Row、Mapping 或 Preview。
- 新 occurrence 记录稳定 position、来源取得时间及 occurrence-level acquisition confirmation 状态；历史 SHA 的 server-default 时间必须人工确认，显式时间和 PATCH 确认保持可审计。
- 新增 validated Settings：每 Batch 最多 20 个 occurrence、累计 100 MiB，source acquisition 允许最多 5 分钟时钟偏差；单文件仍沿用 25 MiB 和 Phase 1B 安全解析限制。

#### Security and Reliability

- Bulk Mutation 继续强制 Session、selected Operator、CSRF、后端 RBAC 和 Department scope；Viewer 只读，新嵌套资源对不可见 scope 返回 404，API 不返回 Storage Key 或服务器路径。
- 上传在消费文件流前完成 Job/scope/Draft 预检，写入前再次锁定 Job；PostgreSQL Job 行锁与数据库 unique/FK 约束共同保证并发 position、SHA occurrence 和 alias 收敛。
- 原子存储补偿覆盖上传中断、验证失败、批级限制、数据库失败和 rename 后异常；排除 occurrence 不删除 blob。提交结果不确定时使用 shielded commit 和独立事务核验，优先保留可能已被 lineage 引用的 canonical blob，所有异常路径执行 rollback。
- 空文件和损坏但受支持的 CSV/XLSX 返回 422，扩展名/MIME mismatch 返回 415，大小超限返回 413；完整错误使用统一 request-ID envelope，OpenAPI 同时描述首次上传 201 与幂等上传 200。

#### Verification

- `make test` 通过：backend_core/integration/smoke 159 passed、5 个外部门控按设计跳过；API 15 passed；Worker 5 passed；Web 29 passed。
- PostgreSQL 16 显式门禁 24 passed，覆盖最终 `0004` Migration、client-ID/SHA/position 并发、Phase 1B Confirm 并发和达人查询；仓库外真实 50 行灰豚附件回归另 1 passed。
- `make lint` 覆盖 Ruff、Black、backend_core/API/Worker mypy、ESLint、TypeScript 和 Prettier；Alembic head 保持 `0004_phase2_bulk_import`，未创建 `0005` 或其他 Migration。

### Phase 2 Task 0 — Design Freeze

#### Documentation

- 冻结新的 Phase 2 方向：多文件 Bulk Import、versioned structured screening、账号/来源级 Freshness、Department-owned Refresh Queue 与人工回流；旧 Browser Automation 方案正式废弃。
- 冻结一个 `ImportJob = 一个 Bulk Batch`，通过 `ImportJobFile` 保存多文件 occurrence 与行级 lineage；明确不新增 `ImportBatch`、`BatchRow`、FreshnessPolicy 或 DailyQuotaPlan。
- 冻结同 Job 相同 SHA 幂等、跨 Job 重新 Parse/Preview、坏文件保持 Draft、统一 Preview Revision、人工 Confirm、Phase 1B Matcher/Merge/Metric Snapshot 复用和 Email 仅疑似重复规则。
- 冻结 `source_acquired_at`、7/30/90 天 Freshness Settings、NO_CHANGE Queue 核销条件、被 lineage 引用的 Raw File 不得物理删除，以及灰豚批量定位能力的唯一 Remaining UNKNOWN。
- 冻结 Migration 拆分为 `0004_phase2_bulk_import` 与 `0005_phase2_refresh_queue`，Worker concurrency=2、Heavy Import 同时最多 1 个，以及 2000/5000/10000 行分级门禁。
- 同步 Product、Architecture、Database、API、Development Plan、Acceptance Criteria、Open Questions 与工程边界文档，明确 AI、Playbook、Campaign、邮件、CRM、其他平台 Connector 和视觉重构均不属于当前 MVP。

#### Boundaries

- 本提交仅包含 Markdown 设计文档；没有业务代码、ORM Model、API/Worker/Web 实现、Docker 配置或 Migration 变更。
- `0004`/`0005` 只是冻结的后续命名，本 Task 不创建 Migration，也不开始 Task 1。

### Phase 1C

#### Added

- 在唯一业务核心 `packages/backend_core` 中新增达人库只读 Schema、Repository 和 Service，复用 Phase 1B 的 Influencer、PlatformAccount、Source、Contact、Current Metrics 与不可变 Metric Snapshot 数据模型。
- 新增四个只读接口：达人列表、筛选选项、达人详情和指标历史；支持一行一个 Influencer 的稳定分页、昵称搜索，以及赛道、粉丝范围、负责人和 CRM Stage 筛选。
- 新增 Web 达人列表 `/influencers` 与详情 `/influencers/{id}`，展示平台账号、完整来源追溯、真实当前指标、联系方式和分页历史快照，不创建 AI、触达或 CRM 占位模块。

#### Security and Boundaries

- 达人库为公司级共享；四个 GET 只要求有效 Session，不按 Owner 或 Import Department 分片，也不要求已选择 Operator。权限仍取自 `DepartmentPermission`，所选 Operator 不会提升 Session 权限。
- `super_admin`、`manager`、`operator` 可读取完整 current Contact；`viewer` 仅收到固定脱敏值 `***`。Contact 原文和 `normalized_value` 不进入日志、console、埋点、错误响应或 Audit。
- 列表和详情只返回 active 且未软删除的 Influencer；Phase 1C 没有 Influencer Mutation、CSRF 写流程、新 AuditAction、Schema 变更或 `0004` migration，CRM Stage 仅展示和筛选。

#### Verification

- `make lint` 全部通过：Ruff、Black（103 个文件）、backend_core/API/Worker mypy、ESLint、TypeScript 与 Prettier 均通过。
- `make test` 全部通过：backend_core/integration/smoke 为 133 passed、3 个外部门控按设计跳过；API 9 passed；Worker 5 passed；Web 28 passed。3 个外部门控已分别显式执行：PostgreSQL 16 Repository/Import 9 passed，仓库外真实灰豚附件 1 passed。
- PostgreSQL 16 验证了 JSONB Followers 安全类型处理、同一指标行范围语义、fan-out 去重、Tag、ILIKE wildcard、稳定排序和 Snapshot 历史；现有 `0001` 至 `0003_phase1b` 通过 fresh、repeat、downgrade/re-upgrade、完整 base 循环、metadata 与 `alembic check`，且未创建 `0004`。
- classic Docker builder 完成最新镜像构建；默认栈与独立隔离栈的七服务均健康，Celery 仍只注册既有 Import 与 Phase 0 任务，PostgreSQL/Redis 无宿主公开端口。
- 真实 Nginx/API/浏览器 E2E 通过导入、Preview、Confirm、列表、全部冻结筛选、详情、Viewer 无 Operator 读取与 Contact 脱敏；后续 Newer/Same/Older/Unknown 导入证明 Current Metrics、人工 Owner/Contact 和不可变 Snapshot 的非破坏性规则保持正确。
- PostgreSQL 数据与 Import Storage Object 在七服务重启后保持相同计数和 SHA-256。Docker buildx 缺失继续作为正式部署前事项，不阻塞 Phase 1C。

### Phase 1B

#### Added

- 新增 `0003_phase1b` migration，以及 Collection Job、Stored Import File、Import Job/Row、公司级 Influencer、PlatformAccount、Source Identity/State、Contact、Current Metrics 与不可变 Metric Snapshot。
- 新增安全 CSV/XLSX Storage 与 Parser、37 列灰豚 CSV/Excel Adapter、显式 Generic Mapping 和平台无关 Canonical Contract。
- 新增持久化 Preview Plan、递增 Revision、稳定 Plan Hash、非破坏性 Freshness、公司级硬身份去重、Email 疑似重复和原子异步 Confirm。
- 新增 Collection/Import API、Celery `import` 队列入口，以及创建任务、上传、Mapping、Preview、行级详情、Confirm、Stale 重建与结果 UI。
- 新增仓库外真实附件门控测试和 PostgreSQL 并发门控测试；真实文件与敏感行不进入仓库或测试输出。

#### Security

- 文件默认硬限制 25 MiB；交叉校验扩展名、MIME、magic/content 与 Parser，并限制行、列、Cell、XLSX entry、解压体积和压缩比。
- 拒绝宏、ActiveX、外部关系、加密/伪装 Office 文件、路径穿越、ZIP bomb、危险 XML 与公式 Header；数据公式不执行并产生行级 Warning。
- `/data/imports` 使用随机 Storage Key、`0700` 目录、`0600` 文件、原子写入、SHA-256 与读取完整性复核；Nginx 预留 multipart 开销，应用仍执行最终文件上限。
- 所有 Mutation 强制认证、已选择 Operator、CSRF 与后端 RBAC；Viewer 只读，普通角色限部门，Super Admin 可跨部门且 Operator 不改变 Session 权限。
- Audit 与异常响应不记录密码、Token、Secret 或 Contact 原文；Worker 未知异常转换为稳定错误，避免数据库参数进入任务日志。

#### Fixed

- FormData 请求不再错误设置 JSON Content-Type，浏览器可生成正确 multipart boundary；非 JSON 的 413 网关响应会映射为稳定文件超限错误。
- Database 在实例化时延迟注册完整 ORM graph，确保独立 Celery Worker 写跨域 Audit 时可解析 Department/Operator 外键，同时避免 `bootstrap-admin` 循环导入。
- 重复看到同源 Contact 时保留业务 `NO_CHANGE`，不重复创建，只更新 last seen/import 审计来源；manual Contact 仍不可变。
- Prettier 忽略 `.next` 等生成目录，生产构建后再次执行 `make lint` 不再误报生成物。

#### Verification

- `make lint` 全部通过；默认门禁 81 项测试通过，真实 50 行附件门控另 1 项通过，隔离 PostgreSQL 并发门控另 2 项通过。
- `0003_phase1b` 在 PostgreSQL 16 上通过首次、重复、降级后再升级、完整 base 循环、ORM metadata 创建与 `alembic check`；默认 Compose 栈当前为 `0003_phase1b (head)`。
- 最新 Docker 镜像构建通过；七服务健康，Nginx Web/API 路由、request ID、未认证 401、Celery ping/任务注册和 PostgreSQL/Redis 无宿主端口均完成验证。
- 独立 Compose E2E 通过登录、Operator、脱敏上传、真实 Celery Parse、Preview、Confirm、重复 Confirm；数据库结果为 2 Influencer、2 PlatformAccount、1 Contact、2 Snapshot。
- PostgreSQL 与实际 Import Storage Object 在容器重启后保持数据和 SHA-256；隔离测试项目及测试卷已清理。
- Docker buildx 缺失继续记录为正式部署前事项，不阻塞 Phase 1B。

### Phase 1A

#### Added

- 新增 Department、部门级权限、Operator、Session 与 Audit Log 数据模型及 `0002_phase1a_auth` migration。
- 新增部门密码登录、操作人选择、当前身份、退出和管理员重置部门密码 API。
- 新增一次性 `bootstrap-admin` CLI，不包含默认账户或默认密码。
- 新增登录、操作人选择与最小登录成功 Web UI。
- 新增 Argon2id 密码、Redis 部门/IP 锁定、Session/CSRF Token 哈希与双提交 CSRF 防护。

#### Security

- Session Token 只以 SHA-256 hash 保存；Session Cookie 为 HttpOnly、SameSite=Lax，生产环境启用 Secure。
- 选择 Operator 只改变 Audit 归属；权限始终取自 Department，无法通过高角色 Operator 提权。
- 登录连续五次失败锁定五分钟；成功登录清除对应 Department + IP 失败状态。
- 密码重置在同一事务中撤销目标 Department 的所有 Session，并记录 Audit。
- Nginx 覆盖不可信 `X-Forwarded-For`，防止客户端伪造 IP 绕过登录锁定。
- API 校验错误不回显原始输入，避免无效密码出现在响应或诊断内容中。

#### Verification

- Phase 1A 核心、HTTP 与 Web 自动化测试覆盖登录、锁定、Session、CSRF、权限不可提升和 Audit。
- Docker 镜像构建通过，七服务健康；Alembic 首次及重复升级均保持 `0002_phase1a (head)`。
- Docker buildx 缺失继续记录为正式部署前事项，不阻塞本阶段。

## [0.1.0] - 2026-08-10

### Added

- 初始化 monorepo、Docker Compose、Next.js、FastAPI、PostgreSQL、Redis、Celery 与 Nginx 工程底座。
- 建立唯一共享 Python 核心包 `packages/backend_core`。
- 建立 lint、类型检查、测试、健康检查、迁移和 CI 基线。
- 记录已确认的产品/技术决策及仍待外部输入的问题。
- 新增 `/health/live` 与 `/health/ready` 基础设施接口。
- 新增空的 `0001_phase0` Alembic baseline migration。

### Security

- 应用容器使用非 root 用户运行，PostgreSQL 与 Redis 只存在于内部网络。
- 本地开发 Secret 自动随机生成；生产 `APP_MASTER_KEY` 必须从环境 Secret 注入。
- 结构化日志对密码、Token、凭证和 Key 字段进行递归脱敏。

### Verification

- Python lint、Black、mypy、ESLint、Prettier 和 TypeScript 检查已纳入 `make lint`。
- Python、API、Worker 和 Web 测试已纳入 `make test`。
- 七服务状态、HTTP readiness 与 Celery ping 已纳入 `make health`。
- Phase 0 共 7 项测试通过；PostgreSQL 不可用时 readiness 正确返回 503。
- Alembic baseline 首次升级与重复升级均通过，并在 PostgreSQL 容器重启后保持 `0001_phase0`。
- API、Worker、Scheduler、Web 均以非 root 用户运行；PostgreSQL 与 Redis 无宿主机端口映射。

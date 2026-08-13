# V1 验收标准

## 1. 登录

- [x] 正确部门密码可登录
- [x] 错误密码不可登录
- [x] 连续 5 次错误锁 5 分钟
- [x] 登录后可选择操作人
- [x] 所有关键操作带 operator_id
- [x] 管理员可重置部门密码
- [x] Session 过期正确

---

## 2. 导入

- [x] 支持 xlsx
- [x] 支持 csv
- [x] 可识别灰豚主要字段
- [x] 可手动字段映射
- [x] 重复达人不重复创建
- [x] 导入结果有统计
- [x] 异常行可查看
- [x] 原始导入数据可追溯

### Phase 1B 扩展自检（2026-08-10）

- [x] Preview 不写达人业务表，Confirm 使用单事务正式写入，Cancel 不写业务数据。
- [x] Preview Revision、Plan Hash 与相关状态失效保护；旧 Revision 不会静默改变动作后继续。
- [x] 同 Job/Revision 重复 Confirm 幂等，并发 Confirm 与相同硬身份并发由 PostgreSQL 锁和唯一约束保护。
- [x] Email 不作为达人匹配依据；不同达人相同 Email 可共存并标记疑似重复；人工 Contact 不被灰豚修改。
- [x] 非破坏性同源 Freshness、Current Metrics 与不可变 Metric Snapshot 已覆盖自动化测试。
- [x] 真实 50 行、37 列灰豚 CSV 通过仓库外门控集成测试；测试不输出原始敏感字段。
- [x] `0003_phase1b` 首次、重复、降级后再升级及 ORM drift 检查已在隔离 PostgreSQL 16 验证。
- [x] 行级 UI 可查看标准化数据、完整原始行、Warning 与 Error；Viewer Mutation 由后端拒绝。

以上扩展项仅验收 Phase 1B 导入能力；完整达人库由下方 Phase 1C 项验收。

---

## 3. 达人库

- [x] 列表可分页
- [x] 可搜索昵称
- [x] 可按赛道筛选
- [x] 可按粉丝筛选
- [x] 可按负责人筛选
- [x] 可按 CRM 阶段筛选
- [x] 联系方式正常显示
- [x] 历史数据不被新导入覆盖错误

---

## 4. Phase 2 — 批量获取、智能筛选、数据新鲜度与定向刷新

文档状态：Phase 2 设计仍按 Task 0 冻结。下方原有主题清单是完整 MVP release checklist；新增的 Task 6 小节单独记录当前 Atomic Confirm/Revalidation/Recovery 的任务级验收，不代表 Task 7+ 已实现或获得授权。

### Task 0 设计冻结

- [x] `docs/PHASE_2_SCOPE.md` 已冻结聚合边界、状态机、数据语义、API 计划、Migration 拆分、任务顺序与测试矩阵。
- [x] 旧的 Browser Automation 方案已废弃；旧的 AI、Playbook、Campaign Phase 2 路线已延期，不属于当前 Phase 2。
- [x] 唯一相关 UNKNOWN 只保留灰豚实际支持的批量重新定位方式；该项不阻塞 Task 1–7。

### Bulk Batch 与统一 Preview

- [ ] 一个 `ImportJob` 可承载多个 `ImportJobFile`；不创建 `ImportBatch` 或 `BatchRow`。
- [ ] 每个文件保留 Stored File、SHA-256、文件 occurrence、Mapping、`source_acquired_at` 和原始行号的可追溯关系。
- [ ] `ImportJobFile.source_acquired_at_confirmation_required` 是历史 SHA acquisition 待确认状态的唯一 occurrence-level 持久化事实源；禁止以动态跨 Job 查询、`error_code`、Redis、Audit JSON 或内存状态替代。
- [ ] `import_job_file_client_ids` 是 `(import_job_id, client_file_id) → import_job_file_id` 的唯一权威持久化幂等映射；Job 内 client ID 唯一，复合 FK 保证 alias occurrence 属于同一 Job，一个 occurrence 可有多个 aliases，Redis/Audit/内存不得充当事实源。
- [ ] 同一 Job 内相同 SHA 重复上传幂等返回已有 occurrence；不同 client ID 命中同 SHA 必须为已有 occurrence 持久化新 alias；不同 Job 可复用 Storage Blob，但必须重新 Parse 和 Preview。
- [ ] 幂等真值全部通过：A+X 首次创建；A+X 重放返回同 occurrence；A+Y 返回 409 且不改绑定；B+X 返回同 occurrence并持久化 B alias；之后 B+Y 稳定返回 409。
- [ ] PostgreSQL 16 并发门禁全部通过：A+X/A+X 最终一个 occurrence/一个 alias；A+X/B+X 最终一个 occurrence/两个 aliases；A+X/A+Y 最终一个 A binding、一个成功和一个 deterministic 409；均不得出现 500、覆盖或分叉 alias。
- [ ] `0004` 与 Legacy single-file 兼容桥同版本交付；现有 `POST /import-jobs` 在 `0004` head 仍能创建 occurrence、写入 Row file FK 并通过 Phase 1B 全回归，且不伪造 observed time 或 client-ID alias。
- [ ] 坏文件或 Mapping 失败时 Batch 保持 Draft；Preview 前可 replace、exclude、retry；存在 blocking file 时不能生成 Preview。
- [ ] 所有纳入文件形成一个统一 Preview Revision；文件内、跨文件和数据库三层硬身份去重共用 Phase 1B Matcher。
- [ ] Email 仅标记疑似重复，绝不作为自动匹配或自动合并依据；身份冲突进入人工复核。
- [ ] Preview 不写达人业务表；MVP 禁止自动 Confirm；Confirm 整批重新校验、单事务提交、支持幂等并拒绝 stale revision。
- [ ] `raw_rows = create + update + no_change + skip + error + manual_review`；Batch Duplicate 属于 skip 子集，Warning/Contact Duplicate 为叠加维度。
- [ ] `unique_rows = raw_rows - internal_duplicate_rows`，并可按 category 查看行级依据。

### Structured Screening

- [ ] 一个 ImportJob 仅绑定一个 originating CollectionJob，并只计算该 CollectionJob 的 `MATCH / NOT_MATCH / UNKNOWN`。
- [ ] `screening_rules.schema_version` 与单调 `screening_rules_revision` 分离；MVP 只使用本次 owner ImportRow 的 Canonical incoming platform、Source Tag 原值精确 ANY 匹配和 Followers 闭区间，数据库旧值不补齐。
- [ ] industry、subdirection、purpose、notes 不参与模糊推断；缺失或非法数据返回 UNKNOWN；不使用 AI semantic matching。

### Task 6 — Atomic Confirm / Revalidation / Recovery

当前证据口径：`[x]` 表示已有直接自动化测试/真实 PostgreSQL 16 Gate 通过；AC23 单独记录最终全仓质量认证。Task 6 PostgreSQL Atomic Confirm + recovery 组合 Gate 为 25 passed；2000-row mixed correctness run 为 76 SQL（55 SELECT、21 DML、7 advisory lock）、4.005935s、RSS high-water 309,641,216 bytes，最终为 1998 Influencer/Account、1 SourceIdentity、1996 SourceState、2 Contact、1995 CurrentMetrics/Snapshot。正式 37 列灰豚兼容 all-new 性能数据另以 1 次预热 + 5 次测量运行，wall P95=4.483997s、CPU P95=3.918085s、55 SQL、RSS high-water P95=343,638,016 bytes；mixed 业务正确性由前述独立 Gate 覆盖。

- [x] AC1：Unified Preview 可由人工显式携带当前 revision 发起 Confirm；不存在 Auto Confirm。
- [x] AC2：写入前完整重建并核对 manifest/SHA、Mapping、acquisition/confirmation、Screening revision/hash、normalized rows、duplicate owner、hard match、数据库 current state、row plan hash 与 canonical summary。
- [x] AC3：revision、plan 或相关数据库事实不一致时整批 `PREVIEW_STALE`，不把重算结果当作新 Preview 自动确认。
- [x] AC4：所有 included files 的业务写入、Row committed lineage、Job result/completed、task completed 和成功 Audit 在一个 PostgreSQL transaction 中提交。
- [x] AC5：Legacy/Bulk 共享同一 Phase 1B Matcher/Planner/Merge materialization；Task 6 不建立第二套写入规则。
- [x] AC6：manual Contact 保护保持不变，Confirm 不以导入值覆盖人工来源 Contact。
- [x] AC7：Email 只产生 possible duplicate signal，永不成为 Hard Match 或自动 Merge key。
- [x] AC8：MetricSnapshot 只按 Phase 1B frozen semantics append，禁止 update/delete/merge 旧 Snapshot。
- [x] AC9：Confirm 双击、同 token 重投和 completed replay 幂等，不重复写入业务实体或 Snapshot。
- [x] AC10：两个 Batch 并发创建同一 New Identity 时通过稳定锁/约束只提交一份业务实体，另一方整批 stale 或安全重试。
- [x] AC11：Existing Account 在 Preview/Confirm 间并发变化时无 lost update；旧 Preview 不能覆盖更新后的 current state。
- [x] AC12：真实 PostgreSQL 16 的 4×500/2000-row Confirm 通过 entity-count、SQL、wall time 与 RSS blocker。
- [x] AC13：Confirm 最后一批 Row/业务写入注入失败时整个 business transaction 回滚，无部分达人、lineage 或 completed task。
- [x] AC14：business commit 后、Broker ACK 前崩溃时，重投读取 completed task/result 并幂等结束。
- [x] AC15：API DB commit 后 Broker publish 丢失由持久 reservation 和 bounded `FOR UPDATE SKIP LOCKED` reconciler 正式关闭。
- [x] AC16：隔离 Compose Redis 7.4.10 真实服务重启后，PostgreSQL task state/attempts/lease 以同 token 恢复；Confirm 从 dispatch=1/run=0 收敛为 completed/dispatch=2/run=1，无用户重试或重复业务写入。
- [x] AC17：Redis 停止且 dispatch 未完成时真实重启 API 进程，DB request 保持 requested；API 重启后由首次启动的 Beat/Worker reconciler 自动完成同一 Confirm。
- [x] AC18：dispatch/run retry 均由 PostgreSQL 计数并受 Settings 上限和 bounded backoff 约束；耗尽进入 terminal。
- [x] AC19：Cancel 只取消 requested/retry_wait；running 返回 409 `IMPORT_TASK_RUNNING`，不产生取消/提交分叉。
- [x] AC20：Phase 1B Legacy CSV/XLSX、Preview/Confirm、duplicate confirm、Contact、Metrics 与 Snapshot 回归通过。
- [x] AC21：Task 5 Unified Preview summary、Screening、Change Summary、category 与 persisted row 输出回归通过（91 项 Task 5 unit + PG 2000-row Preview）。
- [x] AC22：成功 Confirm 保留未来 Freshness 所需的准确 occurrence/source acquisition/ImportRow committed lineage，但未实现 Task 7 Freshness API。
- [x] AC23：最终 `make test` 已以显式 PostgreSQL 16 URL 通过（Backend/Integration/Smoke 368、API 24、Worker 20、Web 29）；`make lint`、`make compose-validate`、正式 Task 6 2k/5k 与 Task 4/5 5k/10k opt-in 均实际通过。
- [x] AC24：Alembic head/current 仍为 `0004_phase2_bulk_import`，真实 PG16 `alembic check` 返回 no new operations；Task 6 未创建 `0005`。
- [x] AC25：本次 Task 6 未实现、提交、迁移或部署 Task 7+、Web UI 或 Server changes。

### Freshness 与 Refresh Queue

- [ ] 新 Bulk Draft 上传文件的 `source_acquired_at` 默认服务器接受时间，可在首次 Preview 前人工修改，之后冻结；普通 server-default 上传为 confirmation required=false，历史 SHA + server-default 的新 occurrence 为 true，显式时间或 PATCH 确认后为 false；不得从文件名、mtime 或未知来源字段推断。Legacy 单文件兼容路径保持 NULL/legacy_unknown/false。
- [ ] Legacy 数据缺少可靠 acquisition time 时显示 unknown，或明确显示 `last_huitun_imported_at`；不得把 `committed_at` 冒充 observed time。
- [ ] Freshness 以 PlatformAccount + Source 为粒度，使用 Settings 中 `<=7 / 8–30 / 31–90 / >90` 天阈值；不创建 Policy 表。
- [ ] Refresh Queue 由 Department 拥有，候选来自公司级 Influencer Library；Owner 或导入部门不改变公司级读取语义。
- [ ] Queue quota 仅为创建参数，系统不宣称知道灰豚真实剩余额度；不创建 DailyQuotaPlan。
- [ ] `NO_CHANGE` 仅在非空 `source_acquired_at` 严格晚于非空 Queue baseline 且 `source_acquired_at_confirmation_required=false` 时可 fulfill；baseline 为空或 confirmation required=true 均 unresolved。
- [ ] Queue 导出只包含数据库真实存在的 Identity；在灰豚批量定位能力完成真人验证前，不宣称导出 CSV 可被灰豚直接消费。
- [ ] 仍被 ImportJobFile lineage 引用的 StoredImportFile 不得自动物理删除；MVP 不实现 archive/delete lifecycle。

### Migration、Worker 与性能

- [ ] `0004_phase2_bulk_import` 只承载 Bulk Import Schema（含 authoritative client-ID alias、acquisition confirmation Boolean/CHECK 与 durable `import_task_requests`）；该未发布 Revision 直接完善，不另建 Migration；`0005_phase2_refresh_queue` 仍只承载 Refresh Queue Schema。
- [ ] `import_task_requests` 以唯一 token、kind/target CHECK、Job/File 复合 FK、持久 dispatch/run attempts、retry time、Worker lease 和 completed/terminal state 成为 task lifecycle 唯一事实源；Redis、Audit、`error_code` 与 Celery retry count 均不得替代。
- [ ] PostgreSQL 16 task schema gate 覆盖 token unique、四类 active partial unique、terminal 历史保留、confirm revision/file_parse file、state/timestamp、非负 attempts、复合 FK、reconciliation indexes、无历史 backfill及有 durable task 时危险 downgrade 拒绝。
- [ ] `0003 → 0004 → 0005` 通过 fresh、repeat、真实数据副本、metadata 与 `alembic check`；任何无法无损投回 0003 的 Phase 2 Bulk/Screening 数据以及任何 Queue 证据都必须让危险 downgrade 安全拒绝。
- [ ] `0004` alias Migration Gate 在 PostgreSQL 16 覆盖 fresh DB、0003 realistic data、repeat upgrade、safe downgrade→0003→0004、dangerous multi-file downgrade guard、`alembic check`、metadata drift、Legacy lineage 和 alias unique/composite FK；Legacy occurrence 保持 0 alias。
- [ ] `0004` confirmation CHECK Gate 覆盖 Legacy NULL/legacy_unknown/false、普通 timestamp/server_default/false、历史 SHA timestamp/server_default/true、显式 timestamp/user_confirmed/false，并由数据库拒绝 NULL/server_default/true、timestamp/user_confirmed/true、NULL/legacy_unknown/true。
- [ ] 当前七服务拓扑不变；Celery Worker concurrency=2，Heavy Preview/Confirm 同时最多 1 个。
- [ ] File Parse task、Job failed_stage/retry、Broker dispatch reconciliation 与 Worker crash recovery 均由持久状态恢复，不依赖进程内状态。
- [ ] 2000 行 Parse、Normalize、Dedup、Preview、Confirm 稳定且没有逐行 N+1，作为 MVP 发布 blocker。
- [ ] 5000 行通过 capacity observation；10000 行通过 correctness/no-OOM soak，10000 行耗时不作为 MVP 发布 blocker。
- [ ] Phase 1A–1C Auth、RBAC、CSRF、Audit、公司级达人读取、Viewer Contact 脱敏和 Phase 1B Merge/Snapshot 全部回归通过。
- [ ] 真实 PostgreSQL、并发、Worker recovery、Compose 与浏览器 E2E 通过。

---

## 5. AI（后续阶段）

- [ ] AI 输出结构化
- [ ] AI 失败有 fallback
- [ ] 无事实时不虚构
- [ ] 生成邮件符合模板结构
- [ ] 保存 prompt_version
- [ ] 保存模型信息
- [ ] 可人工重新生成

---

## 6. Campaign（后续阶段）

- [ ] 可以创建
- [ ] 可以选择达人
- [ ] 可以选择 Playbook Version
- [ ] 可以设置每天发送量
- [ ] 可以设置时间窗口
- [ ] 可以设置审核模式
- [ ] 可以暂停
- [ ] 可以继续
- [ ] 可以取消

---

## 7. 邮件

- [ ] 同 Lead 同 Step 不重复发送
- [ ] suppression 不发送
- [ ] invalid 不发送
- [ ] hard bounce 不继续
- [ ] reply 后不 follow-up
- [ ] mailbox 达上限停止
- [ ] campaign 达上限停止
- [ ] 发送日志完整

---

## 8. Inbox

- [ ] 回复能同步
- [ ] 邮件线程正确
- [ ] AI 自动分类
- [ ] 可人工修改分类
- [ ] 可标记已加微信
- [ ] 可停止触达
- [ ] 可创建 CRM 跟进

---

## 9. CRM

- [ ] 看板正确
- [ ] 拖拽产生事件
- [ ] 可记录微信
- [ ] 可记录备注
- [ ] 可设置下次跟进
- [ ] 到期显示待办
- [ ] 历史 timeline 完整

---

## 10. Analytics

- [ ] 发送量准确
- [ ] 回复量准确
- [ ] 回复率准确
- [ ] 加微信准确
- [ ] 赛道维度准确
- [ ] 模板维度准确
- [ ] 标题维度准确
- [ ] 操作人维度准确

---

## 11. 安全

- [ ] 密码不明文
- [ ] Secret 不进 Git
- [ ] PostgreSQL 不公网暴露
- [ ] 关键 API 有权限
- [ ] 下载/导出有 audit
- [ ] 生产 HTTPS

---

## 12. 上线验收

Pilot 100 达人：
- [ ] 导入成功率 > 95%
- [ ] 无重复达人事故
- [ ] 无重复邮件事故
- [ ] 回复后停止 Follow-up 成功
- [ ] AI 邮件无明显虚构事实
- [ ] 数据统计与抽查一致

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

文档状态：`DESIGN FROZEN — Task 0`。仅 Task 0 小节的已勾选项代表设计冻结；其余均为尚未实现的 MVP 验收门禁。

### Task 0 设计冻结

- [x] `docs/PHASE_2_SCOPE.md` 已冻结聚合边界、状态机、数据语义、API 计划、Migration 拆分、任务顺序与测试矩阵。
- [x] 旧的 Browser Automation 方案已废弃；旧的 AI、Playbook、Campaign Phase 2 路线已延期，不属于当前 Phase 2。
- [x] 唯一相关 UNKNOWN 只保留灰豚实际支持的批量重新定位方式；该项不阻塞 Task 1–7。

### Bulk Batch 与统一 Preview

- [ ] 一个 `ImportJob` 可承载多个 `ImportJobFile`；不创建 `ImportBatch` 或 `BatchRow`。
- [ ] 每个文件保留 Stored File、SHA-256、文件 occurrence、Mapping、`source_acquired_at` 和原始行号的可追溯关系。
- [ ] 同一 Job 内相同 SHA 重复上传幂等返回已有 occurrence；不同 Job 可复用 Storage Blob，但必须重新 Parse 和 Preview。
- [ ] `0004` 与 Legacy single-file 兼容桥同版本交付；现有 `POST /import-jobs` 在 `0004` head 仍能创建 occurrence、写入 Row file FK 并通过 Phase 1B 全回归，且不伪造 observed time。
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

### Freshness 与 Refresh Queue

- [ ] 新 Bulk Draft 上传文件的 `source_acquired_at` 默认服务器接受时间，可在首次 Preview 前人工修改，之后冻结；不得从文件名、mtime 或未知来源字段推断。Legacy 单文件兼容路径保持 acquisition unknown。
- [ ] Legacy 数据缺少可靠 acquisition time 时显示 unknown，或明确显示 `last_huitun_imported_at`；不得把 `committed_at` 冒充 observed time。
- [ ] Freshness 以 PlatformAccount + Source 为粒度，使用 Settings 中 `<=7 / 8–30 / 31–90 / >90` 天阈值；不创建 Policy 表。
- [ ] Refresh Queue 由 Department 拥有，候选来自公司级 Influencer Library；Owner 或导入部门不改变公司级读取语义。
- [ ] Queue quota 仅为创建参数，系统不宣称知道灰豚真实剩余额度；不创建 DailyQuotaPlan。
- [ ] `NO_CHANGE` 仅在非空 `source_acquired_at` 严格晚于非空 Queue baseline，且历史复用 SHA 已经人工确认 acquisition time（或 SHA 从未复用）时可 fulfill；baseline 为空或未确认旧 Blob replay 均 unresolved。
- [ ] Queue 导出只包含数据库真实存在的 Identity；在灰豚批量定位能力完成真人验证前，不宣称导出 CSV 可被灰豚直接消费。
- [ ] 仍被 ImportJobFile lineage 引用的 StoredImportFile 不得自动物理删除；MVP 不实现 archive/delete lifecycle。

### Migration、Worker 与性能

- [ ] `0004_phase2_bulk_import` 只承载 Bulk Import Schema；`0005_phase2_refresh_queue` 只承载 Refresh Queue Schema。
- [ ] `0003 → 0004 → 0005` 通过 fresh、repeat、真实数据副本、metadata 与 `alembic check`；任何无法无损投回 0003 的 Phase 2 Bulk/Screening 数据以及任何 Queue 证据都必须让危险 downgrade 安全拒绝。
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

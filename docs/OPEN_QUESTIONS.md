# Open Questions

更新时间：2026-08-11

本文件只保留无法从现有文档、安全规则或保守工程原则推导的外部事实。工程实现细节不作为产品阻塞问题。

Phase 1A–1C 已完成、部署并通过真实服务器 E2E。新的 Phase 2 Task 0 已冻结；下表只有 `PHASE2-UNKNOWN-001` 与当前 Phase 2 直接相关，且不阻塞 Task 1–13。

## OPEN

| ID | 需要的外部信息 | 最晚确认阶段 | 当前影响 |
|---|---|---|---|
| PHASE2-UNKNOWN-001 | 尚未通过真实灰豚产品流程验证：灰豚实际支持使用 platform_user_id、profile_url、handle、nickname 或 external_source_id 中哪一种/哪些字段进行批量重新定位、导入或定向导出。验证前系统只导出数据库真实 Identity，不承诺 CSV 可被灰豚直接消费。 | 冻结“Refresh Queue CSV → 灰豚批量定向刷新”能力前 | 不阻塞 Task 1–13 的 Bulk Import 与 Freshness；阻塞该外部定向刷新能力的冻结与验收 |
| OPEN-003 | 实际 AI Provider、模型和生产凭证注入信息 | 未来 AI 能力重新启动前 | 不阻塞当前 Phase 2 |
| OPEN-004 | Pilot Mailbox 的 SMTP/IMAP 参数及服务端能力 | 未来邮件阶段前 | 不阻塞当前 Phase 2 |
| OPEN-005 | 生产 HTTPS 证书的签发与续期方案 | 正式公网发布前 | 内部 SSH Tunnel 测试不依赖；不阻塞当前 Phase 2 |
| OPEN-006 | 生产备份存储目标、最终保留期和恢复责任人 | 正式生产发布前 | 不阻塞当前 Phase 2 |
| OPEN-007 | 生产告警渠道、接收人和响应责任 | 正式生产发布前 | 不阻塞当前 Phase 2 |

## PRE-DEPLOYMENT

- `PREDEPLOY-001`：当前使用 classic builder 完成内部测试构建；正式公网/生产部署前安装并验证 Docker buildx。该项不阻塞 Phase 2 开发。

## RESOLVED

以下决策已经确认，不再作为 blocker。

| ID | 已确认决策 | 影响阶段 |
|---|---|---|
| RESOLVED-001 | V1 使用“部门 + 密码 → 选择操作人”；操作人只承担审计归属，Session 权限来自部门；管理员使用独立管理部门/权限。 | Phase 1 |
| RESOLVED-002 | Influencer、达人去重、Suppression、Playbook 公司级共享；Campaign、Mailbox 部门级；Task 部门/操作人级；Analytics 支持公司、部门、操作人维度。 | Phase 1-4 |
| RESOLVED-003 | Session 默认 12 小时，remember_me 为 30 天；允许多设备；重置部门密码撤销该部门全部 Session；失败锁定按 department + IP 计算。 | Phase 1 |
| RESOLVED-004 | 提供一次性 `bootstrap-admin` CLI，禁止内置默认管理员密码。 | Phase 1 |
| RESOLVED-005 | 导入文件保存到持久化 `/data/imports`，默认保留 30 天，计算 SHA-256，持久化 Mapping 和原始 Import Row。 | Phase 1 |
| RESOLVED-006 | 自动去重硬匹配顺序为 XHS platform_user_id、huitun_id、profile_url；邮箱只标记疑似重复。 | Phase 1 |
| RESOLVED-007 | `influencers.crm_stage` 是全局 CRM 状态唯一事实源；Campaign Lead 状态只描述该 Campaign 生命周期。 | Phase 1-4 |
| RESOLVED-008 | 未来 AI 阶段增加 `ai_runs`，保存任务、Provider、模型、Prompt、输入哈希、原始/解析结果、状态和错误；不属于当前 Phase 2。 | 未来 AI 阶段 |
| RESOLVED-009 | `recent_topics` 只能来自真实导入数据；没有则为空，不抓取、不猜测。 | Phase 1-2 |
| RESOLVED-010 | ReplyClass 为 A、B、C、D、E、REJECT、UNSUBSCRIBE、AUTO_REPLY、UNKNOWN；F 只是无回复 Lead 状态。 | Phase 3 |
| RESOLVED-011 | 未来 Campaign 阶段增加 `campaign_variants`；Lead 加入 Campaign 时固定 Variant，运行中不得重新分组；不属于当前 Phase 2。 | 未来 Campaign 阶段 |
| RESOLVED-012 | 未来 Campaign 审核模式为 all、first_n、sample、auto；默认 Campaign 级 first_n=50；sample 使用 lead_id 确定性抽样；不属于当前 Phase 2。 | 未来 Campaign 阶段 |
| RESOLVED-013 | V1 使用 SMTP outbound + IMAP inbound；无法确认 delivered 时保存 null，UI 显示未知，不以 sent 代替 delivered。 | Phase 3 |
| RESOLVED-014 | 邮件线程保存 internet_message_id、in_reply_to、references、thread_key、from_address、to_addresses。 | Phase 3 |
| RESOLVED-015 | 邮件幂等键固定为 campaign_lead_id + sequence_step；已发送 Step 永不自动重发。 | Phase 3 |
| RESOLVED-016 | 同时支持退订链接和退订意图识别；任一种触发均写入 suppression_list。 | Phase 3 |
| RESOLVED-017 | 默认业务时区为 Asia/Shanghai。 | 全阶段 |
| RESOLVED-018 | `campaign_leads.stop_email` 默认 false；wechat_added=true 时默认 stop_email=true。 | Phase 3 |
| RESOLVED-019 | APP_MASTER_KEY 从生产 Secret 注入，不进入数据库、Git 或日志；邮箱凭证加密存储。 | Phase 0、3 |
| RESOLVED-020 | Analytics 指标、唯一 Lead 计数、A/B 正向回复及 Asia/Shanghai 事件日期归属按已确认口径执行。 | Phase 4 |
| RESOLVED-021 | 已读取真实灰豚 CSV：UTF-8 BOM、逗号分隔、50 行、37 列且行宽一致；不存在 huitun_id，“灰豚指数”为 decimal；50 个 Profile URL 均可提取唯一平台 ID；9 个有效 Email、41 个缺失、0 个无效；样本内无硬重复或重复 Contact。真实文件保持在仓库外。 | Phase 1B |
| RESOLVED-022 | Phase 1B 读取要求认证；Mutation 还要求已选择 Operator、CSRF 与非 Viewer 角色。Manager/Operator 限本部门，Super Admin 可跨部门；Operator 只改变 Audit 归属，Session 权限仍来自 Department。 | Phase 1B |
| RESOLVED-023 | 生产服务器与生产域名已购买；仅证书、备份目标和告警渠道继续保持 OPEN。 | Phase 5 |
| RESOLVED-024 | 一个 ImportJob 就是一个多文件 Bulk Batch；通过 ImportJobFile 表达 occurrence，不新增 ImportBatch 或 BatchRow。 | Phase 2 |
| RESOLVED-025 | 同 Job 相同 SHA 幂等返回已有 occurrence；跨 Job 可复用 Blob，但必须重新 Parse 与 Preview。 | Phase 2 |
| RESOLVED-026 | 坏文件或 Mapping 失败时 Batch 保持 Draft；Preview 前允许 exclude/retry/mapping correction（per-file replace deferred / OUT OF SCOPE），仍有 blocking file 时禁止 Preview。 | Phase 2 |
| RESOLVED-027 | MVP 一个 ImportJob 只绑定一个 originating CollectionJob，只计算该 Job 的 MATCH/NOT_MATCH/UNKNOWN；多 Collection 匹配延期。 | Phase 2 MVP |
| RESOLVED-028 | Screening 使用 versioned structured rules；MVP 只用 platform、Source Tag exact、Followers 范围，不从 free text 或 AI 推断，无法判断即 UNKNOWN。 | Phase 2 MVP |
| RESOLVED-029 | ImportJobFile.source_acquired_at 表示文件实际从来源取得的大致时间；新 Bulk Draft 上传默认服务器接受时间、Preview 前可修改、之后冻结，Legacy 单文件兼容路径不伪造。 | Phase 2 |
| RESOLVED-030 | Freshness 使用 Settings 的 7/30/90 天阈值，不创建 Freshness Policy 表。 | Phase 2 MVP |
| RESOLVED-031 | Refresh Queue 由 Department 拥有，候选来自公司级 Influencer Library，不改变公司级读取语义。 | Phase 2 |
| RESOLVED-032 | NO_CHANGE 只有在 source_acquired_at 严格晚于 Queue baseline observation 时才能 fulfill。 | Phase 2 |
| RESOLVED-033 | 被 ImportJobFile lineage 引用的 StoredImportFile 不得物理删除；MVP 不实现 archive/delete lifecycle。 | Phase 2 MVP |
| RESOLVED-034 | Daily quota 只是 Queue 创建参数，系统不宣称知道灰豚真实剩余额度，不创建 DailyQuotaPlan。 | Phase 2 MVP |
| RESOLVED-035 | Migration 拆分为 0004_phase2_bulk_import 与 0005_phase2_refresh_queue，禁止合并或创建空 Migration。 | Phase 2 |
| RESOLVED-036 | 当前服务器 Worker concurrency=2，Heavy Preview/Confirm 同时最多 1 个，不增加新服务。 | Phase 2 MVP |
| RESOLVED-037 | 2000 行为 MVP 稳定性能发布 blocker；5000 行为 capacity observation；10000 行为 correctness/no-OOM soak，耗时不阻塞 MVP。 | Phase 2 MVP |
| RESOLVED-038 | Same-time Metrics 与 Phase 1B Matcher/Merge/Snapshot 语义保持不变，Phase 2 不另建实现。 | Phase 2 |
| RESOLVED-039 | 已确认保留 `PHASE2-UNKNOWN-001` 及其阻塞边界：不阻塞 Task 1–13，但阻塞“Queue CSV 可直接用于灰豚定向刷新”的承诺；灰豚实际批量定位能力本身仍未验证。 | Phase 2 Full |
| RESOLVED-040 | UI-BACKLOG-001 可在 Phase 2 Web Task 实现人类可读粉丝数并保留精确 Tooltip；UI-BACKLOG-002 整体视觉重构继续延期。 | Phase 2 / Future |

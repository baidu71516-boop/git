# Open Questions

更新时间：2026-08-10

本文件只保留无法从现有文档、安全规则或保守工程原则推导的外部事实。工程实现细节不作为产品阻塞问题。

## OPEN

| ID | 需要的外部信息 | 最晚确认阶段 | 当前影响 |
|---|---|---|---|
| OPEN-001 | 实际灰豚导出样本，用于确认列名、别名与数据格式 | Phase 1B 前 | 不阻塞 Phase 1A |
| OPEN-002 | 管理部门与普通部门的最终业务权限矩阵 | Phase 1B 前 | 不阻塞 Phase 1A；1A 仅建立部门级 RBAC 基础 |
| OPEN-003 | 实际 AI Provider、模型和生产凭证注入信息 | Phase 2 前 | 不阻塞 Phase 0 |
| OPEN-004 | Pilot Mailbox 的 SMTP/IMAP 参数及服务端能力 | Phase 3 前 | 不阻塞 Phase 0 |
| OPEN-005 | 生产域名、证书、备份目标和告警接收渠道 | Phase 5 前 | 不阻塞 Phase 0 |

## PRE-DEPLOYMENT

- 当前本地 Docker Compose 使用 classic builder 完成验证；正式部署前安装并验证 Docker buildx。该项不阻塞 Phase 1A。

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
| RESOLVED-008 | 增加 `ai_runs`，保存任务、Provider、模型、Prompt、输入哈希、原始/解析结果、状态和错误。 | Phase 2 |
| RESOLVED-009 | `recent_topics` 只能来自真实导入数据；没有则为空，不抓取、不猜测。 | Phase 1-2 |
| RESOLVED-010 | ReplyClass 为 A、B、C、D、E、REJECT、UNSUBSCRIBE、AUTO_REPLY、UNKNOWN；F 只是无回复 Lead 状态。 | Phase 3 |
| RESOLVED-011 | 增加 `campaign_variants`；Lead 加入 Campaign 时固定 Variant，运行中不得重新分组。 | Phase 2 |
| RESOLVED-012 | 审核模式为 all、first_n、sample、auto；默认 Campaign 级 first_n=50；sample 使用 lead_id 确定性抽样。 | Phase 2 |
| RESOLVED-013 | V1 使用 SMTP outbound + IMAP inbound；无法确认 delivered 时保存 null，UI 显示未知，不以 sent 代替 delivered。 | Phase 3 |
| RESOLVED-014 | 邮件线程保存 internet_message_id、in_reply_to、references、thread_key、from_address、to_addresses。 | Phase 3 |
| RESOLVED-015 | 邮件幂等键固定为 campaign_lead_id + sequence_step；已发送 Step 永不自动重发。 | Phase 3 |
| RESOLVED-016 | 同时支持退订链接和退订意图识别；任一种触发均写入 suppression_list。 | Phase 3 |
| RESOLVED-017 | 默认业务时区为 Asia/Shanghai。 | 全阶段 |
| RESOLVED-018 | `campaign_leads.stop_email` 默认 false；wechat_added=true 时默认 stop_email=true。 | Phase 3 |
| RESOLVED-019 | APP_MASTER_KEY 从生产 Secret 注入，不进入数据库、Git 或日志；邮箱凭证加密存储。 | Phase 0、3 |
| RESOLVED-020 | Analytics 指标、唯一 Lead 计数、A/B 正向回复及 Asia/Shanghai 事件日期归属按已确认口径执行。 | Phase 4 |

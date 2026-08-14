# 项目管理与开发流程

## 1. 总周期建议

V1 推荐 5 个阶段。

---

# Phase 0：项目初始化

目标：建立可持续开发基础。

任务：
- 初始化 monorepo
- Docker Compose
- Next.js
- FastAPI
- PostgreSQL
- Redis
- Celery
- Nginx
- CI
- lint
- test
- env 示例

验收：
- 一条命令启动开发环境
- Web/API/Worker/Postgres/Redis 全部健康

---

# Phase 1：登录 + 达人采集 + 达人库

## Sprint 1.1 登录
- Department
- Operator
- Session
- Login
- Logout
- Lock
- Audit

## Sprint 1.2 导入
- Collection Job
- Excel upload
- Mapping
- Import Job
- Import Rows
- Huitun adapter

## Sprint 1.3 达人库
- Influencer
- Metrics
- Contacts
- Tags
- Dedup
- List
- Detail
- Filter

阶段验收：
灰豚 Excel 可稳定进入系统，并正确去重。

---

# Phase 2：Huitun Bulk Acquisition & Freshness Management

旧的 Phase 2 Browser Automation 方案以及“SOP + Campaign + AI”排期已经人工废弃。Phase 2 的唯一权威范围见 `docs/PHASE_2_SCOPE.md`。

当前开发检查点（2026-08-13）：Task 0–9 已完成本地实现与对应功能/性能门禁；Task 9 收口后只保留本地提交，不 push、merge、tag 或 deploy。Task 10 Functional Web 与后续任务未开始，每个后续 Task 仍需独立人工授权。

目标：

- 一个 ImportJob 支持多个灰豚 CSV/XLSX。
- 统一 Parse、Normalize、三层去重、Screening 和 Preview。
- 保持人工 Confirm 与 Phase 1B Merge 唯一写入路径。
- 建立可靠的 acquisition time、Freshness 与 Refresh Queue。
- 刷新回流继续更新 CurrentMetrics 并保留不可变 Snapshot。

实施顺序：

1. Task 0 — Design Freeze。
2. Task 1 — Bulk Domain、`0004_phase2_bulk_import` 与 Phase 1B single-file 兼容桥；Migration 不得脱离兼容代码单独部署。
3. Task 2 — Multi-file Upload / Storage。
4. Task 3 — Parse / Normalize / Batch Dedup。
5. Task 4 — Bulk Repository、预加载与 N+1 消除。
6. Task 5 — Unified Preview / Change Summary / Screening。
7. Task 6 — Atomic Confirm / Revalidation / Recovery：Preview/Confirm 共用唯一 Plan Builder，Legacy/Bulk 共用 Phase 1B Merge materialization；Confirm 全量 revalidation、整批单事务、持久 task reservation/generation/lease、bounded retry、`SKIP LOCKED` reconciliation、安全 Cancel 与 2000-row PostgreSQL blocker。
8. Task 7 — Freshness Domain 与 Influencer Read API。
9. Task 8 — Refresh Queue / Export 与 `0005_phase2_refresh_queue`。
10. Task 9 — Refresh Return Reconciliation。
11. Task 10 — Functional Web UX。
12. Task 11 — PostgreSQL / Concurrency / Performance Gates。
13. Task 12 — Docker E2E / 文档与部署准备。
14. Task 13 — Server Deployment；必须单独人工授权。

阶段门禁：

- 2000 rows 是 MVP 发布 blocker，必须稳定完成 Parse、Normalize、Dedup、Preview 与 Confirm，且没有逐行 N+1。
- 5000 rows 是 capacity/correctness Gate，并记录性能。
- 10000 rows 是 correctness/no-OOM soak，执行时间不阻塞 MVP 发布。
- 不新增服务；Worker concurrency=2，同时最多一个 Heavy Import。

Playbook、AI、Campaign 的旧 Phase 2 排期延期，未来必须重新需求冻结；本次不自动为后续 Phase 重新编号或授权实现。

---

# Phase 3：邮件 + Follow-up + Inbox

## Sprint 3.1 Mailbox
- SMTP config
- test connection
- encrypted secrets

## Sprint 3.2 Send Queue
- queue
- quota
- time window
- interval
- idempotency
- status

## Sprint 3.3 Follow-up
- Day 4
- Day 10
- stop rules
- scheduler

## Sprint 3.4 Inbox
- sync replies
- thread
- AI classify
- stop sequence
- processing

阶段验收：
完成真实小批量 100 人闭环。

---

# Phase 4：CRM + Analytics

## Sprint 4.1 CRM
- board
- stage change
- followup date
- notes
- tasks

## Sprint 4.2 Analytics
- daily aggregation
- overview
- industry
- subject
- template
- operator
- follower range

阶段验收：
无需 Excel 即可查看日报和周报。

---

# Phase 5：上线与稳定性

任务：
- 权限复查
- 安全检查
- 数据备份
- 恢复测试
- Worker 重试
- AI timeout
- SMTP failure
- 大文件导入
- 并发
- 监控
- 域名 HTTPS

---

## 2. 每个 Sprint 的工作流

```text
需求冻结
↓
Schema
↓
Migration
↓
Backend
↓
Test
↓
Frontend
↓
Integration
↓
QA
↓
Acceptance
↓
Merge
```

---

## 3. 项目看板

列：
- Backlog
- Ready
- In Progress
- Code Review
- QA
- Blocked
- Done

每张任务卡包含：
- 标题
- 对应 PRD
- API
- 数据表
- UI
- Acceptance
- 测试

---

## 4. 优先级

P0：
- 登录
- 数据不丢
- 去重
- 邮件不重复发
- 回复后停止
- suppression
- Audit

P1：
- AI分析
- AI个性化
- CRM
- Analytics

P2：
- 高级图表
- 更复杂实验
- Lookalike
- 多数据源自动接入

---

## 5. 上线策略

不要直接全公司上线。

### Pilot 1
- 1 个部门
- 1 个操作人
- 100 达人

### Pilot 2
- 2-3 人
- 300 达人

### Pilot 3
- 1000 达人

### Production
确认：
- 发送稳定
- 回复同步稳定
- AI 不乱编
- 去重正确
- 数据可追踪

---

## 6. 回滚规则

任何数据库变更：
- Alembic migration
- 有 downgrade（能做则做）
- 生产执行前备份

Campaign Bug：
- 能全局暂停发送
- 能暂停单 mailbox
- 能暂停单 campaign

---

## 7. 风险清单

### 灰豚导出格式变化
解决：字段映射层 + Adapter。

### AI 输出异常
解决：JSON Schema + validation + fallback。

### 邮件重复发送
解决：idempotency + unique。

### 邮箱异常
解决：quota + mailbox pause。

### 人员共用部门账号
解决：强制选择操作人 + audit。

### 数据误删
解决：soft delete。

---

## 8. 项目完成条件

V1 完成必须：
- 一个真实部门连续使用 7 天
- 无重复邮件事故
- 无达人数据明显丢失
- 回复后 Follow-up 能停止
- CRM 可持续记录
- Analytics 与数据库原始数据一致

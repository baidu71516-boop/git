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

# Phase 2：SOP + Campaign + AI

## Sprint 2.1 Playbook
- Playbook
- Version
- Template
- 内置 V1 SOP

## Sprint 2.2 AI
- Provider Adapter
- Fact Builder
- Creator Analysis
- Personalization
- Email generation

## Sprint 2.3 Campaign
- Campaign Create
- Lead select
- Sequence
- Preview
- Review

阶段验收：
选一批达人后能生成可审核邮件。

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

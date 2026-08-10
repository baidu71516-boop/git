# 技术架构

## 1. V1 架构

```text
Browser
  ↓
Nginx
  ├─ Next.js Web
  └─ FastAPI
       ├─ PostgreSQL
       ├─ Redis
       ├─ Celery Worker
       ├─ AI Provider Adapter
       └─ Email Provider Adapter
```

---

## 2. 部署

Docker Compose：

```text
nginx
web
api
worker
scheduler
postgres
redis
```

建议：
- 开发环境可共机
- 正式环境 V1 可单服务器
- 定期备份数据库

---

## 3. 模块边界

### auth
部门登录、Session、操作人。

### imports
Excel/CSV 解析、字段映射、去重。

### influencers
达人主数据。

### campaigns
Campaign 与 Lead。

### playbooks
SOP、模板、版本。

### ai
AI Provider、Prompt、结构化输出。

### email
邮箱、发送、事件、Follow-up。

### inbox
回复同步、分类。

### crm
阶段、跟进、备注、事件。

### analytics
指标聚合。

### audit
日志。

---

## 4. 数据源适配器

统一接口：

```python
class InfluencerSourceAdapter:
    def parse(self, file_or_payload): ...
    def normalize(self, raw_record): ...
    def validate(self, record): ...
```

V1：
- HuitunExcelAdapter
- GenericCsvAdapter

未来：
- XiaohongshuAdapter
- OtherProviderAdapter

---

## 5. AI Provider

```python
class AIProvider:
    async def generate_structured(
        self,
        task_type,
        payload,
        schema,
        model_config
    ): ...
```

不能在业务模块直接调用某厂商 SDK。

---

## 6. Email Provider

```python
class EmailProvider:
    async def send(...)
    async def sync_replies(...)
    async def get_delivery_events(...)
```

V1 支持：
- SMTP
- 可扩展 API provider

---

## 7. 后台任务

Celery Queue：

- default
- import
- ai
- email
- analytics

重要任务：
- import_file
- analyze_influencer
- generate_personalization
- generate_email
- send_email
- schedule_followup
- sync_inbox
- classify_reply
- aggregate_metrics

---

## 8. 幂等性

以下任务必须幂等：
- 导入
- 邮件发送
- Follow-up
- 回复同步
- Analytics 聚合

必须使用：
- idempotency key
- unique constraints
- distributed lock（必要时）

---

## 9. 错误处理

任务错误：
- retry
- max attempts
- dead-letter style 状态
- error_reason

用户必须能在后台看到失败原因。

---

## 10. 可观测性

必须记录：
- API error
- Worker error
- Email send failure
- AI failure
- Import failure
- Login failure

至少输出结构化日志。

# Codex 开发规则

## 0. 身份

你是本项目的代码实现工程师，不是产品经理。

你的职责：
- 按文档实现功能
- 保持代码结构清晰
- 编写必要测试
- 发现冲突时报告
- 不擅自改变产品定义

你无权：
- 删除业务字段
- 更改核心流程
- 改变用户角色
- 改变邮件触达规则
- 将飞书加入核心登录
- 把灰豚写死为唯一数据源
- 将 AI 输出直接视为可信事实
- 绕过平台限制、验证码、风控或访问权限

---

## 1. 强制阅读顺序

开始编码前必须阅读：

1. README.md
2. PRODUCT_PRD.md
3. UI_SPEC.md
4. ARCHITECTURE.md
5. DATABASE_SCHEMA.md
6. API_SPEC.md
7. AI_RULES.md
8. EMAIL_AUTOMATION.md
9. SECURITY_RULES.md
10. DEVELOPMENT_PLAN.md
11. ACCEPTANCE_CRITERIA.md

若文档冲突，优先级：

`CODEX_RULES.md > PRODUCT_PRD.md > SECURITY_RULES.md > DATABASE_SCHEMA.md > API_SPEC.md > UI_SPEC.md > 其他`

已经人工签字的阶段冻结文档是对该阶段的特别契约；当前 Phase 2 必须使用 `docs/PHASE_2_SCOPE.md`，它覆盖旧 PRD/排期中与当前 Phase 2 相冲突的内容，但不降低本文、`SECURITY_RULES.md` 或更新的人工决策所要求的安全边界。

---

## 2. 开发方式

每个功能必须遵守：

1. 先创建数据模型
2. 再创建服务层
3. 再创建 API
4. 再创建页面
5. 再补测试
6. 再更新 changelog

禁止：
- 页面直接访问数据库
- 业务逻辑全部写在 Controller / Route
- AI 调用散落在页面组件
- SMTP 密码写进源码
- 把状态字符串散落在全项目
- 直接删除数据
- 未记录日志的关键业务修改

---

## 3. 目录规范

当前项目固定目录：

```text
/apps
  /web
  /api
  /worker

/packages
  /backend_core
  /shared
  /ui
  /types

/infrastructure
  /docker
  /nginx
  /migrations

/docs
```

架构约束：
- `packages/backend_core` 是唯一共享 Python 业务核心。
- `apps/api` 只负责 HTTP、依赖注入和响应转换。
- `apps/worker` 只负责 Celery 初始化和异步任务入口。
- 禁止在 `apps/api` 内复制 Service/Repository/Provider 规则。
- 禁止恢复根目录 `services/`。
- Web、API、Worker 独立可运行
- 类型定义可复用
- 环境变量统一管理

---

## 4. 代码规则

### Python
- Python 3.12+
- 类型注解必须完整
- Pydantic v2
- SQLAlchemy 2.x
- Alembic migration
- Ruff / Black
- pytest

### TypeScript
- strict = true
- 禁止 any，确实需要时必须注明原因
- API 类型统一维护
- ESLint + Prettier
- React Query 或同类数据请求层
- 不允许页面直接拼接 API URL

---

## 5. 状态管理规则

所有核心状态必须使用 Enum。

包括：
- CampaignStatus
- LeadStatus
- EmailStatus
- ReplyClass
- CRMStage
- ContactType
- VerificationStatus
- ImportStatus
- MailboxStatus

禁止魔法字符串。

---

## 6. 数据规则

1. 所有表必须有：
   - id
   - created_at
   - updated_at
2. 关键业务表建议有：
   - created_by_operator_id
   - updated_by_operator_id
3. 所有删除优先软删除。
4. 所有达人必须支持唯一性判断。
5. 所有邮件必须关联 Campaign Lead。
6. 所有回复必须保留原始内容。
7. AI 分类必须保存：
   - 模型
   - prompt_version
   - confidence
   - 原始结构化结果
8. 所有关键状态变更记录 Audit Log。

---

## 7. AI 开发规则

AI 不直接写数据库最终状态。

正确流程：

```text
AI生成建议
↓
服务层验证
↓
规则引擎校验
↓
写入
```

AI 输出必须使用结构化 JSON Schema。

AI 不允许：
- 虚构达人最近发布内容
- 虚构品牌合作
- 虚构预算
- 虚构指定合作
- 虚构账号数据
- 生成超出已知信息的事实描述

无事实时必须使用泛化表达。

---

## 8. 邮件规则

任何 Campaign 发送前必须检查：

- 邮箱是否有效
- 是否在 suppression_list
- 是否已回复
- 是否退订
- 是否已加微信并设置停止邮件
- 是否已有相同 Campaign 发送记录
- Mailbox 是否正常
- 今日发送额度是否达到上限

Follow-up 发送前重新执行全部检查。

---

## 9. 安全规则

禁止：
- 明文密码
- 明文 SMTP 密码
- API Key 写入 Git
- 前端暴露敏感 Key
- 任意 SQL
- 无 CSRF / Session 防护
- 无速率限制登录接口

部门密码使用 Argon2id。

---

## 10. Git 规则

分支建议：
- main
- develop
- feature/*
- fix/*

提交信息：

```text
feat(auth): add department login
fix(import): handle duplicate xhs ids
refactor(ai): extract provider adapter
test(email): add followup stop tests
docs(prd): update campaign rules
```

---

## 11. 每个 Sprint 结束必须输出

- 已实现功能
- 未实现功能
- 数据库 migration
- 测试结果
- 已知问题
- 下一 Sprint 风险
- 是否满足验收标准

---

## 12. 遇到不明确需求

不要自行发明。

必须：
1. 在代码中标记 TODO
2. 在 `docs/OPEN_QUESTIONS.md` 记录
3. 保持最保守实现
4. 不破坏既有数据结构

---

## 13. 完成定义 Definition of Done

功能只有同时满足以下条件才算完成：

- UI 可操作
- API 可调用
- 数据正确持久化
- 权限正确
- 错误状态可处理
- Audit Log 正确
- 单元测试通过
- 核心集成测试通过
- 验收项通过
- 文档已更新

# 达人智能触达系统 V1.0

> 面向公司内部使用的达人开发、邮件触达、CRM 与数据优化系统。

## 1. 项目目标

将现有人工流程：

灰豚筛选达人 → 导出 Excel → 人工整理 → 人工写邮件 → 人工追发 → 人工记录回复 → 人工统计

改造成：

创建开发任务 → 生成灰豚筛选建议 → 导入 Excel → 自动去重 → AI 分析 → AI 个性化话术 → 邮件发送 → 自动 Follow-up → 回复同步 → AI 分类 → CRM → 自动统计。

## 2. V1 明确边界

### V1 必做
- 部门 + 密码登录
- 登录后选择当前操作人
- 达人采集任务
- 灰豚 Excel/CSV 导入
- 达人去重
- 达人数据库
- 联系方式管理
- AI 达人分析
- AI 个性化切入点
- SOP / Playbook
- Campaign
- 邮件预览与发送
- Follow-up
- Inbox
- AI 回复分类
- CRM
- Analytics
- 操作日志
- 邮箱与系统配置
- Docker 部署

### V1 不做
- 飞书登录
- 飞书作为核心依赖
- 自动绕过灰豚验证码/限制
- 非授权网页破解
- 自动谈判
- 自动定价
- TikTok / Instagram / YouTube 多平台
- Lookalike 达人推荐
- 大模型自主修改 SOP
- 大模型自主修改发送策略
- 无人工控制的高风险全自动回复

## 3. 核心原则

1. 灰豚只是数据入口，不是系统底座。
2. 达人数据是公司长期资产。
3. 邮件、模板、回复、CRM 状态全部可追踪。
4. AI 只能根据已知事实生成内容，不得虚构。
5. 所有系统行为必须留下日志。
6. 邮件触达必须支持退订、退信、停止触达和抑制名单。
7. V1 先跑通最短业务链路，再扩展高级智能。
8. Codex 只负责代码实现，不擅自改变产品规则。

## 4. 技术栈

- Frontend: Next.js + React + TypeScript + Ant Design
- Backend: FastAPI + Python
- Database: PostgreSQL
- Cache / Queue: Redis
- Worker: Celery
- Reverse Proxy: Nginx
- Deployment: Docker Compose
- AI: Provider Adapter
- Email: SMTP / Email Provider Adapter

## 5. 文档入口

建议 Codex 按以下顺序阅读：

1. `CODEX_RULES.md`
2. `PRODUCT_PRD.md`
3. `UI_SPEC.md`
4. `ARCHITECTURE.md`
5. `DATABASE_SCHEMA.md`
6. `API_SPEC.md`
7. `AI_RULES.md`
8. `EMAIL_AUTOMATION.md`
9. `SECURITY_RULES.md`
10. `DEVELOPMENT_PLAN.md`
11. `ACCEPTANCE_CRITERIA.md`

## 6. 推荐开发顺序

- Phase 0：项目初始化、Docker、Next.js、FastAPI、PostgreSQL、Redis、Celery、Nginx、CI
- Phase 1：登录 + 达人采集 + 达人库
- Phase 2：SOP + Campaign + AI 话术
- Phase 3：邮件发送 + Follow-up + Inbox
- Phase 4：CRM + Analytics + 操作日志
- Phase 5：稳定性、权限、备份、压测、上线

## 7. 开发环境

### 前置条件

- Docker Engine 与 Docker Compose
- Node.js 22、pnpm 11
- Python 3.12、uv
- `make`、`curl`、`openssl`

### 一条命令启动

```bash
make dev
```

首次启动会基于 `.env.example` 创建被 Git 忽略的 `.env`，并为本地 PostgreSQL 与 `APP_MASTER_KEY` 生成随机开发值。生产环境必须由 Secret 管理系统注入 `APP_MASTER_KEY`。

统一入口为 `http://localhost:8080`。基础设施接口：

- `GET /health/live`：API 进程存活。
- `GET /health/ready`：PostgreSQL 与 Redis 就绪。

Phase 1A 认证接口：

- `GET /api/v1/departments`：登录页可选的启用部门。
- `POST /api/v1/auth/login`：部门密码登录。
- `GET /api/v1/operators`：当前部门可选操作人。
- `POST /api/v1/auth/select-operator`：选择审计归属，不改变 Session 权限。
- `GET /api/v1/auth/me`、`POST /api/v1/auth/logout`：当前身份与退出。

Phase 1B 采集与导入接口：

- `POST /api/v1/collection-jobs`、`GET /api/v1/collection-jobs`、`GET /api/v1/collection-jobs/{id}`：创建和读取部门采集任务。
- `POST /api/v1/import-jobs`：上传 CSV/XLSX，安全落盘后以 `202 Accepted` 排队解析。
- `GET /api/v1/import-jobs/{id}`、`GET /api/v1/import-jobs/{id}/rows`：轮询 Job，并分页查看标准化数据、原始行、Warning 与 Error。
- `PUT /api/v1/import-jobs/{id}/mapping`：保存字段 Mapping 并生成新的 Preview Revision。
- `POST /api/v1/import-jobs/{id}/preview`：显式重建失效或需刷新的 Preview。
- `POST /api/v1/import-jobs/{id}/confirm`：携带 `preview_revision` 异步确认；同一 Revision 严格幂等。
- `POST /api/v1/import-jobs/{id}/cancel`：取消尚未正式写入的 Import。

Preview Plan 持久化在 Import Row 中。Confirm 会重新运行相同 Planner 并校验 Plan Hash；相关数据发生变化时进入 `preview_stale`，必须由用户查看新 Preview 后再次确认。

### 常用命令

```bash
make lint              # Python/TypeScript lint、格式与类型检查
make test              # Python 与 Web 单元测试
make health            # 七服务状态、HTTP readiness、Celery ping
make migrate           # 执行 Alembic migration
make compose-validate  # 校验 Compose 配置
make down              # 停止服务，保留持久卷
```

PostgreSQL 并发测试和真实灰豚附件测试是显式门控测试，分别要求安全的 `TEST_DATABASE_URL` 和仓库外的 `HUITUN_REAL_SAMPLE_PATH`；默认 `make test` 会跳过这两个外部依赖，不会读取真实文件。

### 首个管理员

数据库迁移完成后，通过容器内的一次性 CLI 创建首个管理部门和 Super Admin。
命令不会提供默认账户或默认密码；未使用 `--password-stdin` 时会安全地交互读取并确认密码。

```bash
docker-compose exec api bootstrap-admin \
  --department-name "管理部门" \
  --operator-name "首位管理员"
```

创建成功后不可再次运行 bootstrap。部门密码使用 Argon2id，Session Cookie 为 HttpOnly，
生产环境通过 `APP_ENV=production` 启用 Secure，并要求运行环境注入 `APP_MASTER_KEY`。

### 后端边界

- `packages/backend_core` 是唯一共享 Python 后端核心包。
- `apps/api` 只负责 HTTP。
- `apps/worker` 只负责 Celery 任务入口。
- Phase 1A/1B 规则只存在于 `backend_core.auth`、`backend_core.audit`、`backend_core.imports` 与 `backend_core.influencers`。
- Phase 1B 只建立导入所需的公司级 Influencer、PlatformAccount、Source State、Contact、Current Metrics 与 Metric Snapshot；不包含 Phase 1C 的完整达人库列表、详情或筛选。
- 当前仍不包含 Campaign、真实 AI、真实邮件、Inbox、CRM、Analytics 或其他平台 Connector。

### 数据与日志

- PostgreSQL、Redis 与 `/data/imports` 使用 Docker named volume。
- PostgreSQL 与 Redis 不映射宿主机端口。
- 原始导入文件使用随机 Storage Key、`0700` 目录与 `0600` 文件权限，并保存 SHA-256；只接受经过扩展名、MIME、内容和 Parser 交叉校验的 CSV/XLSX，默认应用上限为 25 MiB。
- 数据库为每个 Storage Object 保存默认 30 天 `expires_at`，相同 SHA-256 的新上传会延长到期时间。物理清理执行器需在正式部署的定时运维中接入；当前不会错误声称已自动删除到期文件。
- Viewer 只能读取；Upload、Mapping、Preview、Confirm 与 Cancel 均由后端强制要求已选择 Operator、CSRF 和非 Viewer 部门权限。
- 日志禁止包含密码、Token、Provider Key 或 `APP_MASTER_KEY`。

# 达人智能触达系统 V1.0

> 面向公司内部使用的达人开发、邮件触达、CRM 与数据优化系统。

当前状态：**Phase 1A–1C 已部署；Phase 2 Task 1–8 已在开发分支本地实现并通过门禁，尚未 merge 或 deploy。** 当前运行版本仍为 `main@4c973c1`、tag `phase-1c-final`。

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
12. `docs/PHASE_2_SCOPE.md`（当前 Phase 2 唯一实施契约）

## 6. 推荐开发顺序

- Phase 0：项目初始化、Docker、Next.js、FastAPI、PostgreSQL、Redis、Celery、Nginx、CI
- Phase 1：登录 + 达人采集 + 达人库
- Phase 2：灰豚多文件 Bulk Import + 确定性 Screening + Freshness + Refresh Queue
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

Phase 1C 达人库只读接口：

- `GET /api/v1/influencers`：一行一个 Influencer 的分页列表；搜索主体昵称和 active 平台账号名，并支持既有筛选以及 `freshness_status`、`requires_refresh`、`last_huitun_observed_before/after`。响应按 active PlatformAccount 返回 Huitun observed/imported time、状态和 age，并提供达人级最差状态与 requires-refresh 汇总。
- `GET /api/v1/influencers/filter-options`：读取当前可见数据实际使用的 Owner、Source Tag 与 CRM Stage 选项。
- `GET /api/v1/influencers/{influencer_id}`：读取主体、active 平台账号、Contact、来源追溯和真实 Current Metrics。
- `GET /api/v1/influencers/{influencer_id}/metric-snapshots`：按稳定顺序分页读取不可变指标历史。

Phase 2 Task 8 Refresh Queue 接口：

- `POST /api/v1/refresh-queues`：用同一 UTC `as_of` 生成 Department-owned、company-candidate Queue。
- `GET /api/v1/refresh-queues`、`GET /api/v1/refresh-queues/{id}`、`GET /api/v1/refresh-queues/{id}/items`：稳定分页与数据库聚合摘要。
- `POST /api/v1/refresh-queues/{id}/export`：导出只含冻结公开 Identity 的公式注入安全 CSV。
- `POST /api/v1/refresh-queues/{id}/cancel`：原子取消 Queue 与所有 active Items。

Web 入口为 `/influencers`，详情路由为 `/influencers/{id}`。达人库只返回 active 且未软删除的数据，Owner 和 Import 来源部门不是数据 ACL。四个 GET 只要求有效 Session，无 Operator 也可读取且无需 CSRF；`viewer` 的非空 Contact 固定显示 `***`，其他正式角色可读取完整 current Contact。Phase 1B Import Mutation 仍要求已选择 Operator、CSRF 和后端权限。

### 常用命令

```bash
make lint              # Python/TypeScript lint、格式与类型检查
make test              # Python 与 Web 单元测试
make test-freshness-postgres # 显式 PG16 Freshness lineage/query/performance Gate
make test-refresh-queue-postgres # 显式 PG16 Queue migration/concurrency/race/2k Gate
make health            # 七服务状态、HTTP readiness、Celery ping
make migrate           # 执行 Alembic migration
make compose-validate  # 校验 Compose 配置
make down              # 停止服务，保留持久卷
```

PostgreSQL 并发测试和真实灰豚附件测试是显式门控测试，分别要求安全的 `TEST_DATABASE_URL` 和仓库外的 `HUITUN_REAL_SAMPLE_PATH`；默认 `make test` 会跳过这两个外部依赖，不会读取真实文件。

### 首个管理员

数据库迁移完成后，通过容器内的一次性 CLI 创建首个管理部门和 Super Admin。
命令不会提供默认账户或默认密码；它会安全地分别读取并确认 Department 密码和独立的 Operator 密码。使用 `--password-stdin` 时按两行依次读取这两个密码。

```bash
docker-compose exec api bootstrap-admin \
  --department-name "管理部门" \
  --operator-name "首位管理员"
```

创建成功后不可再次运行 bootstrap。两个密码都使用 Argon2id，Session Cookie 为 HttpOnly，
生产环境通过 `APP_ENV=production` 启用 Secure，并要求运行环境注入 `APP_MASTER_KEY`。

从 `0010_permissions_v1_persistence` 升级的现有 Operator 不会获得默认或共享密码，必须在新 API/Web 切流前通过服务器侧精确 ID 命令初始化；命令安全提示输入密码、拒绝重复初始化，并撤销该 Operator 的既有绑定 Session。必须从已记录 digest 的**新 API 制品**启动一次性容器运行命令，不得 `exec` 仍在运行的旧 API 容器：

```bash
docker compose run --rm --no-deps api setup-operator-credential \
  --department-id "<exact-department-uuid>" \
  --operator-id "<exact-operator-uuid>" \
  --require-super-admin
```

`0010 -> 0011` 生产门禁必须严格按以下顺序执行：

1. 记录生产 Department UUID、拟初始化的 Super Admin Operator UUID、当前 Git SHA 和新 API/Web image digest；完成可恢复性验证过的数据库备份。在隔离、一次性的 PostgreSQL 16 生产快照恢复环境中，必须用该**新 API image**从 `0010` 升级到 `0011`，对相同的精确 UUID 运行带 `--require-super-admin` 的初始化并完成 Operator 登录及管理员写入 smoke，然后销毁演练环境；仅运行 `--help` 不算可用管理员路径证明，演练密码也不得复用于生产。
2. 迁移前只读取 `0010` 已存在字段：Alembic 必须精确为 `0010_permissions_v1_persistence`；精确 UUID 联查必须只返回一行，Department/Operator 均为 `active`，Operator 与 DepartmentPermission 均为 `super_admin`。缺失、重复、禁用、角色不符或 UUID 不确定时停止，不得猜测或改用其他 Operator。
3. 进入维护模式，先从负载均衡摘除，再彻底 drain/停止所有旧 API 进程并阻断其直连入口；停止会持有长事务的 worker/scheduler。确认旧 `/api/v1/auth/select-operator` 已无法访问且 PostgreSQL 无未结束长事务后，才允许迁移。`0011` 的 `ALTER TABLE` 需要 `ACCESS EXCLUSIVE` lock；使用经审批的有限 `lock_timeout`，不能立即获得锁时安全失败并继续维护模式，不得无限等待。
4. 仅用记录 digest 的新制品升级到 `0011_operator_auth_p0`。仍保持完全关闭，复核 Alembic head、精确 UUID、active/Super Admin 状态以及目标 `password_hash IS NULL AND credential_version = 0`。
5. 从同一新 API image 的一次性容器运行上面的精确 ID 初始化；`--require-super-admin` 会在持有 Department serialization lock 时再次校验 Department active、DepartmentPermission Super Admin、Operator active 且为 Super Admin。随后只读证明目标为 active、Super Admin、`password_hash IS NOT NULL`、`credential_version = 1`；失败时不得开放流量。迁移到首个凭据完成之间允许没有可登录管理员的唯一前提是系统始终完全处于维护模式，且新制品初始化路径已在步骤 1 验证。
6. 先仅在内网/loopback 启动新 API，完成 Department 登录、精确 Operator 密码认证、旧 Session 拒绝和管理员写入 smoke；再启动同 digest 对应 Web，最后才恢复外部流量。任何旧 API 进程均不得与 `0011` 数据库同时对外服务。

无论是否已初始化 Operator 凭据，**应用单独回滚到包含免密码 Operator 选择的旧版本始终禁止**。初始化前只有在完全维护模式且 guard 确认没有凭据时才可考虑 schema downgrade；初始化后 `0011` downgrade 必须拒绝。任一失败均保持维护模式并前向修复。完整数据库备份恢复只是最后手段；恢复后也必须先部署已修复的新认证制品并完成上述 smoke，才能重新开放流量。

### 后端边界

- `packages/backend_core` 是唯一共享 Python 后端核心包。
- `apps/api` 只负责 HTTP。
- `apps/worker` 只负责 Celery 任务入口。
- Phase 1A 至 Phase 1C 的规则只存在于 `backend_core.auth`、`backend_core.audit`、`backend_core.imports` 与 `backend_core.influencers`。
- Phase 1C 复用 `0003_phase1b` 的公司级 Influencer、PlatformAccount、Source State、Contact、Current Metrics 与 Metric Snapshot，实现只读查询层、四个 GET 和 Web 列表/详情；没有 `0004`、Schema 变更或 Influencer 写接口。
- Phase 2 Task 1–8 已在开发分支实现，Alembic head 为 `0005_phase2_refresh_queue`；`0004` 未被 Task 8 修改，未创建 `0006`。
- Phase 2 继续只在 `backend_core.imports`、`backend_core.influencers` 与 `backend_core.refresh` 扩展业务规则，不创建 `ImportBatch`、`BatchRow`、根目录 `services/` 或 API 内重复 Service。
- 当前仍不包含 Campaign、真实 AI、真实邮件、Inbox、CRM、Analytics 或其他平台 Connector。

### 数据与日志

- PostgreSQL、Redis 与 `/data/imports` 使用 Docker named volume。
- PostgreSQL 与 Redis 不映射宿主机端口。
- 原始导入文件使用随机 Storage Key、`0700` 目录与 `0600` 文件权限，并保存 SHA-256；只接受经过扩展名、MIME、内容和 Parser 交叉校验的 CSV/XLSX，默认应用上限为 25 MiB。
- 数据库为每个 Storage Object 保存默认 30 天 `expires_at`，相同 SHA-256 的新上传会延长到期时间。任何仍被 Import lineage 引用的文件不得物理删除；`expires_at` 不是破坏审计链的授权。Phase 2 MVP 不实现 archive/delete lifecycle。
- 达人库四个 GET 允许所有有效 Session 公司级读取，且不要求已选择 Operator；Viewer Contact 由后端固定脱敏。Upload、Mapping、Preview、Confirm 与 Cancel 仍由后端强制要求已选择 Operator、CSRF 和非 Viewer 部门权限。
- 日志禁止包含密码、Token、Provider Key 或 `APP_MASTER_KEY`。

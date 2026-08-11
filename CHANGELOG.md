# Changelog

本文档记录达人智能触达系统的可交付变更。

## [Unreleased]

### Phase 1C

#### Added

- 在唯一业务核心 `packages/backend_core` 中新增达人库只读 Schema、Repository 和 Service，复用 Phase 1B 的 Influencer、PlatformAccount、Source、Contact、Current Metrics 与不可变 Metric Snapshot 数据模型。
- 新增四个只读接口：达人列表、筛选选项、达人详情和指标历史；支持一行一个 Influencer 的稳定分页、昵称搜索，以及赛道、粉丝范围、负责人和 CRM Stage 筛选。
- 新增 Web 达人列表 `/influencers` 与详情 `/influencers/{id}`，展示平台账号、完整来源追溯、真实当前指标、联系方式和分页历史快照，不创建 AI、触达或 CRM 占位模块。

#### Security and Boundaries

- 达人库为公司级共享；四个 GET 只要求有效 Session，不按 Owner 或 Import Department 分片，也不要求已选择 Operator。权限仍取自 `DepartmentPermission`，所选 Operator 不会提升 Session 权限。
- `super_admin`、`manager`、`operator` 可读取完整 current Contact；`viewer` 仅收到固定脱敏值 `***`。Contact 原文和 `normalized_value` 不进入日志、console、埋点、错误响应或 Audit。
- 列表和详情只返回 active 且未软删除的 Influencer；Phase 1C 没有 Influencer Mutation、CSRF 写流程、新 AuditAction、Schema 变更或 `0004` migration，CRM Stage 仅展示和筛选。

#### Verification

- `make lint` 全部通过：Ruff、Black（103 个文件）、backend_core/API/Worker mypy、ESLint、TypeScript 与 Prettier 均通过。
- `make test` 全部通过：backend_core/integration/smoke 为 133 passed、3 个外部门控按设计跳过；API 9 passed；Worker 5 passed；Web 28 passed。3 个外部门控已分别显式执行：PostgreSQL 16 Repository/Import 9 passed，仓库外真实灰豚附件 1 passed。
- PostgreSQL 16 验证了 JSONB Followers 安全类型处理、同一指标行范围语义、fan-out 去重、Tag、ILIKE wildcard、稳定排序和 Snapshot 历史；现有 `0001` 至 `0003_phase1b` 通过 fresh、repeat、downgrade/re-upgrade、完整 base 循环、metadata 与 `alembic check`，且未创建 `0004`。
- classic Docker builder 完成最新镜像构建；默认栈与独立隔离栈的七服务均健康，Celery 仍只注册既有 Import 与 Phase 0 任务，PostgreSQL/Redis 无宿主公开端口。
- 真实 Nginx/API/浏览器 E2E 通过导入、Preview、Confirm、列表、全部冻结筛选、详情、Viewer 无 Operator 读取与 Contact 脱敏；后续 Newer/Same/Older/Unknown 导入证明 Current Metrics、人工 Owner/Contact 和不可变 Snapshot 的非破坏性规则保持正确。
- PostgreSQL 数据与 Import Storage Object 在七服务重启后保持相同计数和 SHA-256。Docker buildx 缺失继续作为正式部署前事项，不阻塞 Phase 1C。

### Phase 1B

#### Added

- 新增 `0003_phase1b` migration，以及 Collection Job、Stored Import File、Import Job/Row、公司级 Influencer、PlatformAccount、Source Identity/State、Contact、Current Metrics 与不可变 Metric Snapshot。
- 新增安全 CSV/XLSX Storage 与 Parser、37 列灰豚 CSV/Excel Adapter、显式 Generic Mapping 和平台无关 Canonical Contract。
- 新增持久化 Preview Plan、递增 Revision、稳定 Plan Hash、非破坏性 Freshness、公司级硬身份去重、Email 疑似重复和原子异步 Confirm。
- 新增 Collection/Import API、Celery `import` 队列入口，以及创建任务、上传、Mapping、Preview、行级详情、Confirm、Stale 重建与结果 UI。
- 新增仓库外真实附件门控测试和 PostgreSQL 并发门控测试；真实文件与敏感行不进入仓库或测试输出。

#### Security

- 文件默认硬限制 25 MiB；交叉校验扩展名、MIME、magic/content 与 Parser，并限制行、列、Cell、XLSX entry、解压体积和压缩比。
- 拒绝宏、ActiveX、外部关系、加密/伪装 Office 文件、路径穿越、ZIP bomb、危险 XML 与公式 Header；数据公式不执行并产生行级 Warning。
- `/data/imports` 使用随机 Storage Key、`0700` 目录、`0600` 文件、原子写入、SHA-256 与读取完整性复核；Nginx 预留 multipart 开销，应用仍执行最终文件上限。
- 所有 Mutation 强制认证、已选择 Operator、CSRF 与后端 RBAC；Viewer 只读，普通角色限部门，Super Admin 可跨部门且 Operator 不改变 Session 权限。
- Audit 与异常响应不记录密码、Token、Secret 或 Contact 原文；Worker 未知异常转换为稳定错误，避免数据库参数进入任务日志。

#### Fixed

- FormData 请求不再错误设置 JSON Content-Type，浏览器可生成正确 multipart boundary；非 JSON 的 413 网关响应会映射为稳定文件超限错误。
- Database 在实例化时延迟注册完整 ORM graph，确保独立 Celery Worker 写跨域 Audit 时可解析 Department/Operator 外键，同时避免 `bootstrap-admin` 循环导入。
- 重复看到同源 Contact 时保留业务 `NO_CHANGE`，不重复创建，只更新 last seen/import 审计来源；manual Contact 仍不可变。
- Prettier 忽略 `.next` 等生成目录，生产构建后再次执行 `make lint` 不再误报生成物。

#### Verification

- `make lint` 全部通过；默认门禁 81 项测试通过，真实 50 行附件门控另 1 项通过，隔离 PostgreSQL 并发门控另 2 项通过。
- `0003_phase1b` 在 PostgreSQL 16 上通过首次、重复、降级后再升级、完整 base 循环、ORM metadata 创建与 `alembic check`；默认 Compose 栈当前为 `0003_phase1b (head)`。
- 最新 Docker 镜像构建通过；七服务健康，Nginx Web/API 路由、request ID、未认证 401、Celery ping/任务注册和 PostgreSQL/Redis 无宿主端口均完成验证。
- 独立 Compose E2E 通过登录、Operator、脱敏上传、真实 Celery Parse、Preview、Confirm、重复 Confirm；数据库结果为 2 Influencer、2 PlatformAccount、1 Contact、2 Snapshot。
- PostgreSQL 与实际 Import Storage Object 在容器重启后保持数据和 SHA-256；隔离测试项目及测试卷已清理。
- Docker buildx 缺失继续记录为正式部署前事项，不阻塞 Phase 1B。

### Phase 1A

#### Added

- 新增 Department、部门级权限、Operator、Session 与 Audit Log 数据模型及 `0002_phase1a_auth` migration。
- 新增部门密码登录、操作人选择、当前身份、退出和管理员重置部门密码 API。
- 新增一次性 `bootstrap-admin` CLI，不包含默认账户或默认密码。
- 新增登录、操作人选择与最小登录成功 Web UI。
- 新增 Argon2id 密码、Redis 部门/IP 锁定、Session/CSRF Token 哈希与双提交 CSRF 防护。

#### Security

- Session Token 只以 SHA-256 hash 保存；Session Cookie 为 HttpOnly、SameSite=Lax，生产环境启用 Secure。
- 选择 Operator 只改变 Audit 归属；权限始终取自 Department，无法通过高角色 Operator 提权。
- 登录连续五次失败锁定五分钟；成功登录清除对应 Department + IP 失败状态。
- 密码重置在同一事务中撤销目标 Department 的所有 Session，并记录 Audit。
- Nginx 覆盖不可信 `X-Forwarded-For`，防止客户端伪造 IP 绕过登录锁定。
- API 校验错误不回显原始输入，避免无效密码出现在响应或诊断内容中。

#### Verification

- Phase 1A 核心、HTTP 与 Web 自动化测试覆盖登录、锁定、Session、CSRF、权限不可提升和 Audit。
- Docker 镜像构建通过，七服务健康；Alembic 首次及重复升级均保持 `0002_phase1a (head)`。
- Docker buildx 缺失继续记录为正式部署前事项，不阻塞本阶段。

## [0.1.0] - 2026-08-10

### Added

- 初始化 monorepo、Docker Compose、Next.js、FastAPI、PostgreSQL、Redis、Celery 与 Nginx 工程底座。
- 建立唯一共享 Python 核心包 `packages/backend_core`。
- 建立 lint、类型检查、测试、健康检查、迁移和 CI 基线。
- 记录已确认的产品/技术决策及仍待外部输入的问题。
- 新增 `/health/live` 与 `/health/ready` 基础设施接口。
- 新增空的 `0001_phase0` Alembic baseline migration。

### Security

- 应用容器使用非 root 用户运行，PostgreSQL 与 Redis 只存在于内部网络。
- 本地开发 Secret 自动随机生成；生产 `APP_MASTER_KEY` 必须从环境 Secret 注入。
- 结构化日志对密码、Token、凭证和 Key 字段进行递归脱敏。

### Verification

- Python lint、Black、mypy、ESLint、Prettier 和 TypeScript 检查已纳入 `make lint`。
- Python、API、Worker 和 Web 测试已纳入 `make test`。
- 七服务状态、HTTP readiness 与 Celery ping 已纳入 `make health`。
- Phase 0 共 7 项测试通过；PostgreSQL 不可用时 readiness 正确返回 503。
- Alembic baseline 首次升级与重复升级均通过，并在 PostgreSQL 容器重启后保持 `0001_phase0`。
- API、Worker、Scheduler、Web 均以非 root 用户运行；PostgreSQL 与 Redis 无宿主机端口映射。

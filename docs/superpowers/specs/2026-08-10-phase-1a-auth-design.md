# Phase 1A Auth Design

状态：已批准（用户于 2026-08-10 直接提供并要求实施）

## 范围

只实现 Department 登录、Operator 审计归属、Session、部门级 RBAC 基础、Audit、Bootstrap Admin 和最小登录 UI。不实现导入、达人、Campaign、AI、邮件、CRM 或 Dashboard 数据。

## 数据与授权

- `departments` 严格保存名称、Argon2id 密码哈希、状态、Session 配置和时间戳。
- `operators` 属于 Department，并保留 V1 Role；Operator Role 只用于人员信息和未来权限矩阵，不决定当前 Session 权限。
- `department_permissions` 保存部门级授权 Role。认证依赖只读取 Department 权限，选择 Operator 只能更新 Session 的 `operator_id`。
- `sessions` 只保存随机 Session Token 和 CSRF Token 的 SHA-256 哈希；支持多设备、过期与撤销。
- `audit_logs` 保存动作、结果、Department、Operator、IP、User-Agent 和时间，不保存密码、Token 或凭证。

## 登录与安全

- 登录失败计数使用 Redis，以 Department ID 与客户端 IP 的不可逆组合键计数；第五次失败锁定五分钟，成功后清除。
- `remember_me=false` 固定 12 小时，`true` 固定 30 天。
- Session 使用 HttpOnly、SameSite=Lax Cookie；生产环境启用 Secure。
- CSRF 使用双提交 Cookie，并将 CSRF Token 哈希保存在 Session。所有已认证的状态修改接口必须提交匹配的 `X-CSRF-Token`。
- 部门密码重置要求 Super Admin 部门权限，并在同一事务中撤销目标 Department 的全部 Session。

## API 与 UI

- 公共：`GET /api/v1/departments`、`POST /api/v1/auth/login`。
- 已认证：`GET /api/v1/auth/me`、`POST /api/v1/auth/logout`、`GET /api/v1/operators`、`POST /api/v1/auth/select-operator`。
- 管理：`POST /api/v1/admin/departments/{id}/reset-password`。
- CLI：`bootstrap-admin` 通过交互式密码或 stdin 创建唯一首个管理 Department、Super Admin Operator 和部门级 Super Admin 权限；无默认账户或密码。
- UI 只提供登录、选择 Operator、登录成功状态和退出。

## 错误与测试

- 未认证返回 401，权限不足返回 403，CSRF 失败返回 403，锁定返回 423；错误继续使用统一 Envelope。
- 覆盖正确/错误登录、锁定及到期、12 小时/30 天、Token 哈希、过期、退出、密码重置撤销、停用部门、Operator 选择、权限不提升、Audit、未认证拒绝和 CSRF。
- Migration 只新增 Phase 1A 表、Enum、索引和约束，不创建 Phase 1B 业务表。


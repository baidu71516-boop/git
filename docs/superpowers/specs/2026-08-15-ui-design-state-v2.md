# 2026-08-15 UI Design State v2 Checkpoint

此文档为下一轮 UI 设计会话的唯一 checkpoint，基于当前仓库真实实现与 UI-1～UI-5 已冻结内容，供后续会话直接接续。此文档仅描述已冻结界面状态，不复盘实现过程。

## 1. 产品定位

- 公司内部达人智能触达系统
- 中文优先（文案、字段与状态文案以中文为主）
- 高信息密度（数据真实字段展示、默认不隐含解释）
- 真实能力优先（仅展示后端真实能力，禁止文案补齐）
- 内部 SaaS 后台，不是营销网站

## 2. 当前技术栈

- Next.js `16.3.0`
- React `19.2.8`
- TypeScript `5.9.3`
- Ant Design `6.5.4`
- React Query `5.101.4`

## 3. Git / Worktree / 风险约束

- UI worktree：`/Users/mac/Downloads/influencer_outreach_ui`（当前 `ui-redesign`）
- Backend worktree：`/Users/mac/Downloads/influencer_outreach_integration`（当前 `phase-2-integration`）
- 辅助工作树（实现/历史）：`/Users/mac/Downloads/influencer_outreach_project`
- 当前禁止：
  - 不启动未完成 Backend 的功能（尤其是 Task 10 及以后）
  - 不做 UI 与后端未实现能力的 UI 化
  - 不混用 `dirty working tree`；若有脏改动，应先 STOP（本次会话文档提交前先确认工作树状态）

## 4. Design Tokens

### 4.1 视觉主变量

- `--app-primary: #1677ff`
- `--app-primary-dark: #102a56`
- `--app-page: #f3f6fb`
- `--app-surface: #ffffff`
- `--app-border: #e2e8f0`
- `--app-text: #172033`
- `--app-text-secondary: #667085`
- `--app-success: #16a34a`
- `--app-warning: #d97706`
- `--app-danger: #dc2626`
- `--app-shadow: 0 8px 24px rgb(25 42 70 / 6%)`

### 4.2 Layout / Radius / Surface

- `--app-radius: 10px`
- Sidebar 宽度 `--app-sidebar-width: 232px`
- AppShell 侧边栏/容器均采用 App-wide 统一背景与边界颜色，不引入新色板
- 表单控件圆角（Button/Input/Select）当前实际为 `8px`，Card 某些场景为 `12px`

### 4.3 Spacing 原则

- 基础步进以 `4` 与 `8` 的倍数为主（8/10/12/14/16/22/24/28/32/48）
- 页面级主内容 padding：`28px 32px 48px`
- Sidebar 内边距与标题高度采用固定比例，不引入断崖式间距

## 5. UI-1 Freeze

- App Shell（`AuthShell + AppShell`）保持不变
- Sidebar 按三组入口与固定样式渲染
  - 达人库：`/influencers`
  - 数据采集：`/`
  - 数据更新：`/refresh-queues`
- Topbar 固定主标题、面包屑、用户菜单区域
- 当前正式导航仅保留上述路径（无新 Tab、无占位入口）

## 6. UI-2 Freeze

- 达人库页面与表格列为冻结态：
  - 达人 / 平台 / 标签 / 粉丝 / 联系方式 / CRM Stage / 负责人
- 搜索语义：`q` + 标签 + 粉丝区间 + 负责人 + 阶段 + freshness / refresh 筛选
- 粉丝格式：
  - `<10_000` 显示整数
  - `>=10_000` 显示“万”并带缩写（展示真实 follower）
- Freshness 筛选后的最终布局：
  - freshness 标签 + `需要更新` 标记 + 明确时间展示规则，不新增营销化文案

## 7. UI-3 Freeze

- Drawer 与 canonical detail URL 都冻结：
  - 详情 URL：`/influencers/{id}`（canonical）
  - Next.js Intercepting Route：`/influencers/@drawer/(.)[id]`
- Avatar 规则冻结：
  - 有 URL 用真实头像 URL
  - 无 URL 时基于中文名（无则首字符）做稳定 fallback
  - 颜色与首字母固定可重复生成，不与真实姓名直接脱敏映射
- 敏感信息权限冻结：列表/详情按 `viewer` 与正式角色分层展示（详见 §14）

## 8. UI-4 Freeze

- 单文件导入界面不再改造（已冻结在已有行为）
- 批量文件处理：
  - 文件上传、映射、预览、确认、结果区全部按现有多文件链路
- Unified Preview 保持统一入口与汇总视图
- Screening 与 Change Summary 按冻结语义显示
- Confirm 为业务提交动作（单入口）
- 原子写入与失败恢复遵循后端 Task6 实现
- 完成结果使用真实接口状态与持久化反馈

## 9. UI-5A Freeze（数据时效）

- 字段：
  - `freshness_status` 映射
    - fresh → `新鲜`
    - aging → `较旧`
    - stale → `陈旧`
    - very_stale → `严重陈旧`
    - unknown → `未知`
  - `requires_refresh`：是否需要更新（布尔）
  - `freshness_age_days`：时效天数
- 时间阈值遵循现有策略（7/30/90）及 unknown 映射规则
- `指标更新时间` 与 `freshness` 不合并：
  - `freshness` 来源与展示依赖确认过的 Huitun 采集观察时间语义
  - `source_updated_at` 不可作为唯一 freshness 替代来源

## 10. UI-5B Freeze（数据更新 / Refresh Queue）

- 数据更新入口与列表：`/refresh-queues`
- 数据更新名单创建（Create）与创建参数仅在冻结范围内可见
- Queue list / Item list：
  - queue status：open / exported / completed / cancelled
  - item status：pending / fulfilled_changed / fulfilled_no_change / stale_return / unresolved / cancelled
- CSV 导出：通过列表动作执行导出
- Cancel：仅在前置状态允许
- Queue/Return status 中文映射：
  - Queue：待导出、已导出、已完成、已取消
  - Item：待回流、已回流·有变更、已回流·无变更、回流数据已过期、无法确认、已取消

## 11. UI-5C Freeze（回流闭环完整流）

闭环顺序：

1. 数据时效
2. 数据更新名单
3. 导出 CSV
4. 外部重新获取数据
5. 继续处理回流数据
6. 数据采集
7. 回流 Preview
8. Confirm
9. 原子 reconciliation
10. Queue 最终状态

闭环约束：

- `refresh_queue_id` 只在 Bulk Job 创建时绑定
- 普通 Bulk Import 不变（无 `refresh_queue_id`、不产生 return 绑定证据）
- 回流 Preview 为“预计回流结果”（不是最终结果）
- Confirm 后以 `GET /refresh-queues/{id}` 与 `GET /refresh-queues/{id}/items` 为最终权威
- `stale_return / unresolved` 不阻塞其他已匹配的有效项推进
- 未出现 per-item retry、无手工 resolve、无自动 Confirm

## 12. 时间规则

- 统一展示时区基准：`Asia/Shanghai`
- 后端时间展示与后端来源时间优先保留 UTC，再按区域显示
- `Z/offset` 时间可按固定格式展示到分钟；`formatBulkDateTime` 使用上海时区转换
- naive datetime（如 `YYYY-MM-DD HH:MM`）仅作为本地显示文本，不在前端自行强行补时区转换导致错配
- 不混淆以下时间来源：
  - 数据取得时间（`source_acquired_at`）
  - 指标更新时间（`source_updated_at`）
  - 资料更新时间（资料快照时间）
  - Queue 基准时间（`as_of`）
  - 完成时间（`completed_at`）

## 13. 中文 UI 规则

- 全局文案、状态标签、按钮文案、空值文案统一中文
- 列表列标题与字段注释中文化，英文仅用于技术字段名展示（如需）
- 关键状态保持中文可读词（例如“待更新”“待回流”“已完成”）

## 14. 敏感数据规则

- `viewer` 角色只读脱敏 Contact（文案显示 `***`）
- 非 viewer（三类正式角色）在授权范围内显示完整 current contact
- Preview/变更展示中的敏感内容做内嵌脱敏处理，避免原始 contact 与 secret 类字段泄露
- 敏感字段不得进入日志/错误提示/控制台输出
- 联系方式无值时显示 `—`，不要把脱敏误判为缺失

## 15. Hallmark-adapted 15 条审核原则（精简）

- 真实性：不允许虚构 API 字段，不引入未实现行为；只显示确证结果
- 稳定性：同字段/同入口跨页面行为一致（list/detail/drawer）
- 回退安全：异常时回到可恢复状态，避免状态跳转失真

- 结构性：主导航、topbar、抽屉三层结构固定，不引入新入口
- 可读性：高信息密度下保留字段语义，减少装饰性噪音
- 可执行性：每一步操作必须有可验证后端结果

- 边界清晰：Frozen 模块不与未冻结模块混合改造
- 冗余控制：新增字段必须有来源契约，不重复显示重复计算值
- 复用优先：尽量复用既有组件与已有 token，不起新 UI 组件风格

- 保守展示：缺失/0/false 分离展示，时间与空值不混淆
- 风险隔离：敏感数据优先脱敏，禁止误披露
- 不猜测：Preview、排序、筛选仅按真实后端契约展示

- 受控推进：每个阶段只接管已冻结范围的职责，新增入口必须先通过冻结复核
- 时区一致：统一 `Asia/Shanghai` 展示策略
- 结果可复核：关键状态与结果可通过 API 回读验证

- 变更可追溯：关闭未实现模块，变更点附带版本与提交来源
- 不重叠审计：同一能力不在多套规则下反复解释
- 责任边界：UI、API、状态机、权限分离归责

## 16. Model 使用规则

- API / 状态机 / Route / 权限：`GPT-5.6 Sol`
- Visual Polish / Screenshot / spacing / responsive：`GPT-5.3-Codex-Spark`

## 17. Development Preview Harness

现有可用预览路由（development-only）：

- `/dev-ui-preview/influencers`
- `/dev-ui-preview/influencers/drawer`
- `/dev-ui-preview/influencers/drawer/[id]`
- `/dev-ui-preview/data-collection`
- `/dev-ui-preview/refresh-queues`
- `/dev-ui-preview/refresh-queues/[status]`

安全规则：

- 非 development 环境直接 `notFound()`（不提供可见页）
- 预览为内存静态数据，不进入正式导航
- 不进行真实 Auth/API/DB 网络请求（零请求）
- 不作为正式发布路径，不替代正式 `/influencers`、`/`、`/refresh-queues`

## 18. 当前明确禁止伪造的未来能力

以下能力不得在 UI 中预置（除非未来 Capability Audit 证明 backend 已 ready）：

- 不存在的 Campaign
- Inbox
- Email
- AI 意向 / 回复
- Timeline
- 导入历史

## 19. 当前 Git checkpoint（自动抓取）

### 19.1 当前 UI Worktree

- 分支：`ui-redesign`
- HEAD：`d193993ed75251fa9e7555c3f466fc42a0f02a3b`

### 19.2 最近 UI-5 相关 commits（ui-redesign）

- `d193993` style(web): polish refresh return workflow
- `84d4a3c` chore(web): add refresh return visual preview
- `d5c9a7b` feat(web): add refresh return workflow
- `a098e7d` merge: align ui with Task 9 refresh return backend
- `0d25025` feat: reconcile Phase 2 refresh returns（来自 phase-2-bulk-import）
- `c643d7e` feat: add Phase 2 refresh queues
- `f5ecaa8` feat: add Phase 2 influencer freshness
- `b51c9b3` feat: add Phase 2 atomic bulk confirm

### 19.3 当前 Backend Task 9 baseline / merge（backend worktree）

- 后端分支：`phase-2-integration`
- HEAD：`37513ef008092f5619ab87dccbc2575ef7232696`（`fix(web): align Phase 2 backend contracts`）
- 最近 merge：`8fa3f3f merge: integrate frozen UI redesign`
- 任务锚点 commit：`0d25025`（`feat: reconcile Phase 2 refresh returns`）
- 变更基线记录来源：`CHANGELOG.md` Unreleased 明确记为 `Phase 2 Task 9 — Refresh Return Reconciliation`

## 20. 下一阶段启动规则

下一模块必须按顺序：

1. Capability Audit
2. Design Freeze
3. Sol 实现真实 contract（API/状态机/Route/权限）
4. Spark Visual Polish
5. 浏览器截图验收
6. Freeze

- UI-1 ～ UI-5 不再重复审计。
- 仅当出现新 Backend commit 或出现明确 contract 冲突时，才允许对应冻结项重审。

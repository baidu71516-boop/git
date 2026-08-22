# 2026-08-20 UI Design State v5 Checkpoint

此文档是后续 UI 工作使用的唯一 UI Design State checkpoint。它独立包含 UI-1～UI-7 的有效冻结约束、Phase 3A 已冻结 contract、Phase 3 UI-1「今日触达」Final Freeze、Phase 3 UI-2 的当前选择与 Campaign UI-2A Final Freeze、预览与质量门，以及下一模块启动边界。

本文档只记录当前冻结结果与推进边界，不复盘实现过程，不重新审计 UI-1～UI-7。UI-2A 已完成并冻结；UI-2 Overall 仍为 IN PROGRESS，因为 UI-2B 尚未完成。

## 1. 文档定位与当前基线

### 1.1 产品定位

- 公司内部达人智能触达系统。
- 中文优先，文案、字段与状态文案以中文为主。
- 高信息密度，优先展示真实字段与可验证状态。
- 真实 capability 优先，不用 UI 文案补齐未实现能力。
- 内部 SaaS 工作台，不是营销网站。

### 1.2 技术基线

- Next.js `16.3.0`
- React `19.2.8`
- TypeScript `5.9.3`
- Ant Design `6.5.4`
- React Query `5.101.4`

### 1.3 Git / Worktree 基线

- UI worktree：`/Users/mac/Downloads/influencer_outreach_ui`
- 分支：`ui-redesign`
- 本 checkpoint 的 committed baseline / 当前 `ui-redesign` HEAD：`01d133b5190ae0655ff6b190e61c846c71c949db`
- 当前 worktree 中已有用户的 5 个 tracked modified 与 9 个 untracked 文件属于本 checkpoint 之外的内容，必须原样保留，不得纳入本文档 commit。
- v5 只允许新增自身文件；不修改产品代码、测试、Backend、migration 或已有 dirty/untracked 文件。

本文档只新增自身文件；不修改产品代码、测试、Backend、migration 或已有 dirty/untracked 文件。

### 1.4 Design Tokens

视觉主变量：

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

布局与表面：

- `--app-radius: 10px`
- Sidebar 宽度：`--app-sidebar-width: 232px`
- 页面、Sidebar、容器使用既有背景与边界色，不引入新色板。
- Button/Input/Select 圆角为 `8px`；Card 依据既有组件使用 `10px` 或 `12px`。
- 基础间距以 `4` 与 `8` 的倍数为主：`8/10/12/14/16/22/24/28/32/48`。
- 页面主内容 padding：`28px 32px 48px`。

## 2. UI-1～UI-7 冻结继承

UI-1～UI-7 全部为 `Frozen`。除非出现新的 Backend contract delta 或明确 contract 冲突，不重新审计这些冻结项。

### 2.1 UI-1 Freeze：App Shell 与主导航

- `AuthShell + AppShell`、Topbar、Sidebar、用户菜单及三层结构保持冻结。
- Sidebar 仅保留以下正式入口：
  - 工作区：今日触达、达人库。
  - 数据：数据采集、导入记录、数据更新。
- 正式 route：
  - 今日触达：`/outreach/today`
  - 达人库：`/influencers`
  - 数据采集：`/`
  - 导入记录：`/import-jobs`
  - 数据更新：`/refresh-queues`
- 不新增 Campaign、Inbox、Email、AI 等占位 Sidebar 入口。
- App-wide token、中文文案、桌面端高密度布局与既有响应式回退保持不变。

### 2.2 UI-2 Freeze：达人库

- 冻结列：达人、平台、标签、粉丝、联系方式、CRM Stage、负责人。
- 搜索语义：`q`、标签、粉丝区间、负责人、阶段、freshness / refresh 筛选。
- 粉丝展示：小于 `10_000` 显示整数；大于等于 `10_000` 显示带缩写的“万”，详情或 Tooltip 保留真实整数。
- freshness 结果同时展示 freshness 标签、`需要更新` 标记与明确时间，不引入营销化文案。

### 2.3 UI-3 Freeze：详情 Drawer 与 canonical detail

- canonical detail URL：`/influencers/{id}`。
- Drawer route：`/influencers/@drawer/(.)[id]`。
- 有头像 URL 时使用真实头像；无 URL 时基于中文名或首字符生成稳定 fallback，颜色可重复生成。
- 列表、详情、Drawer 按 `viewer` 与正式角色分层展示敏感联系方式。

### 2.4 UI-4 Freeze：导入与批量文件处理

- 单文件导入界面按既有行为冻结，不进行重新设计。
- 批量文件链路固定为：文件上传、字段映射、预览、确认、结果。
- Unified Preview 是统一入口与汇总视图。
- Screening、Change Summary、Confirm、原子写入、失败恢复与持久化反馈遵循已冻结语义。
- Confirm 是业务提交动作，保持单入口；不提供 Auto Confirm。
- 多文件中存在 blocking file 时，在 replace / exclude / retry 前禁止统一 Preview。
- Preview 首屏展示 Raw、Unique、Duplicate、Existing、New、Changed、No change、Warnings、Errors。
- 行数据使用服务端分页，不渲染全部大批量数据。

### 2.5 UI-5A Freeze：数据时效

`freshness_status` 映射固定为：

- `fresh` → 新鲜
- `aging` → 较旧
- `stale` → 陈旧
- `very_stale` → 严重陈旧
- `unknown` → 未知

同时保留 `requires_refresh` 与 `freshness_age_days` 的真实语义。`指标更新时间` 与 freshness 不合并；freshness 依赖已确认的 Huitun 采集观察时间语义，`source_updated_at` 不作为唯一替代来源。时间阈值遵循既有 `7/30/90` 策略。

### 2.6 UI-5B / UI-5C Freeze：数据更新与回流闭环

正式入口：`/refresh-queues`。

Queue status：`open / exported / completed / cancelled`，中文为：待导出、已导出、已完成、已取消。

Item status：`pending / fulfilled_changed / fulfilled_no_change / stale_return / unresolved / cancelled`，中文为：待回流、已回流·有变更、已回流·无变更、回流数据已过期、无法确认、已取消。

允许的闭环顺序：

1. 数据时效。
2. 数据更新名单。
3. 导出 CSV。
4. 外部重新获取数据。
5. 继续处理回流数据。
6. 数据采集。
7. 回流 Preview。
8. Confirm。
9. 原子 reconciliation。
10. Queue 最终状态。

冻结约束：

- `refresh_queue_id` 只在 Bulk Job 创建时绑定。
- 普通 Bulk Import 不绑定 `refresh_queue_id`，也不产生 return 绑定证据。
- 回流 Preview 是预计结果，不是最终结果。
- Confirm 后以 Queue 与 Item API 回读为最终权威。
- `stale_return / unresolved` 不阻塞其他已匹配有效项推进。
- 不提供 per-item retry、手工 resolve 或自动 Confirm。

### 2.7 UI-6 Freeze：采集任务筛选规则

- 入口位于“数据采集 → 当前采集任务”，不新增 Sidebar、route 或顶层 Tab。
- 有编辑权限显示“编辑筛选规则”；Viewer 显示“查看筛选规则”，Viewer 使用只读展示而不是禁用表单。
- 摘要展示平台、来源标签数量、粉丝范围与规则版本。
- 当前真实平台为“小红书”。来源标签使用 `source_tags_exact_any` 精确 ANY 匹配。
- 保存调用真实 `PUT screening-rules` endpoint，必须携带 `expected_revision`。
- 409 不自动重试或 destructive 覆盖，提示：`筛选规则已被其他人更新。请重新加载最新规则后再继续编辑。`
- 操作按钮为“重新加载最新规则”。
- `preview_ready` 保存前提示：`保存后，当前数据预览将失效，需要重新生成。`
- 保存成功后只 refetch，不自动 rebuild，不自动 Confirm。
- Preview 场景包括：筛选规则·可编辑、筛选规则·已有数据预览、筛选规则·规则冲突、筛选规则·只读。

### 2.8 UI-7 Freeze：导入记录

- 正式入口为 `/import-jobs`，development-only preview 为 `/dev-ui-preview/import-jobs`。
- 生产列表继续使用真实 React Query 数据流；preview 使用内存 fixture，不访问 Auth/API/DB。
- 列表、空态、加载态、错误态、状态/结果 presenter 与 640px 详情 Drawer 为冻结结构。
- 预览场景覆盖列表、详情、空状态、加载与失败；场景选择使用单一中文下拉，不使用横向 Tab。
- Fixture 覆盖 completed、importing、preview rebuild required、failed、cancelled。
- 完成任务显示真实持久化结果计数；进行中或非完成状态中不存在的最终字段显示 `—`。
- 详情 Drawer 展示状态、来源、创建/完成时间、可用处理结果、低优先级 task ID，以及必要的可读错误问题。
- 错误不展示 stack trace 或 raw JSON。
- UI-7 不增加 retry、cancel、re-import、delete、export 或 continue-processing 操作。

## 3. Phase 3 总体原则

Phase 3 = 销售增长引擎。

核心目标：

更快找到值得联系的人
→ 更快完成触达
→ 提高有效回复

Phase 3 当前坚持：

- Contract 先于 UI。
- 真实 capability 先于页面。
- 只把已冻结、可验证的后端能力交给 UI。
- 不提前伪造 Email、Sequence、Inbox、AI 或自动化触达能力。
- 页面只展示真实状态与可回读结果，不用 KPI 文案推测业务结果。

## 4. Phase 3A Contract Freeze

### 4.1 已冻结核心

Phase 3A 当前冻结以下核心对象：

- Candidate Pool
- Campaign
- CampaignMember
- OutreachTarget
- OutreachTask
- Today Outreach

这些对象的 API、状态机、权限、持久化与 UI 展示边界以已冻结 Phase 3A contract 为准。UI 不自行扩展字段、排序、状态或操作。

### 4.2 OutreachTask 状态

`OutreachTask` 当前状态固定为：

- `REVIEW_REQUIRED`
- `READY`
- `SENT`
- `STOPPED`
- `FAILED`

当前调度字段只保留 `due_at`。`due_at` 表示任务到期时间，不表示自动发送时间。

### 4.3 Deferred 能力

以下能力保持 Deferred，除非未来有新的正式 contract 与 capability freeze：

- Email
- Sequence
- Inbox
- AI

因此当前 UI 不显示邮件发送、私信自动发送、模板、Sequence、Inbox、回复分析或 AI 意向能力。

## 4.4 Phase 3 UI-2 Selection 与当前冻结状态

### 4.4.1 UI-2 Selection

PHASE 3 UI-2 SELECTION：`PASS`。

选定模块：`Campaign / 拓客活动`。

UI-2 拆分为：

- UI-2A：拓客活动基础管理。
- UI-2B：活动达人名单。

PHASE 3 UI-2 OVERALL：`IN PROGRESS`。

UI-2A 完成不等于 UI-2 完成；UI-2B 尚未完成。

### 4.4.2 UI-2A Final Freeze

PHASE 3 UI-2A — 拓客活动基础管理。

范围固定为：

- Campaign List
- Create Campaign
- Campaign Detail
- Edit Campaign

当前 Gate：

- Final Design Freeze：`PASS`
- Backend UI Readiness：`PASS`
- Web Implementation：`PASS`
- Full Web Gate：`PASS`
- Final Visual Freeze：`PASS`
- Final Candidate：`PASS`
- Safe Sync → `ui-redesign`：`PASS`

因此：

UI-2A：`DONE / FROZEN`。

### 4.4.3 UI-2B Gate

PHASE 3 UI-2B — 活动达人名单。

当前状态：`BLOCKED_BY_MEMBER_DISPLAY_PROJECTION`。

当前 CampaignMember DTO 仍不足以让 UI 在单次分页响应中直接展示成员身份。UI-2B 不得通过前端 workaround 补齐缺失 contract。

当前不能：

- 默认 N+1 获取 influencer detail。
- 显示 UUID 作为用户身份。
- 前端自行决定 preferred account。
- 伪造达人名称或平台账号。

UI-2B 启动至少需要单条 Campaign Member list item 直接提供足够的 canonical display projection，例如：

- member identity
- influencer identity
- 可展示达人身份
- preferred platform
- preferred account display identifier
- active / removed state
- mutation fencing / version

字段名以未来 Backend Contract 为准；v5 不伪造具体 schema。

### 4.4.4 Campaign Backend UI Readiness Amendment

Campaign Backend UI Readiness amendment 已完成。

CampaignResult 新增 required owner projection：

```text
owner: {
  id,
  name,
  status
}
```

`owner.status` 取值为 `active` 或 `disabled`，并保持：

`owner.id == owner_operator_id`。

disabled current owner：

- 仍然可展示真实姓名。
- 仍然可以保持为当前 owner。
- 不允许作为新的 assignment。

### 4.4.5 `/operators` Canonical Options

`GET /api/v1/operators` 现在支持 optional `X-Department-ID`。

规则：

- 默认使用 Session Department。
- Viewer 可读。
- 读取不要求 selected Operator。
- 只返回 active operators。
- 只有 Super Admin 能受控跨 Department。
- 普通角色跨 Department concealment 为 404。

Campaign owner 的 display 直接使用 `CampaignResult.owner`，不得用 `/operators` 反查当前 owner。

`/operators` 只用于可选择的新负责人 options。

### 4.4.6 Campaign Create Freeze

UI“新建拓客活动”只显示：

- 活动名称
- 负责人

POST 只允许前端提交：

- `name`
- 用户明确选择时的 `owner_operator_id`

不得发送隐藏 defaults：

- `review_mode`
- `review_count`
- `duplicate_history_policy`
- `duplicate_window_days`
- `status`
- `version`
- `department_id`

以上字段全部由 Backend authoritative defaults 产生。

创建成功：

- Toast：`拓客活动已创建`
- 唯一导航：`/campaigns/{new_campaign_id}`

### 4.4.7 Campaign Edit Freeze

编辑 Modal 只显示：

- 活动名称
- 负责人

Backend 仍使用 full PUT。编辑必须遵循：

```text
fresh GET detail
→ 保存完整 write baseline
→ 只替换 name / owner
→ 原样保留 review_mode、review_count、duplicate_history_policy、duplicate_window_days、null
→ expected_version = fresh detail.version
```

禁止：

- 使用 list row 作为 PUT baseline。
- 前端 default 补隐藏字段。
- 用 PATCH 模拟 full PUT。
- 清空 null。
- force overwrite。

### 4.4.8 Disabled Owner

当前负责人已停用时，列表与详情展示真实姓名，并弱化标注“已停用”；编辑仍显示当前负责人。

允许：

- 保持该 disabled current owner。
- 切换到 active owner。

禁止：

- 自动清空。
- 自动替换。
- 强迫换负责人。
- 把 disabled owner 做成严重错误。

### 4.4.9 Conflict / Closed

`VERSION_CONFLICT` 文案固定为：

```text
活动信息已被其他人更新。
请重新加载最新信息后再继续编辑。
```

动作：`重新加载最新信息`。

行为：

- 关闭 Modal。
- 丢弃旧 baseline。
- 重新 GET detail。
- 展示服务器最新值。

禁止自动 resubmit、强制覆盖或自动 merge 用户旧输入。

`CLOSED` 可读，UI-2A 不显示编辑入口。race 时若 PUT 返回 `CAMPAIGN_CLOSED`，提示关闭、关闭 Modal、refetch detail；不增加生命周期操作。

### 4.4.10 Campaign List Freeze

正式 route：`/campaigns`。

表格固定为：

- 活动名称
- 状态
- 负责人
- 更新时间
- 操作

操作只有“查看详情”。

状态映射：

- `DRAFT` → 草稿
- `ACTIVE` → 进行中
- `PAUSED` → 已暂停
- `CLOSED` → 已关闭
- 未知 → 未知状态

更新时间使用 `updated_at`。

分页使用 opaque cursor，`limit=50`，交互为“加载更多”。

禁止 total、页码、总页数、自定义排序、搜索、状态筛选、owner 筛选、成员数量与 KPI。

### 4.4.11 Campaign Detail Freeze

route：`/campaigns/{id}`。

Header 展示：

- 活动名称
- 状态
- 负责人
- 更新时间

可编辑场景右侧提供“编辑活动”。基本信息展示活动名称、状态、负责人、创建时间、更新时间。

禁止展示 UUID、Department ID、`created_by_operator_id`、`version`、`expected_version`、review config、duplicate policy、member section 与 KPI。

### 4.4.12 Campaign Preview

development-only route：`/dev-ui-preview/campaigns`。

规则：

- production 访问 `notFound()`。
- 不进入正式导航。
- 不访问 Auth / API / DB。
- 只使用内存 fixture。
- 复用真实 Campaign presentational components。

中文场景固定为：

- 拓客活动 · 列表
- 拓客活动 · 新建活动
- 拓客活动 · 详情
- 拓客活动 · 停用负责人
- 拓客活动 · 编辑活动
- 拓客活动 · 编辑冲突
- 拓客活动 · 空状态
- 拓客活动 · 加载失败

内部 scene key 可英文，但用户展示必须中文。

### 4.4.13 UI-2A 明确禁止能力

UI-2A 当前绝对禁止：

- Campaign Member
- 成员数量
- 添加达人
- 移除达人
- preferred account UI
- Candidate Run import
- activation
- pause
- close
- Review
- Send
- Email
- Sequence
- Inbox
- Reply
- AI
- KPI
- 转化率
- 触达率

这些都不是 UI-2A 的已冻结能力。

## 5. Phase 3 UI-1「今日触达」Final Freeze

状态：`PASS`。

### 5.1 Route 与导航

- 正式 route：`/outreach/today`。
- Sidebar 位于“工作区 → 今日触达”。
- Campaign 当前不进入 Sidebar。

### 5.2 Header

- 标题：`今日触达`
- 描述：`查看今天需要推进的触达任务。`
- 时间信息使用“日期”和“更新于”。
- UI label 不使用“业务日期”。

### 5.3 Task Type

Task Type 选项固定为：

- 全部
- 首次触达
- 待跟进

不显示数量。

### 5.4 筛选

真实候选筛选：

- 渠道
- Campaign
- 负责人
- 赛道

更多筛选：

- 粉丝区间
- 联系方式
- 优先级

canonical source 固定为：

- 负责人：`/api/v1/operators`
- Campaign：`/api/v1/campaigns`
- 赛道：`/api/v1/influencers/filter-options`

没有 canonical source 的 selector 必须隐藏。前端不得维护第二套字典，不得 N+1 请求，不得从当前 rows 拼接 options。

### 5.5 表格

冻结 8 列：

1. 达人账号
2. Campaign
3. 当前任务
4. 渠道
5. 到期时间
6. 联系方式
7. 优先级
8. 负责人

表格没有操作列。

达人主身份使用 `preferred_platform_account.account_name`。达人账号可导航到 `/influencers/{influencer_id}`。

禁止使用假头像、`display_name`、UUID 或引入 Review / Send 操作。

### 5.6 状态与展示

- 任务类型展示“首次触达”或“跟进”。
- `history_warning` 有值时显示“曾有历史触达”的弱橙提示。
- `due_at` 只表示任务到期时间，不代表自动发送时间。
- 联系方式映射为“有邮箱”“有联系方式”“无联系方式”。
- 优先级：`HIGH` → 高，`NORMAL` → 普通；不存在 `LOW`。
- 负责人无法 canonical 解析时显示 `—`，不展示 UUID。

### 5.7 Pagination

- 使用 opaque HMAC cursor。
- 默认 `limit=50`，最大 `limit=100`。
- UI 使用“加载更多”。
- 禁止展示 total、总页数或页码。
- 前端不解析 cursor，不自定义排序。
- Backend 顺序固定为：`priority → due_at → task_id`。

### 5.8 明确禁止能力

Today 当前不允许展示或实现：

- Review
- Send
- Email sending
- 私信自动发送
- Template
- Sequence
- Inbox
- Reply
- AI
- 自动 Follow-up
- KPI Summary
- 新回复
- 高意向
- 回复率
- Campaign 转化率

这些能力必须等待未来正式 contract，不得通过按钮、空状态、摘要卡或文案预置。

## 6. Development Preview / 质量门

### 6.1 Today Preview

development-only route：`/dev-ui-preview/outreach/today`。

场景固定包括：

- 默认列表
- 更多筛选
- 历史触达提示
- 筛选后无结果
- 空状态
- 加载状态
- 加载失败

生产环境访问 preview route 必须 `notFound()`。Preview 不进入正式导航，不访问 Auth/API/DB，只使用内存 fixture，并复用真实 presentational components。

### 6.2 既有 Preview Harness 规则

现有 development-only preview 包括：

- `/dev-ui-preview/influencers`
- `/dev-ui-preview/influencers/drawer`
- `/dev-ui-preview/influencers/drawer/[id]`
- `/dev-ui-preview/data-collection`
- `/dev-ui-preview/refresh-queues`
- `/dev-ui-preview/refresh-queues/[status]`
- `/dev-ui-preview/import-jobs`
- `/dev-ui-preview/outreach/today`

所有 preview 遵循同一安全边界：非 development 环境不可见；不进入正式导航；使用内存静态数据；不发起真实 Auth/API/DB 网络请求；不替代正式 production route。

### 6.3 UI 质量原则

- 真实性：不虚构 API 字段、状态、route 或操作。
- 稳定性：同一字段与状态在 list/detail/drawer 中保持一致。
- 回退安全：异常回到可恢复状态，避免状态跳转失真。
- 结构性：主导航、Topbar、内容区、Drawer 三层结构固定。
- 可读性：高信息密度下保留字段语义，减少装饰性噪音。
- 可执行性：每一个业务操作都必须有可验证后端结果。
- 边界清晰：Frozen 模块不与未冻结模块混合改造。
- 冗余控制：新增字段必须有来源契约，不重复展示重复计算值。
- 复用优先：复用既有组件与 token，不起新 UI 风格。
- 保守展示：缺失、0、false、未知、脱敏与空值分开处理。
- 风险隔离：敏感数据优先脱敏，禁止误披露。
- 不猜测：Preview、排序、筛选只按真实 contract 展示。
- 时区一致：统一按 `Asia/Shanghai` 展示。
- 结果可复核：关键状态与结果可通过 API 回读验证。
- 变更可追溯：变更点附带版本与提交来源。

## 7. PostgreSQL Release Gate

Phase 3A PostgreSQL migration integration gate：

- 6 passed
- 0 failed
- 0 skipped

Release Gate：`PASS`。

Migration head：`0007_phase3a_persistence_amendment`。

这个 gate 只记录已验证的 Phase 3A 持久化与集成结果，不授权启动尚未完成 contract readiness 的 UI-2B 或 Candidate Pool UI。

## 8. Test Infra Debt

状态：`NON_BLOCKING_TEST_INFRA_DEBT`。

已有工程债包括：

- jsdom CSS / getComputedStyle warnings
- Ant Design deprecation warnings
- development preview Modal hydration warning
- 历史 async UI parallel timeout debt

这些不是 Campaign 产品 blocker，也不应被写成 UI-2A 功能失败。不得通过提高 timeout、retry 或 skip 来伪装解决；后续独立治理，不改变既有产品 freeze 或 Release Gate 结论。

## 9. 四层模型调度

- **Spark**：UI 精准修改、screenshot、visual polish。
- **Luna**：repo scan、tests、gate、Git、Docker、mechanical tasks。
- **Terra**：跨模块正式实现、ordinary complex bugs。
- **Sol**：Contract、Schema、Migration、RBAC、concurrency、release decision。

升级原则：

- Contract conflict → Sol
- 跨模块正式实现 → Terra
- 纯视觉 → Spark
- Gate / test → Luna

当前 checkpoint 本身是文档冻结与 Git 边界工作；UI-2A 已在当前 Candidate 中完成并冻结，本 checkpoint 不启动 UI-2B 或 Candidate Pool 实现。

## 10. 当前 Git 记录与 Campaign UI-2A 来源

以下 SHA 均来自当前 `ui-redesign` Git history，用于记录本 checkpoint 的真实来源：

- 当前 committed baseline / `ui-redesign` HEAD：`01d133b5190ae0655ff6b190e61c846c71c949db` — `chore(web): localize campaign preview scenarios`
- Campaign Backend UI Readiness amendment：`0579771b5410c3a50a58f5d76fba71b95316b0c4` — `feat(api): make campaigns safe for UI management`
- UI-2A spec：`3efa9522454ca75ac137ab9931e1134149bcdca4` — `docs: specify Campaign UI-2A implementation`
- UI-2A API / boundaries：`d721e15be0f2d915cdf172d340add27660c75cc3` — `docs: clarify Campaign UI-2A API and boundaries`
- Implementation Plan：`09f063b68674a5587a27c6879b93b5fe6ccb6dde` — `docs: plan Campaign UI-2A implementation`
- Web implementation：`9927257ea9e00d54d23f92c0bb6cc842dfbdcd6f` — `feat(web): add campaign basic management`
- Campaign preview：`798bef9252067cdb7bfaae9c9959fa97908b041c` — `chore(web): add campaign visual preview`
- Visual polish：`d0efeb712d9631e2fa35db5f346db0282e0c23ff` — `style(web): polish campaign basic management`
- Visual polish follow-ups：`6a37986f6229005c18b4fd56d34bae62b0389d2e` / `857fad68e29b268a700017f34def7a622ce7d33f` — `style(web): finalize campaign basic management`
- Final preview localization：`01d133b5190ae0655ff6b190e61c846c71c949db` — `chore(web): localize campaign preview scenarios`

不根据猜测补写不存在的 SHA，不把本 checkpoint 之外的用户 worktree 修改当作 committed baseline。

## 11. 下一阶段启动规则

v5 之后禁止直接实现。下一步只允许在以下两个候选之间做窄范围 readiness selection：

A. 补 UI-2B 所需 Campaign Member display projection。
B. 进入 Candidate Pool UI。

不要重新做全系统 Capability Audit，也不要重新审计 UI-1～UI-7。

比较维度固定为：

- 当前 Backend HTTP readiness
- 用户业务价值
- 对现有 Campaign / Today 的依赖关系
- 是否需要新的 Backend Contract
- 是否能形成完整工作流
- 是否会迫使前端做 N+1、假身份或假能力
- 实现复杂度和状态机风险

下一轮只选择一个候选进入 readiness 评估；选择不等于授权实现。

仍需遵循：

1. Capability / Contract Readiness。
2. UI Design Freeze。
3. Terra 实现真实 contract，包括 API、状态机、route 与权限。
4. Luna Gate。
5. Spark / Luna Visual Polish。
6. Screenshot Review。
7. Freeze。

UI-2B 当前仍为 `BLOCKED_BY_MEMBER_DISPLAY_PROJECTION`；Candidate Pool 也未获实现授权。Email、Sequence、Inbox 与 AI 继续 Deferred。

## 12. Commit Boundary

本 checkpoint 的唯一提交目标：

`docs/superpowers/specs/2026-08-20-ui-design-state-v5.md`

当前 `ui-redesign` worktree 已有用户的 tracked modified 与 untracked 文件；这些不能进入 v5 commit。

只允许 stage：

```text
docs/superpowers/specs/2026-08-20-ui-design-state-v5.md
```

提交前必须确认：

```bash
git diff --cached --name-only
git diff --check --cached
git status --short
```

`git diff --cached --name-only` 必须只有 v5 文档。提交信息固定为：

```text
docs: checkpoint Phase 3 UI design state v5
```

提交后停止，不 push，不纳入现有 tracked 修改或 untracked 用户文件，不开始 UI-2B 或 Candidate Pool。

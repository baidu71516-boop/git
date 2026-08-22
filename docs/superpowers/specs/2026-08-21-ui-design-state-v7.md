# 2026-08-21 UI Design State v7 Checkpoint

此文档是后续 UI 工作使用的唯一 UI Design State checkpoint。它独立包含 UI-1～UI-7 的有效冻结约束、Phase 3A 已冻结 contract、Phase 3 UI-1「今日触达」Final Freeze、Phase 3 UI-2「拓客活动」Final Freeze、Phase 3 UI-3「候选池」Final Freeze、预览与质量门，以及下一模块启动边界。

本文档只记录当前冻结结果与推进边界，不复盘实现过程，不重新审计 UI-1～UI-7。UI-2A 与 UI-2B 均已完成并冻结；PHASE 3 UI-2 OVERALL 为 DONE / FROZEN。

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
- 本 checkpoint 的 committed baseline / 当前 `ui-redesign` HEAD：`50d56c267796a71a66f7b5304d0b59b380b511ed`
- 当前 worktree 中已有用户的 5 个 tracked modified 与 10 个 untracked 文件属于本 checkpoint 之外的内容，必须原样保留，不得纳入本文档 commit。
- v7 只允许新增自身文件；不修改产品代码、测试、Backend、migration 或已有 dirty/untracked 文件。

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

### 1.5 Hallmark-adapted 审核与敏感数据原则

- 审核以真实 contract、可复核 evidence、稳定状态与明确权限边界为准；视觉完成不等于 capability readiness。
- 不以假字段、假身份、UUID 展示、N+1 hydration、自动重试、强制覆盖或 stale-input merge 补齐未实现能力。
- 中文优先；状态、错误、空值、未知值与不可用状态必须分别表达，不用装饰性文案掩盖数据缺失。
- 敏感联系方式按角色分层展示并保持脱敏；不因 preview、列表或 drawer 方便而扩大数据披露。
- 时间统一按 `Asia/Shanghai` 展示，除非真实 contract 明确要求保留原始时区语义。

## 2. UI-1～UI-7 冻结继承

UI-1～UI-7 全部为 `Frozen`。除非出现新的 Backend contract delta 或明确 contract 冲突，不重新审计这些冻结项。

### 2.1 UI-1 Freeze：App Shell 与主导航

- `AuthShell + AppShell`、Topbar、Sidebar、用户菜单及三层结构保持冻结。
- Sidebar 仅保留以下正式入口：
  - 工作区：今日触达、拓客活动、候选池、达人库。
  - 数据：数据采集、导入记录、数据更新。
- 正式 route：
  - 今日触达：`/outreach/today`
  - 达人库：`/influencers`
  - 数据采集：`/`
  - 导入记录：`/import-jobs`
  - 数据更新：`/refresh-queues`
- 不新增 Inbox、Email、AI 等占位 Sidebar 入口。
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

PHASE 3 UI-2 OVERALL：`DONE / FROZEN`。

组成：

- UI-2A：拓客活动基础管理，`DONE / FROZEN`。
- UI-2B：活动达人名单，`DONE / FROZEN`。

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

### 4.4.3 UI-2B Final Gate

PHASE 3 UI-2B — 活动达人名单。

当前状态：`DONE / FROZEN`。

已确认：

- CampaignMember Display Backend：`PASS`
- UI-2B Web Implementation：`PASS`
- Full Web Gate：`PASS`
- Final Visual Freeze：`PASS`
- Final Candidate：`READY_WITH_TEST_INFRA_DEBT`
- Backend / Contract / migration preservation：`PASS`
- production preview safety：`PASS`
- Safe Sync → `ui-redesign`：`PASS`

UI-2B 的 canonical display projection、真实 mutation contract、Campaign Detail tabs、development preview 与视觉状态均已冻结。不得通过前端 workaround、N+1、假身份或假能力扩大本范围。

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


## 4.5 Phase 3 UI-2B「活动达人名单」Final Freeze

### 4.5.1 UI-2B 范围

UI-2B 正式范围：

Campaign Detail
→ [基本信息] [活动达人]

活动达人支持：

- active member list
- influencer identity
- preferred platform account display
- visible influencer detail navigation
- direct add
- active account selection
- bulk add
- remove
- expected_version / CAS
- opaque cursor load-more
- Viewer read-only
- empty / loading / error

UI-2B 不创建独立 Campaign Member route，也不扩展 Campaign 生命周期、Review、Send、Email、Sequence、Inbox、Reply、AI 或 Candidate Pool。

### 4.5.2 Campaign Detail Tabs

正式 route：

- `/campaigns/{id}` → 默认基本信息。
- `/campaigns/{id}?tab=members` → 活动达人。

规则：

- tab 缺失 → 基本信息。
- 非法 tab → 基本信息。
- URL state 可恢复。
- refresh / browser back-forward 保持。
- 只有进入 `members` tab 才请求 Member list。
- 不新增独立 Campaign Member route。

### 4.5.3 Member List Freeze

表格固定为：

| 列 | 内容 |
| --- | --- |
| 达人 | canonical influencer identity |
| 活动账号 | preferred platform account |
| 首次加入时间 | `member.created_at` |
| 操作 | Operator mutation；Viewer 不显示 |

Member list contract：

- active-only。
- default `limit=50`。
- max `limit=100`。
- `created_at ASC`。
- `id ASC`。
- 返回 `next_cursor`。
- 不返回 total。
- 不显示 page count。
- 不支持 custom sorting。
- UI 交互固定为“加载更多”。
- cursor 必须视为 opaque，前端不得解析、拼接或自行生成。

### 4.5.4 Member Identity Projection

canonical Backend projection：

`CampaignMemberResult` 新增 required：

```text
influencer {
  id
  display_name
  status
}

preferred_platform_account {
  id
  platform
  platform_account_id
  account_name
  account_handle
  is_active
}

is_active
```

并保持：

- `influencer.id == influencer_id`。
- `preferred_platform_account.id == preferred_platform_account_id`。
- preferred account belongs to influencer。
- `member.is_active == (removed_at is null)`。

单条 Member list item 必须直接提供这些 display projection；UI 不得通过 N+1 请求补齐。

### 4.5.5 Historical Identity

disabled influencer：

- 仍显示真实 `display_name`。
- 增加“已停用”状态标识。
- 不承诺导航 influencer detail。

inactive preferred account：

- 仍显示真实 platform / account_name / handle。
- 增加“账号已停用”状态标识。
- account inactive != member removed。
- 不把整行做成 disabled。

### 4.5.6 达人导航

active influencer 可导航：

`/influencers/{influencer_id}`

disabled influencer：

- 普通文本。
- 显示“已停用”。
- 不承诺详情导航。

不新增 Campaign Member Detail 页面。

### 4.5.7 活动账号展示

中文列名固定为：

`活动账号`

展示格式：

`{平台中文名} · {account_name}`
`@{account_handle}`

handle 为空时不显示第二行。

平台 presenter 复用 canonical mapping，不硬编码小红书。未知平台必须安全 fallback。

### 4.5.8 首次加入时间

使用 `member.created_at`。

中文列名固定为：

`首次加入时间`

不得写成“加入时间”。

原因：implicit restore 保留原 `created_at`。

### 4.5.9 Campaign Status 与 Member Mutation

成员写入口允许：

- `DRAFT`
- `ACTIVE`
- `PAUSED`

禁止：

- `CLOSED`

`CLOSED`：

- Member list 仍可读。
- 不显示“添加达人”。
- 不显示“移出活动”。
- 显示中性说明：“活动已关闭，无法调整活动达人。”

Viewer 始终无写入口。

### 4.5.10 Add Drawer

Add Drawer 固定为右侧 Drawer，约 920px。

标题：

`添加达人`

副标题：

`选择达人，并指定该活动使用的平台账号。`

数据源：

`GET /api/v1/influencers`

规则：

- 不创建 Campaign 专属候选 API。
- 不做 N+1。
- Drawer 复用真实 Influencer pagination 与真实 candidate data。

### 4.5.11 Add Drawer Search

placeholder 精确冻结为：

`搜索达人昵称或平台账号名称`

真实 `q` 仅搜索：

- influencer `display_name`
- active platform account `account_name`

不搜索：

- handle
- external account id
- contact/email
- bio
- metrics

### 4.5.12 Add Candidate Pagination

候选达人列表复用真实 Influencer pagination：

- `page`
- `page_size`
- `total`

不要为 Add Drawer 发明 cursor。

搜索变化时 page reset 到 1；Drawer 生命周期内 selection 跨分页、跨搜索继续保留。

### 4.5.13 Account Selection

每个 candidate 使用其真实 `platform_accounts[]`，且只包含 active accounts。

规则：

1. 1 个 active account：自动选中，并明确展示。
2. 多个 active accounts：用户必须显式选择。
3. 0 个 active accounts：不可提交，显示“暂无可用平台账号”。

禁止：

- 小红书优先。
- followers 最大优先。
- 第一个账号优先。

### 4.5.14 查看已选

Drawer 内维护本地 selection state：

- “已选择 N 位达人”。
- “查看已选”。

selected-only mode 支持：

- 查看全部已选。
- 修改活动账号。
- 取消选择。
- 返回搜索结果。

selection state 按 influencer identity 管理；达人与账号不得串位。

关闭 Drawer 后，未提交 selection 丢弃。

### 4.5.15 Drawer Close Confirmation

无未提交选择时直接关闭。

有未提交选择时显示：

`放弃本次选择？`

说明：

`关闭后，尚未添加的达人选择将不会保留。`

动作：

- 继续选择。
- 放弃并关闭。

### 4.5.16 Bulk Add

Direct bulk-add 每 row 只发送：

- `influencer_id`
- `preferred_platform_account_id`

不发送 `source_pool_run_id`。

Mutation：

`retry=false`

Idempotency：

- 新 attempt → 新 key。
- 同 payload 的 ambiguous network / 5xx 显式 retry → same key + same payload。
- selection/account 变化 → new attempt + new key。
- 关闭 Drawer 后重新打开 → new attempt。

### 4.5.17 Active Duplicate NO-OP

正式结果标识：

`ACTIVE_DUPLICATE_NOOP`

已经 active 的 member 再 bulk-add：

- 不更新 preferred account。
- version 不变。
- `already_active_count + 1`。

因此 UI-2B Bulk Add 只负责：

- 新增。
- implicit restore。

不负责更换 active member 活动账号。未来更换活动账号必须等待独立 Backend mutation contract。

成功反馈可显示：

`已在活动中的达人未做修改。`

### 4.5.18 Bulk Add Success

真实结果：

- 新增 `{added_count}`
- 重新加入 `{restored_count}`
- 已在活动中 `{already_active_count}`

不要把 `active_count_after` 做永久 KPI。

成功后：

- 关闭 Drawer。
- 清 Member cursor/pages。
- 从第一页重新 GET。
- 不在前端手工插入 rows。

### 4.5.19 Remove Freeze

操作名称：

`移出活动`

确认文案：

`将达人移出活动？`

说明：

`移出后，该达人将不再属于当前拓客活动。不会删除达人资料。`

Remove 使用：

`expected_version = member.version`

mutation：

`retry=false`

成功：

- Toast：`达人已移出活动`。
- 清 cursor/pages。
- 从第一页重新获取。

### 4.5.20 Remove Conflict

`409 VERSION_CONFLICT`：

- 关闭确认框。
- 丢弃旧 member version。
- 清 cursor/pages。
- 从第一页重新 GET。

刷新后 member 不存在：

`该达人已不在当前活动中，列表已更新。`

刷新后仍存在：

`该达人的活动成员状态已发生变化，列表已更新。`

禁止：

- auto retry remove。
- 只替换 version 再提交。
- force overwrite。

### 4.5.21 UI-2B 明确禁止

本阶段永久禁止：

- removed member history
- 独立 restore
- restored_at
- added-by 员工显示
- inactive/deleted influencer 通用详情导航承诺
- Member total / KPI
- Member search/filter/sort
- Candidate Run import UI
- Candidate Pool UI
- Campaign lifecycle controls
- Review
- Send
- Email
- Sequence
- Inbox
- Reply
- AI
- active member account-change mutation

### 4.5.22 UI-2B Development Preview

development-only route：

`/dev-ui-preview/campaigns`

新增并冻结场景：

- 拓客活动 · 活动达人
- 拓客活动 · 添加达人
- 拓客活动 · 查看已选
- 拓客活动 · 多账号选择
- 拓客活动 · 停用达人和账号
- 拓客活动 · 移出达人
- 拓客活动 · 成员状态冲突
- 拓客活动 · 活动已关闭
- 拓客活动 · 暂无活动达人
- 拓客活动 · 活动达人加载失败

规则：

- development-only。
- production 访问 notFound。
- 不进正式导航。
- 不访问 Auth/API/DB。
- 只使用内存 fixture。
- 复用真实 Campaign/Member components。

### 4.5.23 UI-2B Visual Freeze

Final Visual Freeze：`PASS`。

UI-2B visual freeze 覆盖：

- Campaign Detail tabs。
- Member table。
- Add Drawer。
- account selection。
- selected-only mode。
- empty/loading/error。
- disabled identity/account。
- remove success/conflict。
- CLOSED read-only state。
- production preview safety。

### 4.5.24 UI-2B Final Candidate

Final Candidate：`READY_WITH_TEST_INFRA_DEBT`。

Test Infra Debt 是 Web full suite 中既有 async/jsdom 随机失败：

- 不属于 UI-2B 产品 blocker。
- 本 checkpoint 不处理。
- 不得借此修改 Campaign 产品代码。
- 不得修改测试 timeout、retry 或 skip。

Backend / Contract / migration preservation：`PASS`。

### 4.5.25 UI-2B Release / Gate Boundary

UI-2B 已完成冻结，但仍遵循既有 Release / Gate 规则：

- contract-first。
- Backend authoritative。
- Viewer boundary。
- production preview safety。
- targeted evidence 与 full Web gate 必须分开记录。
- Test Infra Debt 不得伪装成产品失败，也不得被忽略为 UI-2B 功能缺陷。
- 本 checkpoint 不 push、不 deploy、不启动新的产品模块；Candidate Pool UI-3 已在既有 Final Candidate 中完成并冻结。

### 4.5.26 UI-2 Overall Completion

PHASE 3 UI-2 — Campaign

OVERALL：`DONE / FROZEN`

组成：

- UI-2A：拓客活动基础管理，`DONE / FROZEN`。
- UI-2B：活动达人名单，`DONE / FROZEN`。

Campaign 是 UI-2 的唯一容器。UI-2A 与 UI-2B 的冻结边界必须分别保持；后续模块不得把 Candidate Pool、Review、Send、Email、Sequence、Inbox、Reply 或 AI 偷渡进 Campaign。

## 5. Phase 3 UI-1「今日触达」Final Freeze

状态：`PASS`。

### 5.1 Route 与导航

- 正式 route：`/outreach/today`。
- Sidebar 位于“工作区 → 今日触达”。
- Campaign 当前以“拓客活动”进入 Sidebar。

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

这个 gate 只记录已验证的 Phase 3A 持久化与集成结果；UI-2B 已完成并冻结，不因该 gate 重新审计 UI-2B。Candidate Pool UI readiness、Web implementation、visual freeze 与 Safe Sync 均已通过；UI-3 已升级为 `DONE / FROZEN`。

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

当前 checkpoint 本身是文档冻结与 Git 边界工作；UI-2A 与 UI-2B 已在当前 Candidate 中完成并冻结，本 checkpoint 不启动新的产品实现；Candidate Pool UI-3 已完成并冻结。

## 10. 当前 Git 记录与 Campaign UI-2A / UI-2B 来源

以下 SHA 均来自当前 `ui-redesign` Git history，用于记录本 checkpoint 的真实来源：

- 当前 committed baseline / `ui-redesign` HEAD：`50d56c267796a71a66f7b5304d0b59b380b511ed` — `fix(web): stabilize candidate pool detail tabs`
- Campaign Member display Backend：`05e8f972d15d068406199610b6b7320ef77a55ba` — `feat(api): enrich campaign member display projection`
- UI-2B Web implementation：`9efd2946128fe75f9a86aed5cb392c0f23062fbe` — `feat(web): add campaign member management`
- UI-2B development preview：`bd50101fa428483e4df1eaef6b0802a3fa5ca056` — `chore(web): add campaign member visual preview`
- UI-2B visual polish：`c3e7b83aef71f4df05b965e354979ba13e1cdf62` — `style(web): polish campaign member management`
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

Candidate Pool UI-3 已完成并冻结。v7 不选择下一产品模块，只记录：

- 下一阶段候选：`TBD / 需单独选择`。
- 下一模块必须在本 checkpoint 完成后另行决定。
- 不自动开始 Email、Sequence、Inbox、AI 或任何新模块。
- 不重新审计 UI-1～UI-7、Today、Campaign 或 Candidate Pool。
## 12. Commit Boundary

本 v7 checkpoint 的唯一提交目标：

`docs/superpowers/specs/2026-08-21-ui-design-state-v7.md`

当前 `ui-redesign` worktree 已有用户的 tracked modified 与 untracked 文件；这些不能进入 v7 commit。

只允许 stage 上述 v7 文档。提交前必须确认 staged 文件只有 v7 文档，且 `git diff --check --cached` 通过。提交信息固定为：

`docs: checkpoint Phase 3 Candidate Pool UI design state v7`

提交后停止，不 push，不 deploy，不纳入现有 tracked 修改或 untracked 用户文件，不开始下一模块。

## 13. Phase 3 UI-3「候选池」Final Freeze

Phase 3 UI-3 — Candidate Pool：`DONE / FROZEN`。

Candidate Pool 是真实的人工作业闭环：查看已有候选池、查看只读规则、生成不可变 Candidate Run、查看 `MATCH / UNKNOWN`、理解 reason / evidence、人工明确选择，再加入已有 Campaign。它不是 AI 自动找客户，不是自动加入 Campaign，也不是生成 Run 后自动触达。

正式状态汇总：

- Phase 3 UI-1 — 今日触达：`DONE / FROZEN`。
- Phase 3 UI-2 — Campaign / 拓客活动：`DONE / FROZEN`。
  - UI-2A：`DONE / FROZEN`。
  - UI-2B：`DONE / FROZEN`。
- Phase 3 UI-3 — Candidate Pool / 候选池：`DONE / FROZEN`。

### 13.1 Current Git Baseline 与 Candidate History

- `ui-redesign` 当前 committed baseline：`50d56c267796a71a66f7b5304d0b59b380b511ed`。
- Candidate Pool Final Candidate branch：`phase-3a-candidate-pool-ui3-final`。
- Candidate Pool Final Candidate SHA：`50d56c267796a71a66f7b5304d0b59b380b511ed`。
- Safe Sync 回 `ui-redesign`：`PASS`。

Candidate Pool 完整链路的真实 Git history：

- Backend UI readiness amendment：`7b1f339131492be351baf4d800388f08ed877f1b`。
- Web implementation：`f8f6eecdd0a16282b243e33f096b4f795feef242`。
- Campaign success link fix：`85788dcf5368fe51657b286d80e6de3a21691674`。
- Visual preview：`4152b374fecba95706d6855fe233acf7dc08e564`。
- Preview stabilization：`2c9872327fa5c88a0d1b20e28cb6077b7d501050`。
- Visual Freeze delta：`db9ad293e11b0bbb063f61b26ff8af5d71d16ee0`。
- Tabs runtime fix / Final Candidate：`50d56c267796a71a66f7b5304d0b59b380b511ed`。

### 13.2 Candidate Pool Gates

- Backend UI Readiness Contract Freeze：`PASS`。
- Backend amendment implementation：`PASS`。
- Backend amendment Gate：`PASS`。
- Product Structure Review：`PASS`。
- Final Design Freeze：`PASS`。
- Web Implementation：`PASS`。
- Campaign Link Fix：`PASS`。
- Real Visual Preview：`PASS`。
- Final Visual Freeze：`PASS`。
- Tabs Runtime Fix：`PASS`。
- Final Candidate：`READY_WITH_TEST_INFRA_DEBT`。
- Safe Sync：`PASS`。

### 13.3 Candidate Pool Frozen Contract

以下是 v7 当前冻结的 Candidate Pool UI-3 完整产品与交互边界：

### Candidate Pool 产品定位

冻结第一版真实闭环：

查看已有候选池
→ 查看只读规则
→ 生成不可变 Candidate Run
→ 查看 MATCH / UNKNOWN
→ 理解 reason / evidence
→ 人工明确选择
→ 加入已有 Campaign

明确：

不是 AI 自动找客户。

不是自动加入 Campaign。

不是生成 Run 后自动触达。


### Candidate Pool 正式导航

Sidebar 工作区顺序：

今日触达
拓客活动
候选池
达人库

数据区：

数据采集
导入记录
数据更新

Candidate Pool route：

/candidate-pools

/candidate-pools/{pool_id}

/candidate-pools/{pool_id}/runs/{run_id}

无 Candidate Result point-detail route。


### Candidate Pool List Freeze

正式页面：

候选池

副标题：

查看用于筛选目标达人的候选池。

固定表格列：

候选池名称
类型
状态
负责人
更新时间
操作

操作：

查看详情

禁止：

- 新建候选池
- 搜索
- filter
- custom sort
- total
- pages
- KPI
- raw UUID

Pool list：

UUID cursor
default 50
max 100
id ASC
next_cursor
no total

Web视 cursor为 opaque。

UI：

加载更多


### Pool Status / Owner

Candidate Pool正式状态只有：

ACTIVE
ARCHIVED

中文 presenter按正式实现记录。

不要保留不存在的：

DRAFT
CLOSED
DISABLED

Pool owner使用：

CandidatePoolPublic.owner

复用：

CampaignOwnerSummary

字段：

id
name
status

owner.id == owner_operator_id

disabled owner：

仍显示真实姓名
+
弱“已停用”

不得通过 Web /operators hydration。


### Pool Detail Freeze

正式 Tabs：

基本信息
规则历史
生成记录

URL：

/candidate-pools/{id}
→ 基本信息

?tab=policies
→ 规则历史

?tab=runs
→ 生成记录

missing tab：
basic

invalid tab：
basic

refresh/back/forward：
URL-driven

lazy load：

basic：
不读 policies/runs

policies：
只读 policies

runs：
只读 runs


### Tabs Runtime Fix

正式记录最终 runtime fix：

CandidatePoolDetailView 使用单一 canonical tab source。

数据流：

URL / preview canonical tab
→ Tabs.activeKey

Tabs onChange：

只有：

nextTab != current canonical tab

才执行一次 navigation。

禁止：

local state
↔ effect
↔ URL

双向同步。

记录：

之前 visual preview stabilization
使用 uncontrolled/defaultActiveKey
造成内部 AntD tab state
和外部 preview tab source分裂。

最终修复 commit：

50d56c267796a71a66f7b5304d0b59b380b511ed

正式 browser smoke：

basic PASS
policies PASS
runs PASS

Maximum update depth blocker：
CLOSED。


### 基本信息

只展示：

候选池名称
类型
状态
负责人
创建时间
更新时间

首版隐藏：

id
department_id
owner_operator_id
current_policy_id
source_collection_job_id
version

不显示 raw UUID。


### Policy History Freeze

只读。

数据：

GET /api/v1/candidate-pools/{pool_id}/policies

真实 contract：

全量 list
version ASC
无 cursor
无 pagination
无 total

固定列：

版本
规则类型
创建时间
操作

当前 policy：

policy.id == pool.current_policy_id

在版本 cell中显示：

当前规则

不要生命周期“状态”列。

操作：

查看规则

无：

编辑
复制
新版本
保存
JSON editor


### Policy Type Truthfulness

同一个 Pool 的 Policy History
只能是与 Pool kind一致的一种 Policy。

不要混：

SELLER
BUYER

同一 Pool history。

员工 UI使用中文 presenter：

卖家规则
买家规则

不得显示 raw SELLER / BUYER。


### Policy Drawer

标题：

筛选规则

约 640px Drawer。

SELLER：

只展示 SELLER typed definition。

BUYER：

只展示 BUYER typed definition。

不得在同一 Drawer中混两种 schema。

Presenter必须：

typed
allowlisted
中文

禁止：

raw JSON
JSON.stringify
UUID
工程对象 dump

未知 schema：

该规则版本暂不支持完整展示。


### Create Run Eligibility

“生成候选结果”只属于：

生成记录 tab。

基本信息：
不显示。

规则历史：
不显示。

生成记录：
有真实写权限且满足 eligibility时显示。

位置：

生成记录区域右上

正常尺寸 primary button。

不得：

full-width
底部 CTA
sticky CTA

真实 Pool状态矩阵：

ACTIVE：
可尝试 create run，
但必须满足 current policy真实前置条件。

ARCHIVED：
不可 create。

current policy缺失/不可用：

显示：

当前候选池没有可用规则，暂时无法生成候选结果。

Backend仍为最终 authority。


### Run History

GET runs：

cursor
default 50
max 100
id ASC
next_cursor
no total

表格固定：

创建时间
数据基准时间
状态
符合条件
信息不足
不符合条件
操作

操作统一：

查看详情

只有 COMPLETED：

显示真实：
match_count
unknown_count
not_match_count

PENDING
RUNNING
FAILED：

全部 count：
—


### Run Status

正式：

PENDING
RUNNING
COMPLETED
FAILED

中文：

等待生成
正在生成
已完成
生成失败

视觉：

等待生成：
中性

正在生成：
蓝

已完成：
绿

生成失败：
克制红色

文字 + 颜色。

无：

progress %
progress bar
retry
cancel


### Create Run Mutation

POST：

/api/v1/candidate-pools/{pool_id}/runs

body：

{}

Mutation：

selected Operator
CSRF
Idempotency-Key

retry=false

首次：

202

idempotent replay：

200

统一成功：

候选结果已开始生成

然后导航 Run detail。


### Run Polling

PENDING / RUNNING：
继续 polling

COMPLETED / FAILED：
停止

必须支持：

PENDING → COMPLETED

不依赖一定观察 RUNNING。

GET读取失败：

!= Run FAILED

显示：

状态更新失败

重新加载

页面 hidden：

暂停或显著降低 polling。

恢复 visible：

立即 GET authoritative状态。


### Run Failed Error Boundary

error_message没有 safe-public保证。

禁止直接展示：

error_message
exception
stack trace
detail JSON

使用：

error_code
→ allowlisted中文 presenter

至少记录正式 stable mapping：

TARGETING_POLICY_MISSING

TARGETING_POLICY_INVALID

CANDIDATE_POOL_INACTIVE

TARGETING_MATERIALIZATION_FAILED

未知 code：

候选结果生成失败，请稍后查看或联系管理员。


### Completed Summary

仅 COMPLETED显示：

符合条件 {match_count}
信息不足 {unknown_count}
不符合条件 {not_match_count}

使用一条紧凑 Summary Band。

不是 KPI cards。

NOT_MATCH：

只有 count。

不能点击生成逐条 rows。


### Candidate Result Semantics

Materialized rows只有：

MATCH
UNKNOWN

UI filter：

全部
符合条件
信息不足

映射：

omitted
MATCH
UNKNOWN

无：

不符合条件 tab

NOT_MATCH：
不 materialize。

Buyer：

CATEGORY_MISMATCH
→ MATCH

不要误映射成 NOT_MATCH。


### Candidate Result Table

写权限用户：

选择
候选达人
平台账号
结果
判断原因
判断依据

Viewer：

无选择列。

无：

header select-all checkbox

不提供：

Select all results
Select all current page

用户必须逐行明确选择。


### Candidate Identity Projection

Campaign UI Readiness amendment正式记录：

CandidatePoolMemberPublic 新增 required：

influencer: InfluencerIdentitySummary

platform_account: PlatformAccountIdentitySummary

一致性：

influencer.id == influencer_id

platform_account.id == platform_account_id

platform_account belongs to influencer

无 Contact字段。


### Historical Identity

disabled influencer：

仍显示真实 display_name
+
已停用

不导航。

soft-deleted influencer：

historical row仍可展示 canonical identity。

inactive account：

仍显示：

平台
account_name
handle
+
账号已停用

不灰整行。

这些历史 rows：

对有写权限用户仍允许选择。


### Platform Account

使用：

member.platform_account

员工展示：

平台中文名 · account_name

如果 handle存在：

@handle

Candidate Pool preview/Contract
必须使用正式 Backend支持的平台枚举。

不要 fixture创造未支持平台。


### Result / Reason Presenter

MATCH：

符合条件
弱绿色

UNKNOWN：

信息不足
弱橙色

Evidence Drawer顶层结果
复用同一 presenter。

reason_codes：

全部当前 closed enum必须有中文 presenter。

未知：

未知判断原因

生产不显示 raw enum。


### Evidence Drawer

约640px。

标题：

候选判断依据

顶部：

达人
平台账号
结果
判断原因

SELLER：

typed allowlisted criteria presenter。

BUYER：

typed allowlisted presenter。

禁止：

raw JSON
重新计算 Backend result
恢复 Contact
推测隐藏字段

CATEGORY_MISMATCH Buyer 场景：

结果必须是：

符合条件

弱说明：

该候选池用于查找分类方向可能发生变化的账号，因此分类不匹配属于当前规则的命中条件。


### Selection Lifecycle

默认：

无选择。

MATCH / UNKNOWN：

都允许显式选择。

UNKNOWN：

不能自动加入。

Load More：
保留 selection

Evidence Drawer：
保留

Campaign Modal open/cancel：
保留

Campaign list read error：
保留

filter变化：
清 selection
清 cursor
第一页 GET

强制第一页 reload：
清 selection

成功加入 Campaign：
清 selection

离开 Run route：
销毁

refresh：
不恢复

不写 local/session storage。


### Same Influencer Multi-account

Backend constraint：

同一 bulk request
不得为同一 influencer
选择多个 Candidate Member账号。

UI冻结：

已经选中 influencer A某 row后，

尝试选第二个同 influencer row：

- 阻止第二次选择
- 原 selection保持
- count不增加
- 不 silent replace
- 不 tie-break
- 不自动平台优先

显示弱提示：

同一达人只能选择一个平台账号，请先取消已选账号。


### Campaign Selector

Modal：

约640–720px。

数据：

GET /api/v1/campaigns

无 fake search。

无 status filter。

无“新建活动”。

状态：

DRAFT：
草稿，可选

ACTIVE：
进行中，可选

PAUSED：
已暂停，可选

CLOSED：
已关闭
可见
不可选择

不能前端隐藏 CLOSED，
因为 Campaign list是 cursor API且无 status server filter。


### Candidate → Campaign

POST：

/api/v1/campaigns/{campaign_id}/members/from-candidate-run

body exact：

run_id
member_ids[]

不发送 account id。

Backend使用 Candidate Member自身 account。

Mutation：

selected Operator
CSRF
Idempotency-Key
retry=false

真实 success counts：

added_count
restored_count
already_active_count

already_active > 0：

已在活动中的达人未做修改。


### Candidate → Campaign Success Navigation

成功：

- Modal关闭
- Candidate selection清空
- 当前 Run page保持
- 不自动导航

提供次级 semantic Link：

查看拓客活动

目标：

/campaigns/{campaign_id}?tab=members

campaign_id：

使用本次成功提交前明确选中的 target Campaign id。

正式 fix commit：

85788dc


### Viewer

Viewer可读：

Pool
Detail
Policy
Run
Results
Evidence
Load More
active influencer navigation

Viewer不显示：

生成候选结果
Candidate checkbox
selection summary
加入拓客活动

不要 disabled mutation buttons。


### Empty / Error States

记录冻结的：

Pool empty

Policy empty

Run History empty

Completed Result empty

MATCH empty

UNKNOWN empty

Campaign selector loading/empty/error

polling error

全部中文、局部、克制。

不要营销插画。


### Development Preview

正式 dev-only：

/dev-ui-preview/candidate-pools

必须：

development-only
production notFound / 404
不进正式 Sidebar
无 Auth
无 API
无 DB
内存 fixture
复用真实 Candidate Pool components

正式 Visual Freeze已通过。

不得把：

Superpowers Brainstorming
设计说明卡片
screenshot-ready文案

带入真实 preview。


### Candidate Pool Visual Freeze

正式记录：

FINAL VISUAL FREEZE：
PASS

已验收并冻结：

- Candidate Pool List
- Policy History
- SELLER Policy Drawer
- BUYER Policy Drawer
- Run History
- Completed Results
- Evidence Drawer
- Campaign Selector
- Historical Identity
- Same Influencer Multi-account
- Viewer Read-only
- final 4 visual delta fixes
- Tabs browser runtime fix


### Candidate Pool Git History

自动读取 Git history
并记录真实完整 SHA。

至少包含：

Backend UI readiness：
7b1f339131492be351baf4d800388f08ed877f1b

Web implementation：
f8f6eec...

Campaign success link：
85788dc...

Visual preview：
4152b37...

Preview stabilization：
2c98723...

Visual Freeze delta：
db9ad29...

Tabs runtime fix / Final Candidate：
50d56c267796a71a66f7b5304d0b59b380b511ed

不要猜未知完整 SHA。

以 git log为准补全。


### Candidate Pool Gates

记录：

Backend UI Readiness Contract Freeze：
PASS

Backend amendment implementation：
PASS

Backend amendment Gate：
PASS

Product Structure Review：
PASS

Final Design Freeze：
PASS

Web Implementation：
PASS

Campaign Link Fix：
PASS

Real Visual Preview：
PASS

Final Visual Freeze：
PASS

Tabs Runtime Fix：
PASS

Final Candidate：
READY_WITH_TEST_INFRA_DEBT

Safe Sync：
PASS

最终模块状态：

Phase 3 UI-3 Candidate Pool：
DONE / FROZEN


### Test Infra Debt

继续记录：

NON_BLOCKING_TEST_INFRA_DEBT

包括历史：

- jsdom
- Ant Design
- CSS/getComputedStyle
- async/full-suite instability
- development preview hydration 类 warning（如当前工程仍存在）

明确：

这些不是：

Candidate Pool产品 blocker。

禁止通过：

timeout increase
skip
retry
删测试

伪装解决。

后续独立治理。


### 当前 Phase 3 UI状态

正式：

UI-1 Today：
DONE / FROZEN

UI-2 Campaign：
DONE / FROZEN

UI-3 Candidate Pool：
DONE / FROZEN

至此：

Candidate Pool
→ Campaign
→ Outreach Task
→ 今日触达

上游到下游闭环已经有真实 UI。

不要把这句话扩展成：
Email/Sequence/Inbox/AI已经实现。


### Deferred 能力

继续明确：

Candidate Pool尚未包含：

- Create Pool UI
- Edit Pool
- Archive Pool
- Append Policy
- Policy Editor
- Buyer taxonomy authoring
- JSON editor
- Run Retry
- Run Cancel
- NOT_MATCH row list
- Candidate Result point-detail
- Candidate Pool KPI
- AI

Phase 3整体继续 deferred：

- Email
- Sequence
- Inbox
- Reply
- AI自动触达

不要在 v7中暗示这些已完成。


### 下一模块

v7 不选择下一产品模块。

只记录：

下一阶段候选：
TBD / 需单独选择

不要自动开始：

Email
Sequence
Inbox
AI
或任何新模块。

下一模块必须在 v7 checkpoint
完成后另行决定。

### 四层模型调度

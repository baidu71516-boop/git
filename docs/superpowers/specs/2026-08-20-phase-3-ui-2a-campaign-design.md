# Phase 3 UI-2A：拓客活动基础管理设计

## 范围与边界

实现 `/campaigns` 和 `/campaigns/[id]` 的 Campaign List、Create、Detail 与 Edit。用户界面统一使用“拓客活动”。侧栏在“工作区”中新增“拓客活动”，位于“今日触达”和“达人库”之间。

本设计不实现 Campaign Member、成员数量、达人增删、preferred account、Candidate Pool 导入、生命周期操作、Review、Send、Email、Sequence、Inbox、Reply、AI 或 KPI。UI-2B 继续 blocked。

## 架构与路由

新增 `features/campaigns`，分离 DTO 类型、API 请求、React Query hooks、展示格式化器和页面组件。`AuthShell` 新增 `campaigns` workspace；Campaign 读取不要求 selected Operator，只有写入口受现有 mutation-context UX 约束。`/campaigns` 和 `/campaigns/[id]` 路由仅负责把页面参数传入 AuthShell。

列表使用 `GET /campaigns?limit=50` 及服务端返回的 opaque `next_cursor`。每次“加载更多”将下一批 append 到已有 rows；前端不得解析 cursor、重排或显示 total/page 信息。

详情使用 `GET /campaigns/{id}`。打开编辑框前再次 refetch 详情，得到唯一的 fresh write baseline。

## 展示与状态

列表固定列为活动名称、状态、负责人、更新时间、操作。负责人直接使用 `CampaignResult.owner`；disabled owner 显示真实姓名和低优先级“已停用”。状态只映射 `DRAFT`、`ACTIVE`、`PAUSED`、`CLOSED`；未知值为“未知状态”。所有时间使用项目现有 Asia/Shanghai presenter 和 `updated_at` / `created_at` 的真实字段。

列表和详情分别使用局部 Skeleton、空态与冻结中文文案。详情对 404 展示“拓客活动不存在或不可访问”，对真实 403 展示“无法查看该拓客活动”，其他读取失败展示可重试错误；不暴露后端原始错误。

Viewer 可读取列表和详情，但不显示创建或编辑。CLOSED 可读取，但不显示编辑。

## 创建

创建 Modal 宽约 560px，只有活动名称和负责人。名称前端验证 required 与 max 200，后端仍是最终权威。负责人选项仅来自 `GET /operators` 的 canonical active operators；加载失败时保留未指定负责人和重试 option，不伪造数据。

POST payload 只包含 `name` 与用户明确选择时的 `owner_operator_id`。未选负责人不填 selected Operator ID，由后端使用 authoritative default。每一次创建 attempt 生成一个 Idempotency-Key；用户对同一未改 payload 显式重试网络不确定或 5xx 时复用同一 key，编辑 payload 或关闭后重开均开始新 attempt。mutation `retry: false`。成功 toast 后唯一导航到详情。

## 编辑、并发与隐藏配置

编辑 Modal 仅允许修改名称和负责人，但 full PUT 从 fresh baseline 原样带回 `review_mode`、`review_count`、`duplicate_history_policy`、`duplicate_window_days` 与 `expected_version`。必须保留 null，不能以默认值替换或从列表行构造请求。

若 current owner disabled，编辑框显示其为当前已选项并允许保持；active `/operators` 列表只允许新的 active assignment。选项加载失败时仍可进行 name-only edit。

`VERSION_CONFLICT`：关闭 Modal、丢弃旧 baseline、重新 GET detail 并呈现服务器值，不合并、强写或自动重试。`CAMPAIGN_CLOSED`：提示关闭、关闭 Modal、refetch detail。所有变更均沿用 CSRF、selected Operator 与权限校验，mutation 禁止自动 retry。

## 验证

新增 Campaign API、presenter、workspace 和 routing tests，覆盖固定请求、cursor append、权限、empty/loading/error、owner projection 与 disabled owner、创建幂等性、详情读取状态、fresh baseline、full PUT、null 保留、owner options 失败、409 与 closed 路径。运行相关 Vitest 文件及 typecheck/lint，并在结束时确认只包含本功能和规格文件的改动。

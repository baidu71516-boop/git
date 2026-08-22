# Phase 3 UI-2B：活动达人名单 Development Preview 与 Visual Polish 设计

## 目标

在独立 visual worktree 中扩展现有 `/dev-ui-preview/campaigns`，为 Campaign Detail 的“活动达人”提供 development-only 的 UI-2B fixture/state preview，并完成第一轮视觉 polish 与 responsive 检查。

本轮不改变正式 API、query、mutation、cursor、idempotency、RBAC、CampaignMember 业务语义或任何后端代码。preview 只注入内存 fixture 和场景状态，复用正式 presentational components。

## 范围与禁止项

范围仅包括：

- 现有 Campaign preview route 的 UI-2B scene select、fixture 和本地状态；
- Campaign Detail tabs 的 additive polish；
- 活动达人 header、member table、empty/error/closed/pagination 状态的视觉 polish；
- Add Campaign Members Drawer、selected-only、multiple-account selection、discard confirmation、bulk-add feedback 的 preview wiring/polish；
- remove confirmation 与 409 冲突 preview 状态；
- 1600、1440、900、720 宽度的实际浏览器检查与 6 张截图；
- targeted frontend tests、Campaign UI-2A 回归、Prettier、相关 ESLint、TypeScript、`git diff --check`。

明确不实现：Backend、API Contract、DB/migration、CampaignMember mutation semantics、cursor semantics、idempotency semantics、RBAC、selected Operator、Candidate Pool、Campaign lifecycle、Review、Send、Email、Sequence、Inbox、Reply、AI、KPI、removed member history、restore、active member account-change mutation、merge、push、deploy。

## 架构与路由

继续使用 `/dev-ui-preview/campaigns`，不创建第二个 Campaign preview route。页面保持现有 production guard：非 development 环境调用 `notFound()`。

preview workspace 是唯一的 preview orchestration 层，负责：

1. 读取并维护单个中文 Select 的 scene key；
2. 根据 scene 注入 Campaign、Member、candidate influencer、platform account、pagination 和 mutation result fixtures；
3. 将 preview fixtures 适配为正式组件接受的 presentational props；
4. 处理 drawer/modal 是否打开、selected-only、搜索、账号选择、提交 loading、错误提示和列表更新等本地 state；
5. 对模拟 mutation 只做内存更新，不调用正式 API client、React Query、Auth 或 DB。

正式组件边界保持不变；如果当前组件把 query/mutation 与 presentation 耦合，则只做最小的纯 presentational extraction：

- `CampaignDetailView` 的正式路由继续拥有详情读取、Campaign Header、Basic/Member tabs 和正式 query orchestration；preview 复用其 detail/tabs presentation，并通过 preview adapter 注入 fixture；
- `CampaignMemberTable` 继续拥有四列成员表与 identity/account/date presenter，正式路由和 preview 都使用同一组件；
- `AddCampaignMembersDrawer` 继续拥有批量添加工作区的搜索、选择、账号选择、selected-only 和关闭确认；正式路由使用正式 candidate query/mutation adapter，preview 使用内存 candidate/submit adapter；
- `CampaignMembersSection` 的纯 presentation（header、empty/error/closed/pagination、remove confirm）继续复用，正式路由保留原 query/mutation wiring，preview 只提供本地 member/pagination/remove adapter；
- 现有 modal、presenter、formatter 和 canonical platform presenter 继续复用。

这些 adapter 只传递已存在的 displayable data、loading/error 状态和 callback，不新增 DTO 字段、业务规则、mutation retry、cursor 解析、idempotency 处理或权限判断。正式 `/campaigns` 路由的 query/mutation 代码保持原样。

如果 preview 为了注入状态必须改变正式业务逻辑、正式 query/mutation、DTO/contract 或权限行为，立即停止并报告，不使用 preview workaround 掩盖问题。

## Scene 与 fixture 设计

单个 Select 的用户可见 label 必须完整中文，内部 key 可使用英文。保留现有中文 UI-2A 场景，并新增：

- `拓客活动 · 活动达人`
- `拓客活动 · 添加达人`
- `拓客活动 · 查看已选`
- `拓客活动 · 多账号选择`
- `拓客活动 · 停用达人和账号`
- `拓客活动 · 移出达人`
- `拓客活动 · 成员状态冲突`
- `拓客活动 · 活动已关闭`
- `拓客活动 · 暂无活动达人`
- `拓客活动 · 活动达人加载失败`

UI-2B fixtures 使用 displayable identity projection：influencer `display_name/status`、platform account `platform/account_name/account_handle/is_active`、member `created_at`。fixture 不把 UUID、version、added-by、source run、total 或未来 lifecycle 字段渲染到页面。

至少包含以下可见数据：

- active influencer “科技小王”，链接到现有 `/influencers/{id}`；
- disabled influencer “科技小王 / 已停用”，普通文本；
- active account “小红书 · 数码老李 / @shumaolaoli”；
- inactive account 仍显示名称，第二行弱化为“账号已停用”；
- 一个无 handle 的 account，验证不渲染空的第二行；
- 一个只有单一 active account 的 candidate，自动选中；
- 一个有小红书与抖音两个 active account 的 candidate，必须显式选择；
- 一个无可用账号的 candidate，显示“暂无可用平台账号”且不可提交；
- next cursor 与 exhausted 两种分页状态；
- active、viewer、CLOSED 三种权限/状态组合；
- bulk-add success、account error、remove conflict 三种本地结果。

## Campaign Detail 与 Member table 视觉策略

Campaign Header/UI-2A 保持现有冻结视觉，仅做 Member tab additive polish。正式 tabs 保持 `[基本信息] [活动达人]`，默认 Basic；Member 选中态清楚但不扩大为导航墙。

活动达人区显示：


- 标题“活动达人”；
- 辅助文案“管理当前活动中的达人及其使用的平台账号。”；
- 仅在有写权限且 Campaign 为 DRAFT/ACTIVE/PAUSED 时显示“添加达人”；
- CLOSED 显示中性 inline 说明“活动已关闭，无法调整活动达人。”；
- 不显示成员数量、统计卡、KPI、Candidate Pool 入口。

Member table 固定四列：达人、活动账号、首次加入时间、操作。Viewer/CLOSED 不渲染操作列。正文至少 14px，行高目标 48–52px，表格保持 table，不 Card 化。

达人列与账号列使用现有 presenter：active influencer 为正常链接；disabled influencer 为普通文本加中性灰“已停用”；平台使用 canonical platform label；account handle 为空时不显示第二行；inactive account 只弱化状态文本，不整行变灰、不使用红色警报。

时间列固定 label “首次加入时间”，使用现有 Asia/Shanghai absolute formatter，格式 `YYYY-MM-DD HH:mm`。

操作列每行只有“移出活动”，不出现查看、更多、恢复或更换活动账号。

分页只显示“加载更多”或“已经到底了”之一；不显示 total、page、total pages、disabled loading button 与 exhausted text 同时出现。

## Add Drawer 与本地状态流

复用正式 `AddCampaignMembersDrawer`，宽度约 920px，标题/副标题固定：

- “添加达人”
- “选择达人，并指定该活动使用的平台账号。”

搜索 placeholder 固定为“搜索达人昵称或平台账号名称”。候选行高密度展示 checkbox、达人昵称和活动账号选择，不渲染 CRM Stage、Freshness、联系方式、账号 ID 或 metrics。

selection state 保持在 Drawer 本地：

- 只有一个 active account 时自动选中且清楚展示；
- 多账号时展示平台、账号名称、handle，要求选择；
- 无可用账号时显示弱提示且该行不能组成可提交 selection；
- 底部固定工作摘要“已选择 N 位达人”和“查看已选”；
- selected-only mode 标题/状态清楚，可查看所有已选、修改账号、取消选择并返回搜索结果；
- 有未提交选择时关闭触发普通“放弃本次选择？”确认，不使用全红危险视觉；
- 提交缺少账号时按钮禁用并显示清楚辅助原因；
- submitting 只在按钮内 loading，不使用大 Spin；
- success 显示“达人添加完成”及“新增 N · 重新加入 N · 已在活动中 N”，already-active 时追加弱说明；
- account error 显示指定中文错误，保留 Drawer 和 selection，不暴露 raw backend code。

## Remove 与 read-only 状态

remove confirm 使用小尺寸 Modal：

- 标题“将达人移出活动？”；
- 说明“移出后，该达人将不再属于当前拓客活动。不会删除达人资料。”；
- 显示达人姓名；
- 按钮“取消”“确认移出”，确认按钮可使用 danger。

409 preview 只显示 reload 后的列表状态和对应中文提示：

- “该达人的活动成员状态已发生变化，列表已更新。”；或
- “该达人已不在当前活动中，列表已更新。”。

不提供强制移出、再次提交、覆盖、自动重试或旧输入合并。

Viewer：tabs、member list、account、pagination 和 active influencer link 正常；直接不渲染添加、操作列和移出按钮。CLOSED：member list 可读，显示中性说明，不渲染添加和移出。

## Responsive 策略

- 1600/1440：Member table 四列完整，Drawer 约 920px，内部留白舒适；
- 900：Member table 保持 table，Drawer 使用更高比例宽度；
- 720：Member table 只在自身容器横向滚动，Drawer 接近全屏宽度；
- 页面不产生整体横向滚动；不隐藏达人、账号、首次加入时间；不缩小全局字体；不把 table 改成 Card list。

## 测试与视觉验证

新增或更新 targeted preview tests，至少覆盖：

- production `notFound` guard；
- 单 Select 场景 label 与 UI-2B scene mapping；
- Member table 四列、viewer/CLOSED 操作列隐藏、active/disabled identity、account handle/inactive account、Asia/Shanghai date；
- empty/error/reload/has-next-cursor/exhausted；
- Drawer search placeholder、single/multiple/no-account selection、selected-only、discard confirm、disabled submit、submit loading/success/account error；
- remove confirm 与 conflict result；
- Campaign UI-2A detail/list/routing regressions。

验证命令按实际 package scripts 执行：UI-2B targeted Vitest、Campaign UI-2A 相关回归、changed files Prettier、relevant ESLint、TypeScript、`git diff --check`。如果修改共享 CSS，补跑对应 Campaign/UI-2A regression。最后使用实际 preview 在 1600、1440、900、720 检查并保存六张用户指定截图，同时记录 Console error/warning。

## Git 与交付边界

视觉 worktree：`/Users/mac/Downloads/influencer_outreach_campaign_ui2b_visual`

分支：`phase-3a-campaign-ui2b-visual`

起点：`9efd2946128fe75f9a86aed5cb392c0f23062fbe`

设计文档先单独提交。preview wiring/fixture 使用：

`chore(web): add campaign member visual preview`

视觉调整独立提交：

`style(web): polish campaign member management`

只有确有极小收口时才新增：

`style(web): finalize campaign member management`

本轮不 merge、push 或 deploy。结束时 visual worktree 必须 clean，且不带 generated build noise。

最终 verdict 只能是：

- `CAMPAIGN_UI2B_VISUAL_READY`
- `CAMPAIGN_UI2B_VISUAL_BLOCKED`

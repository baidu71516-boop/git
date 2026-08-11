# Phase 1C 需求冻结与技术设计

- 文档状态：`IMPLEMENTED — pending final human acceptance`
- 编制日期：2026-08-10
- 最终门禁日期：2026-08-11
- 设计基线：`da2c3a3`（`phase-1b-complete`）
- 实现基线：`0ee22a7`（Phase 1C Task 1F）
- 目标分支：`phase-1c-influencer-library`
- 本文用途：Phase 1C 的冻结需求、技术设计与最终实现门禁记录。
- 状态含义：需求、API、数据读取语义、权限、测试和实施边界仍保持冻结；对应代码与运行门禁已经完成，但人工最终验收尚未完成。
- 阶段门禁：不得重新引入第 35 节已经关闭的备选方案，也不得在未获明确指令前进入 Phase 2。

## 0. 依据、优先级与冲突裁决

本文已交叉检查：

- `DEVELOPMENT_PLAN.md`
- `ACCEPTANCE_CRITERIA.md`
- `PRODUCT_PRD.md`
- `ARCHITECTURE.md`
- `PROJECT_STRUCTURE.md`
- `DATABASE_SCHEMA.md`
- `API_SPEC.md`
- `SECURITY_RULES.md`
- `CHANGELOG.md`
- 当前 `backend_core` 的 Influencer、Import、Auth、Audit 实现
- 当前 FastAPI Router、依赖注入、RBAC、CSRF 与统一响应实现
- 当前 Next.js 登录壳、API client、测试结构
- Alembic `0001` 至 `0003`
- Phase 1B 单元、HTTP、PostgreSQL 集成测试与脱敏 Fixture

冲突裁决顺序：

1. 已由产品负责人确认的决策和本任务红线。
2. `ACCEPTANCE_CRITERIA.md` 中 Phase 1C 的明确验收项。
3. `DEVELOPMENT_PLAN.md` 的阶段边界。
4. 已验收的 Phase 1B 数据模型与保护规则。
5. `PRODUCT_PRD.md`、`API_SPEC.md` 中与本阶段一致的内容。
6. 其他历史设计文档。

已确认的陈旧内容不得恢复：

- `PROJECT_STRUCTURE.md` 中 API 内 Service 和根目录 `services/` 的双业务层结构已经失效。唯一业务核心继续是 `packages/backend_core`。
- `DATABASE_SCHEMA.md` 中把小红书字段直接放在 Influencer 主体、以及单一 Metrics 表的旧结构，已经被 `Influencer + PlatformAccount + Source + CurrentMetrics + Snapshot` 取代。
- `PRODUCT_PRD.md` 中“Email 自动去重”的旧规则已经失效。Email 永远不能触发自动匹配或合并。
- `API_SPEC.md` 中通用 PATCH、批量派单、加入 Campaign、停止触达等是全 V1 草案，不自动进入 Phase 1C。

`docs/OPEN_QUESTIONS.md` 当前 OPEN-003 至 OPEN-007 分别面向 Phase 2、Phase 3 和正式部署，不阻塞本次需求冻结。`PREDEPLOY-001` 的 Docker buildx 缺失仍是正式部署前事项，不改变 Phase 1C 功能范围。

---

## 1. 阶段目标

Phase 1C 对应 `DEVELOPMENT_PLAN.md` 的 Sprint 1.3“达人库”。目标是把 Phase 1B 已安全落库的公司级达人数据变成可日常查询的达人资源库，并满足以下明确验收结果：

1. 按 Influencer 主体分页浏览，不因多账号、多来源、多 Contact 的 JOIN 产生重复主体。
2. 支持昵称搜索。
3. 支持赛道、粉丝范围、负责人、CRM Stage 筛选。
4. 正确展示达人主体、平台账号、真实公开资料、联系方式、当前指标、来源追溯与不可变历史快照。
5. 延续硬身份去重和 Email 疑似重复规则。
6. 新导入不得错误覆盖人工数据、较新来源状态或历史快照。
7. 为后续多平台数据源保留通用边界，但本阶段只使用现有 `xiaohongshu` 数据，不新增其他 Connector。

本阶段的验收主体是“可查询、可追溯、非破坏性的达人库”，不是 CRM、触达或营销工作流。

## 2. 明确的非目标

Phase 1C 不包含：

- AI 分析、AI 评分、AI 标签、个性化生成或真实 Provider 调用。
- Playbook、Campaign、Campaign Lead 或 A/B Test。
- 邮件发送、Follow-up、Inbox、回复分类、退订处理或停止触达动作。
- CRM 看板、CRM Stage 修改、跟进记录、备注、Task 或成交工作流。
- Analytics、日报或转化指标。
- Market Demand、需求池、跨部门派单、Handoff To Marketing 或 Deal。
- 抖音、视频号、快手或其他平台 Connector。
- 重新设计或重写 Phase 1B Import、Preview、Confirm、Matcher、Storage。
- 手工新增 Influencer。
- 通用 Influencer PATCH、任意字段编辑或通用批量 PATCH。
- Influencer、PlatformAccount、SourceState、Contact、CurrentMetrics、Snapshot 的物理删除。
- 为填满页面而创建 AI、触达、CRM、备注等空 Tab 或占位业务模块。
- 对缺失指标进行推算、抓取、补造、AI 猜测或跨时间窗口换算；例如不得把真实“近 60 天”指标伪装成“近 7 天”。
- Contact 导出；如后续增加导出，必须另行冻结权限、字段白名单和 Audit。

## 3. 实现基线与可复用能力

### 3.1 已有领域模型

当前 Phase 1B 已提供：

```text
Influencer（公司级主体）
├── owner_operator_id -> Operator
├── PlatformAccount[]
│   ├── SourceState[]
│   ├── SourceIdentity[]
│   ├── CurrentMetrics[]
│   └── MetricSnapshot[]
└── Contact[]
```

- `Influencer`：`display_name`、`owner_operator_id`、`crm_stage`、`status`、`deleted_at` 和时间戳。
- `InfluencerPlatformAccount`：通用平台身份、账号资料、来源标签和活跃状态；当前 `Platform` 只包含 `xiaohongshu`。
- `InfluencerSourceState`：按账号与来源保存来源当前状态、版本、哈希和最后 Import Job/Row。
- `PlatformAccountSourceIdentity`：保存来源系统稳定 ID，并有公司级唯一约束。
- `InfluencerContact`：保存类型、值、来源、验证状态、当前/历史状态、疑似重复、观察时间和 Import 追溯。
- `InfluencerCurrentMetrics`：按 `(platform_account_id, source)` 保存当前合并投影。
- `InfluencerMetricSnapshot`：保存每次真实输入的不可变历史快照及 Import 追溯。

### 3.2 已有 Import 能力

- Adapter 输出平台无关的 `CanonicalInfluencerRecord`。
- 灰豚 37 列 Mapping 集中在 Huitun Adapter，不泄漏到通用 Matcher/Planner。
- 当前支持安全 CSV/XLSX 解析、持久化 Preview、stale 防护、异步 Confirm、原子写入和幂等。
- Planner 已实现来源新鲜度、非破坏性合并、人工数据保护、硬身份冲突转人工审核、Email 疑似重复和 Snapshot 幂等。
- 真实指标只在输入存在且严格解析成功时保存；缺失或解析失败不会被推算。

### 3.3 已有平台与 HTTP 基础

- FastAPI 已有统一 envelope、错误转换、request ID 和依赖注入模式。
- Phase 1C 读请求复用 `require_auth`。现有 Phase 1B Mutation 继续复用 `require_csrf_context` 并在核心 Service 再次鉴权；Phase 1C 不新增 Mutation。
- Session 权限来自 `DepartmentPermission.role`；所选 Operator 只改变 Audit 归属，绝不能提升权限。
- Audit 已有不可变记录结构，可记录 Department、Operator、Action、Result、Entity、before/after、IP、User-Agent。
- Web API client 使用同源 Cookie；非 GET/HEAD 自动发送 CSRF header；FormData 不覆盖 multipart boundary。
- Web 已有登录、Session 恢复、Operator 选择、React Query Provider 和 Vitest/Testing Library 基础。

### 3.4 Phase 1C 已实现能力

- `backend_core/influencers` 已包含 API 无关的 Schema、只读 Repository 和只读 Service；业务查询、公司级可见性和 Contact 字段裁剪只实现一次。
- FastAPI 已装配冻结的四个 `/api/v1/influencers` GET，并继续使用统一 envelope、request ID 和 Session 认证。
- Web 已实现 `/influencers` 列表与 `/influencers/{id}` 详情，并以最小调整复用既有 AuthShell；达人库读取不要求 Operator，Import 流程仍要求 Operator。
- PostgreSQL、真实 Compose/API/浏览器与 Phase 1A/1B 回归门禁已完成；第 33 节记录最终通过项。
- Phase 1C 没有新增人工标签模型、AuditAction、表、字段、约束、性能索引或 migration。

## 4. 达人列表的准确字段

列表粒度固定为“一行一个 Influencer 主体”，不能退化为“一行一个平台账号”。PlatformAccount 和 CurrentMetrics 均以数组返回，不创建 Primary Account、Primary Metric 或跨来源优先级。

### 4.1 列表 API 项的最小字段

| 字段 | 类型 | 数据来源 | 规则 |
|---|---|---|---|
| `id` | UUID | Influencer | 主体 ID |
| `display_name` | string | Influencer | 受人工数据保护；不能被普通导入覆盖 |
| `status` | enum | Influencer | 模型支持 `active` / `disabled`；Phase 1C 响应只返回 `active` |
| `crm_stage` | enum | Influencer | 只展示、筛选；Phase 1C 禁止修改 |
| `owner` | object/null | Operator | 最小摘要：`id`、`name`、`status`；Owner 为空返回 null |
| `platform_accounts` | array | PlatformAccount | 只返回 active 账号摘要；不得扁平 JOIN 复制主体 |
| `current_metrics` | array | CurrentMetrics | 返回 active 账号的账号+来源分组；只返回真实存在字段 |
| `current_contacts` | array | Contact | 返回全部 `is_current=true` Contact；前三个非 Viewer 角色输出完整 `display_value`，Viewer 对任一非空值固定输出 `***` |
| `possible_duplicate_contact` | boolean | Contact 聚合 | 任一可见当前 Contact 被标记时为 true；不是合并状态 |
| `created_at` | datetime | Influencer | UTC ISO 8601 |
| `updated_at` | datetime | Influencer | UTC ISO 8601；不是来源指标时间 |

`platform_accounts[]` 摘要字段：

- `id`
- `platform`
- `platform_account_id`
- `account_name`
- `account_handle`
- `profile_url`
- `source`
- `is_active`
- `source_tags`（账号级当前投影）

`current_metrics[]` 摘要字段：

- `platform_account_id`
- `source`
- `source_updated_at`
- `followers_count`（仅存在时返回数字，否则 `null`）

`current_contacts[]` 摘要字段：

- `id`
- `type`
- `display_value`（`super_admin`、`manager`、`operator` 为完整值；`viewer` 对任一非空值固定为 `***`）
- `source`
- `validation_status`
- `possible_duplicate_contact`

### 4.2 列表 UI 列

明确需要的列：

1. 达人：主体 `display_name`，辅以 active 平台账号名。
2. 平台账号：平台、账号名/Handle、主页链接。
3. 赛道/标签：只使用 active PlatformAccount 的当前 `source_tags` 投影；不显示人工标签。
4. 粉丝：按 active 账号与来源展示真实 `followers_count`，不得计算跨账号总和、最大值或来源优先级。
5. 联系方式：返回全部当前联系方式；`viewer` 只看脱敏值，其他三个正式角色看完整值。
6. CRM Stage：只读。
7. 负责人：Owner 摘要或未分配。

不显示 AI Score、最近联系、下次跟进、Campaign、Reply、微信添加状态或 Analytics 字段。

## 5. 达人详情的准确区块和字段

详情使用区块，不创建后续业务的空 Tab。

### 5.1 主体概要

- `id`
- `display_name`
- `status`
- `crm_stage`（只读）
- `owner` 摘要
- `created_at`
- `updated_at`

### 5.2 平台账号与公开资料

每个 PlatformAccount 单独分组：

- `id`
- `platform`
- `platform_account_id`
- `account_name`
- `account_handle`
- `profile_url`
- `source`
- `is_active`
- `bio`
- `gender`
- `region_raw`
- `verification_info`
- `mcn_name`
- `creator_level`
- `is_brand_partner`
- 该账号的 `source_tags` 当前投影

### 5.3 联系方式

- `id`
- `platform_account_id`（可空，空表示主体级 Contact）
- `type`
- `display_value`：`super_admin`、`manager`、`operator` 返回完整当前值；`viewer` 对任一非空值固定返回 `***`
- `source`
- `validation_status`
- `is_current`
- `possible_duplicate_contact`
- `first_seen_at`
- `last_seen_at`
- `source_updated_at`
- `first_import_job_id` / `first_import_row_id`
- `last_import_job_id` / `last_import_row_id`

返回全部 current Contact，不创建 Primary Contact 或 `is_primary`。Viewer 的固定掩码不得保留原值字符、局部地址、尾号、域名或原值长度；`type` 等非值字段仍按契约返回，缺失单值仍为 `null`。不得向浏览器返回仅供匹配用的 `normalized_value`；完整 Contact 不得进入日志、console、埋点、错误消息或 Audit。

### 5.4 来源与追溯

按 PlatformAccount + Source 展示：

- SourceState 的 `source`、`source_updated_at`、`state_version`
- SourceState 白名单 canonical `creator_tags`，仅作为该来源的追溯字段展示，不形成独立筛选协议
- `last_import_job_id`、`last_import_row_id`
- SourceIdentity 的 `id`、`platform_account_id`、`platform`、`source`、`external_account_id`、`first_import_job_id`、`first_import_row_id`、`last_import_job_id`、`last_import_row_id`

不得把 PlatformAccount 自身的单值 `source` 误解为该账号唯一来源。页面不直接暴露任意原始 `source_data` JSON；只通过白名单字段响应。

现有 SourceIdentity 重导时不会刷新其 last import 字段，因此 UI 不得把这组字段命名为“最后观察”。Phase 1C 不修改 Phase 1B identity observation 逻辑。

### 5.5 当前指标

按 PlatformAccount + Source 分组，响应只包含数据库真实存在的键：

- `followers_count`
- `notes_count`
- `likes_collects_total`
- `commercial_notes_count`
- `notes_60d`
- `viral_rate_60d`
- `avg_likes_60d`
- `avg_collects_60d`
- `avg_comments_60d`
- `avg_shares_60d`
- `huitun_score`
- `active_fans_raw`
- `active_fans_rate`
- `active_fans_count`
- `suspicious_fans_raw`
- `suspicious_fans_rate`
- `suspicious_fans_count`
- `fan_gender_raw`
- `fan_male_rate`
- `fan_female_rate`
- `fan_region_raw`
- `fan_region_distribution`
- `fan_age_raw`
- `fan_age_distribution`
- `fan_active_time_raw`
- `fan_active_time_distribution`
- `fan_interests_raw`
- `fan_interests_distribution`
- `image_note_price`
- `image_cpe`
- `image_cpm`
- `video_note_price`
- `video_cpe`
- `video_cpm`

同时返回：`source`、`source_updated_at`、`last_import_job_id`、`last_import_row_id`。不得把 `huitun_score` 改名为通用 AI Score。

上述键是“当前 Huitun Adapter 已产生的真实 Metrics 数据字典”，不是通用 Service 的来源分支。Repository/Service 只按 `platform_account_id + source` 读取和白名单透传 Metrics JSON，不得写 `if source == huitun` 的字段映射或计算；任何新来源的字段解释必须留在对应 Adapter 或独立展示字典中。

类型必须保持 Phase 1B canonical JSON 的实际语义：

- 计数类字段是 JSON integer。
- `huitun_score`、价格、CPE、CPM、比例和 rate 是保精度的十进制字符串；前端不得先转为二进制浮点再回写。
- `viral_rate_60d` 和解析出的 `*_rate` 使用 0 到 1 的比例语义；UI 可格式化为百分比，但 API 不改变存储值。
- 当前文档没有确认价格字段的币种，UI 不得自行添加人民币或其他币种符号。
- `*_raw` 是来源原文字符串，仅用于如实展示。
- `*_distribution` 是 `{label, rate}` 数组，其中 `rate` 是十进制字符串。
- 字段缺失与数值 0 必须区分；API 不得为了结构整齐而补造 0。

### 5.6 历史指标快照

历史快照通过独立分页 endpoint 读取，按账号与来源显示：

- `id`
- `platform_account_id`
- `source`
- `source_updated_at`
- `captured_at`
- `metrics`（本次输入的真实指标集合）
- `import_job_id`
- `import_row_id`

Snapshot 不是 CurrentMetrics 的完整副本；不得把输入快照和合并后的当前投影混为一谈。

### 5.7 标签

- 赛道/列表标签：只使用 active PlatformAccount 的当前 `source_tags` 投影。
- 来源追溯：可在详情对应 SourceState 中展示 canonical `creator_tags`，但它不参与本阶段赛道筛选。
- 不创建 Manual Tag、独立赛道字典、Tag 表或 AI Tag；不做同义词、AI 分类或推断。

## 6. 搜索语义

搜索参数固定为 `q`：

- 对输入做首尾空白 trim；trim 后为空等同未传。
- 在 `Influencer.display_name` 和 active PlatformAccount 的 `account_name` 上做不区分大小写的 contains 匹配。
- `%`、`_` 和 escape 字符必须按字面量正确转义，用户输入不能改变 LIKE 模式。
- 最大 160 字符；超长返回统一 422。
- 不搜索 `account_handle`、Contact、bio、MCN 或 Metrics。
- 本阶段不强制安装 `pg_trgm`，也不为假设的性能问题创建搜索投影或索引。

## 7. 每一个筛选条件的语义

Phase 1C 只支持下列五个单值筛选参数：

| 参数 | 冻结语义 | 缺失/非法行为 |
|---|---|---|
| `tag` | 输入先 trim，trim 后为空等同未传；非空值与任一 active PlatformAccount 当前 `source_tags` 的 trim 后原值完整精确匹配 | 无标签不命中；输入最大 160 字符，超长 422；不做 NFC、casefold、子串、同义词或 AI 匹配 |
| `followers_min` | 任一 active PlatformAccount 的任一真实 CurrentMetrics `followers_count >= followers_min` | followers 缺失、null 或非法遗留值不命中；参数必须为非负十进制整数 |
| `followers_max` | 任一 active PlatformAccount 的任一真实 CurrentMetrics `followers_count <= followers_max` | followers 缺失、null 或非法遗留值不命中；参数必须为非负十进制整数 |
| `owner_operator_id` | `Influencer.owner_operator_id` UUID 精确匹配 | Owner 为 null 不命中；非法 UUID 返回 422 |
| `crm_stage` | 当前 `CRMStage` 枚举精确匹配 | 非枚举值返回 422 |

Followers 上下边界均包含；同时传入时必须满足 `followers_min <= followers_max`，否则返回 422。命中规则不跨账号求和、不选择最大值、不建立来源优先级、不推算。

`crm_stage` 只接受当前 `CRMStage` 的实际值：`待开发`、`已发送邮件`、`第一次跟进`、`第二次跟进`、`已回复`、`已加微信`、`沟通中`、`潜在合作`、`高意向`、`暂不考虑`、`长期维护`、`已结束`。Phase 1C 只查询这些现存状态，不创建状态流转。

Phase 1C 不支持 `platform`、`source`、`has_contact`、`contact_type`、`possible_duplicate_contact`、`status` 或任何 AI Score、Campaign、ReplyClass、微信添加、最近联系、下次跟进、邮件状态、Analytics 筛选。

### 7.1 组合规则

- 不同筛选字段之间使用 AND。
- 不支持同一字段多值参数；同一参数重复出现返回 422，不定义 ANY/ALL。
- Repository 必须使用 `EXISTS`、子查询或等价方式避免多 JOIN 扩张导致主体重复和 `total` 错误。

## 8. 分页协议

已确认采用 API 规范中的页码分页：

```json
{
  "items": [],
  "page": 1,
  "page_size": 50,
  "total": 0
}
```

请求参数固定为 `page`、`page_size`：`page` 默认 1，`page_size` 默认 50、最大 100；两者必须为正整数，非法值返回 422。响应嵌入统一 envelope 的 `data`。超过末页返回 HTTP 200、`items=[]`，`total` 保持真实总数。

## 9. 默认稳定排序及 tie-breaker

冻结排序：

- 达人列表：`Influencer.created_at DESC, Influencer.id DESC`。
- Snapshot：`captured_at DESC, id DESC`。
- 所有排序末尾必须追加不可变且唯一的 `id` tie-breaker。
- Phase 1C 不开放客户端排序参数。

嵌套集合必须确定性排序：PlatformAccount 按 `platform ASC, id ASC`，CurrentMetrics 按 `platform_account_id ASC, source ASC, id ASC`，Contact 按 `type ASC, id ASC`，SourceState 和 SourceIdentity 按 `platform_account_id ASC, source ASC, id ASC`。该排序只保证响应稳定，不创建“主账号”或“主 Contact”语义。

采用 `created_at` 避免来源更新改变既有主体的默认位置。即使有稳定 tie-breaker，页码分页在并发插入时仍可能发生跨请求位移，客户端不能把它当作快照游标。

## 10. 空值、缺失值和未知值的展示与筛选规则

- API 的单值缺失返回 `null`，集合缺失返回 `[]`；不返回字符串 `"unknown"` 代替 null。
- UI 对 null 或不存在字段统一显示 `—`。
- 数值 `0`、布尔 `false` 和空字符串不是同一语义；`0` 不得当作未知。
- 指标键不存在或值为 null 时显示 `—`，不得计算替代值。
- 普通精确/范围筛选默认不匹配缺失值。
- Phase 1C 不提供 unknown 或未分配专用筛选参数。
- Owner 为空显示“未分配”。
- 无 Contact 显示 `—`；被权限脱敏的 Contact 不得显示成缺失。
- Source Tag 为空返回空数组。
- 时间按 API UTC ISO 8601 传输，UI 按 `Asia/Shanghai` 展示；未知时间显示 `—`。

## 11. Influencer、PlatformAccount、Source、Contact、Metrics 的关联规则

1. Influencer 是公司级主体，不含部门外键；Owner 不改变可见性。
2. 一个 Influencer 可有多个 PlatformAccount；一个账号只能属于一个 Influencer。
3. PlatformAccount 的平台身份由平台 ID、来源外部 ID或规范化主页 URL 保证；裸账号由 Service 拒绝。
4. 一个 PlatformAccount 可有多个 SourceState 和 CurrentMetrics，键均为账号 + Source。
5. PlatformAccount 自身的 `source` 是创建来源，不代表唯一数据来源。
6. Contact 属于 Influencer，可选关联具体 PlatformAccount；主体级人工 Contact 的账号外键可空。
7. CurrentMetrics 和 Snapshot 必须同时指向同一 Influencer 下的 PlatformAccount；现有复合外键保持不变。
8. SourceState、Contact、CurrentMetrics、Snapshot 的 Import Job/Row 追溯不得被 Phase 1C 重写。
9. 查询组装不得通过任意一个账号或 Contact 反向改变 Influencer 主体归属。
10. 公司级读取不按导入来源部门或 Owner 部门裁剪；Owner 是业务归属信息，不是数据 ACL。
11. 正式权限只来自 Session 的 `DepartmentPermission.role`；所选 Operator 只用于操作归属，不得提升权限。
12. 列表、搜索、筛选和详情统一只读取 `Influencer.status=active AND deleted_at IS NULL`；Phase 1C 不提供 disabled/已删除浏览入口。active Influencer 引用 disabled Owner 时仍准确返回该 Owner 的 `status=disabled` 摘要。

## 12. 当前指标与不可变历史快照规则

Phase 1C 必须原样延续 Phase 1B 规则：

- Newer：当前指标执行非破坏性最新数据合并；新值覆盖同名字段，输入缺失字段保留旧值。
- Same：只补现有空值；同时间冲突保留旧值并产生警告。
- Older：不替换当前指标。
- Unknown：已有当前指标时不替换；首次真实指标可建立当前投影。
- 只要本次输入含真实指标且 `snapshot_key` 不重复，可新增历史 Snapshot，即使该输入比当前状态旧。
- Snapshot 保存“该次输入集合”，CurrentMetrics 保存“当前非破坏性合并投影”。
- Phase 1C 只读代码不得 UPDATE 或 DELETE Snapshot，不得为展示补齐缺失历史字段。
- Snapshot 的不可变性由无 Snapshot 写 Service/API、现有 FK/RESTRICT 和自动化回归共同保证；Phase 1C 不新增 PostgreSQL immutable trigger。

## 13. 来源标签边界

- Phase 1C 的“赛道”唯一查询真相源是 active PlatformAccount 的当前 `source_tags` 投影。
- `tag` 参数使用现有 Adapter trim 后的原值做完整精确匹配；输入最大 160 字符，超长返回 422。
- 不做 Unicode NFC、casefold、子串、同义词、AI 分类或推断。
- 历史 Source Tag 原值不截断、不回写；超过 160 字符的既有值仍可展示，但本阶段无法通过 `tag` 参数筛选。
- SourceState 以账号 + Source 保存来源状态；其中 canonical `creator_tags` 只在详情中作为来源追溯白名单字段展示，不形成独立来源筛选协议。
- PlatformAccount 的 `source_tags` 列本身不是来源级标签表，仍只由 Phase 1B Import 新鲜度规则维护。
- Phase 1C 不增加 Manual Tag、独立赛道字典、Tag 表、规范化标签投影或标签 Mutation。

## 14. 硬身份去重规则

自动硬匹配顺序固定为：

1. `platform + platform_account_id`；当前灰豚小红书即 XHS platform user ID。
2. `source + platform + external_source_id`；当前可承载 huitun_id，样本中可为空。
3. `platform + normalized_profile_url`。

补充约束：

- 本次已提供且实际命中的硬身份（可以是 1、2 或 3 个）全部指向同一账号时才可自动匹配；不要求三个键同时存在。
- 多个硬身份指向不同账号时必须 `MANUAL_REVIEW`，禁止自动合并。
- `account_handle` 只可产生潜在匹配警告，不是硬身份。
- 文件内相同 `primary_identity_key` 由首行确定性占有，后续行 Skip。当前 Generic Import 的跨键冲突仍可能在 Preview Revision/数据库唯一约束阶段转为 stale 或失败；Phase 1C 不绕过约束，也不借机重写 Matcher。
- Phase 1C 不新增手工 merge、拆分或删除操作。
- 现有数据库唯一约束必须保留，不得通过 UI 或 API 绕过。

## 15. Email 疑似重复规则

- Email 是 Contact，不是 PlatformIdentity。
- Email 不得参与 Influencer 自动匹配或自动合并。
- 不同硬身份共享同一 Email 时仍是不同 Influencer，只把相关 Contact 标为 `possible_duplicate_contact=true`。
- 同一 Email 可能是 MCN 公共商务邮箱，不能据此判断同一达人。
- UI 必须使用“疑似重复联系方式”等准确文案，不得显示“重复达人已合并”。
- Phase 1C 只展示该标记，不创建“已处理/非重复”处理闭环。
- 人工 Contact 不得被 Huitun Import 停用、改值、更新观察追溯或改写疑似重复状态。

## 16. Phase 1C 允许的操作与写边界

Phase 1C 新增能力完全只读，只允许以下四个 endpoint：

- `GET /api/v1/influencers`
- `GET /api/v1/influencers/filter-options`
- `GET /api/v1/influencers/{influencer_id}`
- `GET /api/v1/influencers/{influencer_id}/metric-snapshots`

Phase 1C 不新增任何写操作。既有 Phase 1B Import Confirm 仍可按原规则写入 Influencer 相关表，其权限、CSRF、Audit、原子性和人工数据保护规则不受影响。

## 17. Phase 1C 禁止的写操作

本阶段禁止：

- 手工创建 Influencer。
- 通用 `PATCH /influencers/{id}` 或任意字段 PATCH。
- 批量通用 PATCH。
- Owner 修改或重新分配。
- Manual Tag 新增、修改或删除。
- Manual Contact 新增、修改、停用或删除。
- 修改 CRM Stage。
- 修改平台身份、PlatformAccount 来源字段、SourceState、SourceIdentity。
- 手工写 CurrentMetrics。
- 更新或删除 MetricSnapshot。
- 修改、停用或删除来源 Contact。
- 物理删除 Influencer 或任何来源追溯记录。
- 批量加入 Campaign、停止触达、发送邮件。
- 通过选择不同 Operator 获取更高权限。

## 18. Owner、Tag、Contact 的冻结范围及依据

| 能力 | 明确依据 | 当前冻结状态 |
|---|---|---|
| Owner 展示/筛选 | Phase 1C 验收明确要求负责人筛选 | 纳入只读 |
| Owner 修改 | Phase 1C 冻结为完全只读 | 不纳入 |
| Source Tag 展示/筛选 | Sprint 1.3 包含 Tags，真实导入已有 `source_tags` | 纳入，只查 active 账号当前投影 |
| Manual Tag | Phase 1C 冻结为现有 Source Tag 只读 | 不纳入，不建表 |
| Contact 展示 | Phase 1C 验收明确要求 | 纳入只读 |
| Manual Contact 维护 | Phase 1C 冻结为完全只读 | 不纳入 |
| 来源 Contact 修改 | 与 Phase 1B 追溯和人工保护冲突 | 禁止 |
| CRM Stage 修改 | 本任务红线只允许展示和筛选 | 禁止 |

## 19. Admin、Manager、Member、Viewer 权限矩阵

正式代码角色继续是 `super_admin / manager / operator / viewer`。Admin 只是 `super_admin` 的文档显示别名，Member 只是 `operator` 的文档显示别名；不得修改 Role enum、认证、Session 或数据库。

| 操作 | `super_admin`（Admin） | `manager` | `operator`（Member） | `viewer` |
|---|---:|---:|---:|---:|
| 公司级达人列表 | 允许 | 允许 | 允许 | 允许 |
| 公司级达人详情 | 允许 | 允许 | 允许 | 允许 |
| 当前指标/历史快照 | 允许 | 允许 | 允许 | 允许 |
| Source Tag | 允许 | 允许 | 允许 | 允许 |
| current Contact 完整值 | 允许 | 允许 | 允许 | 禁止；任一非空 `display_value` 固定为 `***` |

四个角色都只拥有本阶段的公司级读取能力；本阶段没有 Mutation 权限矩阵。权限必须由后端 Service 执行，来自 Session 的 `DepartmentPermission.role`，不能从所选 Operator 的角色推导。Owner 和 Import 来源部门都不是数据 ACL。

## 20. CSRF 要求

- Phase 1C 四个 endpoint 均为 GET，只要求有效 Session，不要求 CSRF。
- `backend_core.influencers.service` 必须执行公司级读取授权和 Contact 字段裁剪，不能只依赖前端。
- Phase 1B 既有 Mutation 的 double-submit CSRF、Operator 和后端二次鉴权保持不变。
- Web 必须继续使用统一 API client，不得为达人库单独绕过 Cookie/CSRF。
- 当前 Web client 的 CSRF Cookie 名硬编码为默认 `outreach_csrf`，后端 Cookie 名可配置。Phase 1C 不得改 Cookie 名；配置一致性作为已知工程风险记录，不能假设任意自定义名称可工作。

## 21. Audit 事件及记录内容

- 普通列表、详情、filter-options 和 Snapshot GET 不产生业务 Audit；现有 request ID 和结构化访问日志继续生效。
- Phase 1C 不新增 AuditAction。
- Contact 导出不在本阶段。
- 密码、Session token、CSRF token、Secret、完整 Contact 或任意导入原始行不得进入日志、console、埋点、错误消息或 Audit。

## 22. 冻结的后端 API endpoint、请求和响应结构

所有响应使用现有 envelope：

```json
{
  "success": true,
  "data": {},
  "error": null,
  "request_id": "..."
}
```

Phase 1C 只实现以下四个 GET，不增加任何 Influencer POST、PUT、PATCH 或 DELETE。

#### `GET /api/v1/influencers`

Query：

- `q`
- `tag`
- `followers_min`
- `followers_max`
- `owner_operator_id`
- `crm_stage`
- `page`
- `page_size`

响应：第 4 节的 ListItem 数组与第 8 节分页结构。

响应形状示例：

```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "00000000-0000-0000-0000-000000000001",
        "display_name": "示例达人",
        "status": "active",
        "crm_stage": "待开发",
        "owner": null,
        "platform_accounts": [
          {
            "id": "00000000-0000-0000-0000-000000000002",
            "platform": "xiaohongshu",
            "platform_account_id": "example-platform-id",
            "account_name": "示例达人",
            "account_handle": null,
            "profile_url": null,
            "source": "huitun",
            "is_active": true,
            "source_tags": []
          }
        ],
        "current_metrics": [
          {
            "platform_account_id": "00000000-0000-0000-0000-000000000002",
            "source": "huitun",
            "source_updated_at": null,
            "followers_count": null
          }
        ],
        "current_contacts": [],
        "possible_duplicate_contact": false,
        "created_at": "2026-08-10T00:00:00Z",
        "updated_at": "2026-08-10T00:00:00Z"
      }
    ],
    "page": 1,
    "page_size": 50,
    "total": 1
  },
  "error": null,
  "request_id": "example-request-id"
}
```

示例中的名称、ID 和时间仅用于描述 JSON 类型，不是 Fixture、默认账号或内置数据。

#### `GET /api/v1/influencers/{influencer_id}`

响应：第 5.1 至 5.5、5.7 节的结构化详情。不存在或按可见性规则不可见时返回统一 404；不得泄漏已软删除实体是否存在。

详情响应必须用命名数组分别承载 `platform_accounts`、`contacts`、`source_states`、`source_identities` 和 `current_metrics`；不得返回 ORM 对象、任意 `source_data`、`normalized_value` 或导入原始行。只读取 `status=active AND deleted_at IS NULL`，其他主体统一返回 404。

#### `GET /api/v1/influencers/{influencer_id}/metric-snapshots`

Query 只允许 `page`、`page_size`，默认值和限制与第 8 节一致。响应为第 5.6 节的分页快照，默认 `captured_at DESC, id DESC`。

单个 Snapshot 的响应结构：

```json
{
  "id": "00000000-0000-0000-0000-000000000003",
  "platform_account_id": "00000000-0000-0000-0000-000000000002",
  "source": "huitun",
  "source_updated_at": null,
  "captured_at": "2026-08-10T00:00:00Z",
  "metrics": {},
  "import_job_id": "00000000-0000-0000-0000-000000000004",
  "import_row_id": "00000000-0000-0000-0000-000000000005"
}
```

#### `GET /api/v1/influencers/filter-options`

响应只包含：

- `owners`：当前可见 active Influencer 实际引用到的 Owner 最小摘要，字段固定为 `id`、`name`、`status`，按 `name ASC, id ASC` 排序。active Influencer 引用 disabled Owner 时保留 `status=disabled`；不得返回完整公司 Operator 目录，也不得按当前部门缩减。
- `tags`：当前可见 active Influencer 的 active PlatformAccount `source_tags` 中实际存在的 trim 后原值；按原值去重并确定性排序，不截断或回写历史值。超过 160 字符的历史值仍返回用于展示，但前端必须禁用其筛选动作，因为 `tag` 请求上限固定为 160。
- `crm_stages`：当前 `CRMStage` enum 的完整值列表，按 enum 定义顺序返回。

响应形状：

```json
{
  "owners": [
    {
      "id": "00000000-0000-0000-0000-000000000006",
      "name": "示例操作人",
      "status": "active"
    }
  ],
  "tags": ["示例赛道"],
  "crm_stages": [
    "待开发",
    "已发送邮件",
    "第一次跟进",
    "第二次跟进",
    "已回复",
    "已加微信",
    "沟通中",
    "潜在合作",
    "高意向",
    "暂不考虑",
    "长期维护",
    "已结束"
  ]
}
```

该对象作为统一 envelope 的 `data` 返回。示例中的 Owner 和 Tag 是字段形状示例；`crm_stages` 是完整 enum 列表。

静态 `/filter-options` 必须在动态 `/{influencer_id}` 之前注册并有 HTTP 回归测试，避免被 UUID 动态路由捕获后返回错误的 422。

## 23. Repository、Service、Schema、Router 的职责边界

唯一实现位置：

```text
packages/backend_core/src/backend_core/influencers/
├── models.py       # ORM 持久化模型
├── enums.py        # 领域枚举
├── schemas.py      # API 无关的输入/输出契约
├── repository.py   # SQL、预加载、分页、总数与数据库并发原语
└── service.py      # 认证上下文、公司级读取权限、字段裁剪与响应组装

apps/api/app/http/influencers.py
└── HTTP 参数、Depends、状态码、envelope 转换
```

职责：

- Repository：只负责 SQL、只读查询、分页、总数和预加载，不决定角色权限、Email 去重或标签业务语义。
- Service：公司级可见性、RBAC、Contact 完整/脱敏字段裁剪、关联完整性和空值规则；不提供 Phase 1C 写方法。
- Repository/Service/Schema 必须把 Metrics 作为 `source + metrics JSON` 的通用数据处理；不得包含 Huitun 表头、`if source == huitun` 分支或平台专用业务模型。
- Schema：稳定输入/输出类型、枚举、分页和字段验证；不依赖 FastAPI。
- Router：HTTP 适配、依赖注入、请求上下文、响应转换；不得复制 Service 规则。
- Web：消费 API，不重新实现权限、去重、来源新鲜度或指标合并。
- Worker：Phase 1C 不新增业务 Worker；Phase 1B Import Worker 继续调用同一 `backend_core`。

## 24. Schema 结论与已落地查询层

现有 Phase 1B Schema 已足以实现冻结的只读达人库，Phase 1C 没有持久化 Schema blocker，也没有新增数据结构：

1. Repository、Service、Schema 和 Router 已实现列表、filter-options、详情和 Snapshot 查询。
2. Followers 继续以 CurrentMetrics JSONB 为唯一真相源。Repository 使用安全类型谓词和安全 cast 处理数值、0、null、缺失键与遗留非法值；非法或缺失值不命中筛选。
3. 赛道继续以 active PlatformAccount 的当前 `source_tags` JSONB 投影为查询真相源，使用 trim 后原值完整精确匹配。
4. `Influencer.display_name` 与来源 `account_name` 是两个有意不同的事实；搜索同时覆盖主体名和 active 账号名，不修改任何数据。
5. 没有 Primary PlatformAccount、Primary Metric、Primary Contact、Manual Tag 和 Snapshot trigger 是已经接受并保持的设计，不是 Schema 缺口。
6. 查询在没有新增索引的前提下通过了真实 PostgreSQL 16 正确性门禁；性能优化仍必须以未来真实证据为前提。

## 25. 实际新增的表、字段、约束和索引

Phase 1C 实际新增数量为：

- 新表：0
- 新字段：0
- 新约束：0
- 新索引：0
- 新 PostgreSQL extension：0
- 新 Audit enum 值：0

本阶段不创建 Tag 表、Manual Contact 字段、Primary 标记、Followers 投影列、JSONB 表达式索引或 `pg_trgm`。

非阻塞未来优化：若真实 PostgreSQL 测试、`EXPLAIN` 和实际数据量证明需要性能优化，可另行设计 additive migration，例如活跃列表组合索引、Contact/CurrentMetrics/Snapshot 读取索引、Source Tag GIN、Followers 安全表达式索引或搜索索引。该未来工作不属于 Phase 1C 当前实施要求，不得先于证据落地。

## 26. Migration 结果

Phase 1C 实现未创建 `0004` migration，也没有为了阶段编号创建空 migration。现有 Alembic head 保持 `0003_phase1b`。

最终实现门禁已完成以下回归：

1. 现有 `0001` 至 `0003_phase1b` 空库全链 upgrade。
2. 重复 `upgrade head`。
3. `alembic check` 和 ORM metadata 一致性。
4. Phase 1B 既有 migration 与数据持久化回归。

Phase 1C 不修改、不回填、不重写 SourceState、ImportRow、Contact provenance、CurrentMetrics 或 Snapshot 内容，因此没有 Phase 1C 数据 migration 回滚步骤。

## 27. 前端页面、路由和组件边界

冻结路由：

- `/influencers`：达人列表。
- `/influencers/[id]`：达人详情。

已实现组件边界：

```text
apps/web/src/features/influencers/
├── api.ts
├── formatters.tsx
├── influencer-detail-workspace.tsx
├── influencer-workspace.tsx
├── types.ts
├── queries.ts
└── components/
    ├── influencer-filter-bar.tsx
    ├── influencer-table.tsx
    ├── influencer-pagination.tsx
    ├── influencer-detail.tsx
    ├── platform-account-section.tsx
    ├── contact-section.tsx
    ├── current-metrics-section.tsx
    ├── metric-snapshot-list.tsx
    └── source-provenance-section.tsx
```

边界规则：

- 优先原样复用当前 `AuthShell`、登录、Session 恢复、Operator Modal 和 ImportWorkspace，不为 Phase 1C 主动重构认证壳。只有新路由确实无法接入时，才允许最小必要调整，并必须有 Phase 1A/1B 回归证据。
- 列表分页、搜索、筛选状态写入 URL query string；React Query key 包含全部查询参数。
- 返回详情后浏览器 Back 必须恢复列表状态。
- 页面必须有 loading、empty、error、retry 和缺失值展示状态。
- Contact 不得写入 console、埋点或错误消息。
- Viewer 控件隐藏仅用于 UX，权限最终由后端判断。
- 不创建 AI、触达、CRM、备注占位 Tab。
- 不为 Phase 1C 顺便重写全站状态管理或 ImportWorkspace。

## 28. 后端测试计划

### 28.1 Repository

- 一行一个 Influencer，多个账号/Contact/Metric/Source Tag 不导致重复。
- `total` 与去重后的主体数一致。
- 默认排序与相同排序值的 `id` tie-breaker。
- 页边界、空页、非法分页。
- 昵称中文、大小写、首尾空白和 `%/_` 字面转义。
- `tag`、Followers 范围、Owner、CRM Stage 各自及跨字段 AND 组合；重复同字段参数返回 422。
- Followers 为数字、0、null、缺失键和遗留非法值时的安全行为。
- Source Tag 原值精确匹配、160 字符边界和 active PlatformAccount 限制。
- 只返回 active 且未软删除 Influencer；disabled Owner 摘要仍准确显示。
- 详情和 Snapshot 分页 404/空值行为。

### 28.2 Service

- 四个正式角色的公司级读取；Owner/部门不缩小可见范围。
- 未认证请求拒绝。
- Session 权限不受所选 Operator 角色影响。
- 只返回真实 CurrentMetrics/Snapshot，不推算。
- SourceState `creator_tags` 只用于详情追溯，赛道筛选只查 active 账号 `source_tags`。
- `super_admin`、`manager`、`operator` 返回完整 current Contact；`viewer` 对任一非空 `display_value` 固定返回 `***`。
- 普通 GET 不创建业务 Audit；异常和日志不包含 Contact 原文或凭证。
- Service 不暴露 Owner、Tag、Contact、CRM Stage、来源数据、CurrentMetrics 或 Snapshot 写方法。

### 28.3 API

- 四个冻结 GET 的统一 envelope、request ID、401、404、422 和分页结构。
- 四个角色的公司级读取与 Contact 字段差异。
- GET 不要求 CSRF；缺少有效 Session 仍返回 401。
- `/filter-options` 静态路由不会被 `/{influencer_id}` 捕获。
- 未定义的 Influencer POST、PUT、PATCH、DELETE 不存在可调用路由。

不在本文虚构测试数量；数量由实际实现和覆盖矩阵产生。

## 29. PostgreSQL 集成测试计划

SQLite 不能替代以下真实 PostgreSQL 门禁：

- ILIKE 与 JSONB 的真实 PostgreSQL 语义。
- 多账号、多 Contact、多 Source JOIN 下列表主体和 `total` 去重。
- 相同排序值下页间不重复/不漏项。
- JSONB Followers 数字 cast、缺失键、null 和非法遗留值。
- active PlatformAccount Source Tag 原值精确匹配。
- Snapshot 时间线稳定排序。
- 硬身份三个唯一约束；相同 Email 不触发 Influencer 唯一冲突。
- 现有 `0001` 至 `0003_phase1b` fresh/repeat upgrade、`alembic check` 和 ORM metadata 一致性；不存在 0004。
- 保留并执行 Phase 1B 的 Confirm 幂等和并发 stale 测试。

测试数据库必须继续使用显式安全门和专用测试库名，不接触项目开发/生产卷。没有已确认 SLA 时，不以虚构毫秒阈值作为验收。

## 30. 前端测试和 E2E 计划

### 30.1 Vitest / Testing Library

- API query 编码、统一错误和 abort 行为。
- 列表 loading、error、empty、data。
- 搜索提交/清空。
- 每个筛选和组合筛选。
- 翻页、返回恢复、URL query 与 Query key 一致。
- null/`—`/0/false 的区别展示。
- 多账号、多来源指标和疑似重复 Contact 文案。
- 详情各已确认区块；不存在后续业务占位 Tab。
- Contact 按冻结权限完整/脱敏显示，且不输出 console、埋点或错误消息。
- 直接访问路由时的 Session 恢复和 Operator 选择流程。
- Viewer 只读 UI；API 测试仍是权限权威。
- 现有 AuthShell、API client 和 ImportWorkspace 测试继续通过。

### 30.2 浏览器 E2E

真实浏览器 + Docker Compose E2E 是 Phase 1C 验收门禁。当前仓库没有 Playwright；本阶段不强制、也不授权仅为 Phase 1C 引入 Playwright 或其他新 E2E framework，优先复用 Phase 1B 已有的真实 Compose/E2E 验证方式：

1. 登录并选择 Operator。
2. 进入达人列表。
3. 搜索、逐项筛选、组合筛选、分页。
4. 打开详情和历史快照，再返回保持列表状态。
5. 使用 Phase 1B 脱敏 37 列 Fixture 完成 Import Confirm，确认新达人可在列表/详情出现。
6. 再导入更新数据，确认当前指标规则、历史快照和人工保护不变。
7. Viewer 可读且 Contact 脱敏，其他正式角色可读取完整 current Contact。
8. 四个冻结 GET 可用，且不存在 Influencer 写入口。

## 31. Phase 1B 回归要求

最终实现门禁已重新通过全部现有 Phase 1A/1B 测试，并特别覆盖：

- CSV/XLSX 安全解析和 37 列 Mapping。
- Preview 不写业务表。
- Confirm 原子性、幂等和 Preview Revision stale 保护。
- 三种硬身份去重与冲突人工审核。
- Email 永不硬匹配，只标疑似重复。
- Newer/Same/Older/Unknown 新鲜度规则。
- 同来源 Contact 观察更新 first/last provenance。
- 现有人工 Owner 和人工 Contact 不被来源导入覆盖。
- CurrentMetrics 非破坏性合并。
- Snapshot 追加、幂等和历史不变。
- PostgreSQL 并发 Confirm 门禁。
- 仓库内脱敏 Fixture；仓库外真实样本门禁保持可运行但不得复制真实文件。
- 登录、Session、Operator 不提权、RBAC、CSRF 和 Audit 基线。

## 32. 已完成的实施切片及每个切片的完成条件

以下切片均已完成；完成状态不等于人工最终验收已经签署。

### 1C-00 需求冻结（已完成）

- 完成本文 35 项覆盖。
- 19 项人工决策全部正式写回正文和第 35 节。
- 文档状态变为 `DESIGN FROZEN — Phase 1C Task 0`，无阻塞性待决事项。
- 不修改代码或数据库。

### 1C-01 backend_core 查询契约与只读能力（已完成）

- 最终 List/Detail/Filter/Snapshot Schema 可生成确定 OpenAPI。
- Repository/Service/Schema 边界落地。
- 单元测试覆盖查询、过滤、分页、关联和权限。
- 无业务规则进入 API Router。
- Followers 使用 JSONB 安全谓词/cast；不创建 migration、投影或新索引。

### 1C-02 FastAPI 只读接口（已完成）

- 四个冻结 GET 全部使用统一 envelope，并且没有 Influencer 写 endpoint。
- 401/404/422、公司级可见性和 OpenAPI 验证通过。
- HTTP 与核心鉴权边界清晰。

### 1C-03 Web 列表（已完成）

- 路由、筛选、搜索、稳定分页、URL 状态、loading/error/empty 完成。
- 优先复用现有 AuthShell；只允许新路由所需的最小调整。
- 不破坏 Phase 1B ImportWorkspace。

### 1C-04 Web 详情（已完成）

- 仅实现第 5 节区块。
- CurrentMetrics 与 Snapshot 语义清晰。
- Contact 权限和空值规则符合冻结协议。

### 1C-05 集成、回归与验收（已完成，等待人工最终验收）

- 后端、HTTP、PostgreSQL、Web、E2E 测试完成。
- Phase 1A/1B 全量回归通过。
- 现有 migration 链、Compose 和七服务门禁通过。
- 更新交付文档后停止，不进入下一阶段。

## 33. 最终验收清单

以下勾选表示工程自检与运行门禁已经通过，不代表人工最终验收已经签署。

### 功能

- [x] 一行一个 Influencer 的稳定分页。
- [x] 昵称搜索仅覆盖主体名和 active 账号名，转义 wildcard，最大 160 字符。
- [x] `tag` 仅精确匹配 active 账号当前 `source_tags` 原值。
- [x] Followers 闭区间按任一 active 账号任一真实来源命中，不聚合、不择优、不推算。
- [x] Owner 精确筛选和 null“未分配”展示正确。
- [x] CRM Stage 只读展示与筛选。
- [x] `filter-options` 只返回实际 Owner 最小摘要、实际 Source Tag 和 CRM enum。
- [x] 三个非 Viewer 角色读取完整 current Contact，Viewer 只读脱敏值；Email 仅疑似重复。
- [x] 详情仅含主体、账号、来源、Contact、真实指标、历史快照和标签。
- [x] CurrentMetrics 与 Snapshot 明确区分。
- [x] 新导入不错误覆盖人工数据或历史。
- [x] 列表和详情只返回 active 且未软删除 Influencer，disabled Owner 状态仍准确。

### 架构与安全

- [x] 业务规则只在 `packages/backend_core`。
- [x] Router 只做 HTTP 适配。
- [x] 后端执行 RBAC；Operator 不提权。
- [x] 仅存在四个冻结 GET，不存在 Influencer POST/PUT/PATCH/DELETE。
- [x] 普通 GET 不产生业务 Audit，Phase 1C 不新增 AuditAction。
- [x] Contact、Token、Secret 不进入日志、console、埋点、错误消息或 Audit。
- [x] 无物理删除、无来源追溯破坏、无 Snapshot 修改入口。
- [x] 没有 AI/Campaign/邮件/CRM/Analytics/多平台 Connector 越界。

### 测试与运行门禁

- [x] `make lint`
- [x] `make test`
- [x] `make health`
- [x] `make compose-validate`
- [x] Docker 镜像重建及七服务健康。
- [x] 现有 `0001` 至 `0003_phase1b` Alembic fresh/repeat/check；未创建 0004 或空 migration。
- [x] PostgreSQL 专项集成测试。
- [x] 复用现有方式完成真实浏览器 + Compose E2E；未引入新 E2E framework。
- [x] Phase 1A/1B 全量回归。
- [x] 实际结果、文件清单、已知问题、CHANGELOG 和验收自检已更新；未虚构测试数量。

## 34. 风险与回滚策略

| 风险 | 影响 | 预防/检测 | 回滚 |
|---|---|---|---|
| 多表 JOIN fan-out | 重复达人、错误 total | `EXISTS`/子查询、PG 组合测试 | 回退查询实现，不改数据 |
| 多账号/多来源组装错误 | 展示或筛选误导 | 主体一行、账号/来源数组、PG Fixture | 回退只读组装代码，保留数据 |
| JSONB Followers cast | 遗留非法值导致查询失败 | 安全类型谓词、安全 cast、PG 测试 | 回退筛选代码，继续使用原 JSONB |
| Source Tag 原值精确匹配 | 大小写或 Unicode 不同导致不命中 | UI 只使用 filter-options 实际值；边界测试 | 关闭 tag UI 参数，不改历史标签 |
| 超过 160 字符的历史 Tag | 可展示但不能作为筛选输入 | 明确 UI 状态与 422 测试，不截断 | 保留展示，禁用该值筛选 |
| Offset 分页并发位移 | 跨页重复/遗漏 | 稳定排序 + id；UI 不宣称快照 | 后续兼容引入 cursor，不破坏数据 |
| Contact 暴露 | 敏感信息越权 | 后端字段级策略、无日志、E2E | 立即关闭值字段，仅保留摘要 |
| Snapshot 被误改 | 历史审计破坏 | 无写入口、RESTRICT、集成测试 | 阻止发布；已有历史不得靠重建伪造 |
| 新路由接入影响认证壳 | 登录/Import 失效 | 优先原样复用 AuthShell；Phase 1A/1B 回归 | 回退最小路由/UI 变更，不回滚数据 |
| 无新增性能索引 | 数据量增长后查询可能变慢 | 真实 PG 测试与查询计划；不虚构 SLA | 先回退高成本 UI 查询，另开 additive 优化设计 |
| SourceIdentity last import 语义误标 | UI 误称“最后观察” | 使用准确字段名和详情测试 | 修正文案，不改 Phase 1B 数据 |
| Phase 1B Generic 外部身份交叉冲突 | Confirm 可能 stale/失败 | 保留已知风险与回归，不在 1C 重写 Matcher | 回到 Preview/人工处理，不绕过唯一约束 |

Phase 1C 没有 Schema migration 或新增写路径。回滚只需要撤销达人库只读代码、Router 和 UI，现有数据保持不变；不得通过删除 ImportRow、SourceState、Contact provenance 或 Snapshot 来“修复”任何问题。

## 35. 已关闭决策记录

人工审核已于 2026-08-10 关闭 DN-01 至 DN-19。以下是正式设计依据，不再是待决策项。

| ID | 最终决定 |
|---|---|
| DN-01 | 正式角色保持 `super_admin / manager / operator / viewer`；Admin、Member 分别只是 `super_admin`、`operator` 的文档显示别名；不改 Role enum、认证、Session 或数据库。 |
| DN-02 | Phase 1C 新增能力完全只读；不实现 Owner、Manual Tag、Manual Contact 或任何其他 Mutation；Phase 1B Import 写能力不变。 |
| DN-03 | 赛道只查询 active PlatformAccount 当前 `source_tags`；SourceState canonical `creator_tags` 只用于详情追溯；不建 Manual Tag、赛道字典或 Tag 表。 |
| DN-04 | `filter-options.owners` 只返回当前可见 active Influencer 实际引用的 Owner 最小摘要；不返回完整 Operator 目录，不设计 Owner Mutation。 |
| DN-05 | 列表一行一个 Influencer，PlatformAccount 和 CurrentMetrics 为数组，无 Primary。Followers 由任一 active 账号任一真实来源闭区间命中；缺失不命中，不求和、不取最大、不设来源优先级、不推算。 |
| DN-06 | `super_admin`、`manager`、`operator` 读取完整 current Contact；`viewer` 对任一非空 `display_value` 固定读取 `***`，不暴露原值字符或长度；普通 GET 不产生业务 Audit，完整值不进入任何日志或客户端诊断渠道。 |
| DN-07 | `q` 搜索 `Influencer.display_name` 与 active `PlatformAccount.account_name`；trim、不区分大小写 contains、转义 wildcard、最大 160 字符；不搜索其他字段。 |
| DN-08 | 只支持 `tag`、`followers_min`、`followers_max`、`owner_operator_id`、`crm_stage`；字段间 AND，不支持同字段多值，不增加额外筛选。 |
| DN-09 | `page=1`、`page_size=50`、最大 100，均为正整数；超页 HTTP 200、空 items、真实 total。 |
| DN-10 | 列表默认 `created_at DESC, id DESC`；Snapshot 默认 `captured_at DESC, id DESC`；嵌套集合确定性排序。 |
| DN-11 | 单值缺失为 null、集合缺失为 `[]`、UI 为 `—`；0/false 不是 unknown，普通筛选不匹配缺失，Owner null 显示“未分配”。 |
| DN-12 | Source Tag 输入先 trim，trim 后为空等同未传；非空值使用 trim 后原值完整精确匹配；无 NFC/casefold/规范化表/同义词；输入最大 160 字符，历史值不截断、不回写。 |
| DN-13 | 因 Phase 1C 完全只读，本阶段 N/A；不增加 Mutation 权限矩阵或 AuditAction。 |
| DN-14 | 只返回 `status=active AND deleted_at IS NULL`；不提供 status 筛选、disabled 浏览、恢复或删除；disabled Owner 摘要仍准确显示。 |
| DN-15 | Snapshot 使用独立分页 GET，按 `captured_at DESC, id DESC`；依靠无写 Service/API、现有 FK/RESTRICT 和回归保证不可变，不加 trigger。 |
| DN-16 | Followers 保持 CurrentMetrics JSONB 真相源，使用安全类型谓词/cast；不投影到 Influencer，不强制 pg_trgm，不建表达式索引或投影列。 |
| DN-17 | 只展示 `possible_duplicate_contact`，不创建处理闭环。 |
| DN-18 | 返回全部 current Contact，不创建 Primary Contact 或 `is_primary`。 |
| DN-19 | 按现有 SourceIdentity 语义准确展示，不把未刷新的 last import 字段命名为“最后观察”，不修改 Phase 1B observation 逻辑。 |

### 非阻塞未来事项（不属于 Phase 1C）

- 真实 PostgreSQL 测试和查询计划如证明需要性能优化，可另行设计 additive migration；当前不创建 0004、索引投影或 `pg_trgm`。
- Owner 维护、Manual Tag、Manual Contact、疑似重复 Email 处理闭环、Primary Account/Contact、Contact 导出均需未来独立范围冻结。
- SourceIdentity observation 刷新规则、Phase 1B Generic 跨键冲突可在独立 Phase 1B 修订中评估；Phase 1C 不修改。
- 抖音、视频号、快手及其他 Connector 属于后续阶段。
- Docker buildx 仍是正式部署前事项；不阻塞 Phase 1C。
- Web CSRF Cookie 名可配置一致性是既有工程风险；Phase 1C 不修改认证或 Cookie 配置。

## 36. 35 项覆盖自检

| 用户要求 | 覆盖位置 | 状态 |
|---|---|---|
| 1 阶段目标 | §1 | 已覆盖 |
| 2 非目标 | §2 | 已覆盖 |
| 3 代码基线 | §3 | 已覆盖 |
| 4 列表字段 | §4 | 已冻结 |
| 5 详情区块/字段 | §5 | 已覆盖 |
| 6 搜索语义 | §6 / §35 DN-07 | 已冻结 |
| 7 每个筛选语义 | §7 / §35 DN-03/05/08/12 | 已冻结 |
| 8 分页协议 | §8 / §35 DN-09 | 已冻结 |
| 9 稳定排序/tie-breaker | §9 / §35 DN-10/15 | 已冻结 |
| 10 空值规则 | §10 / §35 DN-11 | 已冻结 |
| 11 关联规则 | §11 | 已覆盖 |
| 12 当前指标/快照 | §12 | 已覆盖 |
| 13 Source/Manual Tag 边界 | §13 / §35 DN-03/12 | 已冻结；Manual Tag 不纳入 |
| 14 硬身份去重 | §14 | 已覆盖 |
| 15 Email 疑似重复 | §15 / §35 DN-17 | 已冻结；禁止自动合并 |
| 16 允许写操作 | §16 / §35 DN-02 | 已冻结为完全只读 |
| 17 禁止写操作 | §17 | 已覆盖 |
| 18 Owner/Tag/Contact 是否纳入 | §18 / §35 DN-02/03 | 已冻结 |
| 19 权限矩阵 | §19 / §35 DN-01/06/13 | 已冻结为读取矩阵 |
| 20 CSRF | §20 | 已覆盖 |
| 21 Audit | §21 | 已冻结；不新增事件 |
| 22 API | §22 | 已冻结为四个 GET |
| 23 分层职责 | §23 | 已覆盖 |
| 24 Schema 缺口 | §24 | 已覆盖 |
| 25 新表/字段/约束/索引 | §25 / §35 DN-03/16 | 已冻结为全部 0 |
| 26 Migration | §26 | 已冻结；不创建 0004 或空 migration |
| 27 前端边界 | §27 | 已覆盖 |
| 28 后端测试 | §28 | 已覆盖 |
| 29 PostgreSQL 测试 | §29 | 已覆盖 |
| 30 前端/E2E | §30 | 已覆盖 |
| 31 Phase 1B 回归 | §31 | 已覆盖 |
| 32 实施切片/DoD | §32 | 已覆盖 |
| 33 最终验收 | §33 | 已覆盖 |
| 34 风险/回滚 | §34 | 已覆盖 |
| 35 所有原待决问题 | §35 | 19 项全部关闭并记录 |

自检结论：全文不存在阻塞性待决事项。Phase 1C 的冻结设计、代码实现与工程门禁均已完成，状态为等待人工最终验收；这不代表生产上线，也不授权开始 Phase 2。

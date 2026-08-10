# Phase 1B Import Design

状态：已批准。用户于 2026-08-10 确认“持久化 Preview Plan + 异步两阶段执行 + Preview Revision 失效保护”，并授权完成设计自检后直接实施。

## 1. 范围

本阶段只实现 Collection Job、安全 CSV/XLSX 上传、Storage Adapter、Import Job/Row、Parser、Huitun/Generic Adapter、Mapping、Normalize、Preview、Confirm、公司级去重所需的最小 Influencer/PlatformAccount/Contact/Metric 模型、Audit、RBAC 和最小导入 UI。不实现完整达人库、Campaign、AI、邮件、Inbox、CRM、Analytics、Playbook、抖音、视频号、需求池、跨部门派单或成交链路。

长期模型固定为“Influencer 达人主体 + PlatformAccount 平台账号 + Source 数据来源”。一个 Influencer 可关联多个平台账号；平台 ID、主页和平台公开资料不得直接成为 Influencer 主体字段。Phase 1B 只启用 platform=xiaohongshu，但通用 Matcher、Freshness、Contact 和 Metric 不依赖 Huitun 或小红书字段名。

真实样本事实：UTF-8 BOM、逗号分隔、50 条记录、37 列且行宽一致；不存在 huitun_id；“灰豚指数”是 decimal 评分；50 个 Profile URL 均可提取唯一 xhs_profile_id；9 个有效 Email、41 个缺失 Email、0 个无效 Email；样本内无硬重复或重复 Contact。

## 2. 数据模型与 Migration 0003

所有表使用 UUID 主键及 created_at/updated_at。新增 PostgreSQL Enum、约束和索引。

### stored_import_files

- sha256 唯一、storage_key 唯一、size、detected_type、detected_mime、encoding、expires_at。
- 原始文件名不用于磁盘路径。相同内容复用 Storage Object，但每次上传建立独立 Import Job/Audit。

### collection_jobs

- name、industry、subdirection、purpose、target_action、follower_min/max、target_count。
- department_id、owner_operator_id、source_type=manual_huitun_export、status、备注。
- 普通角色只能访问本部门；Super Admin 可跨部门。

### import_jobs

- collection_job_id、department_id、operator_id、stored_file_id。
- original_filename、mime_type、file_size、sha256、source_type、status。
- detected_fields、field_mapping、mapping_hash、preview_revision、preview_summary、result。
- total/valid/warning/error/created/updated/no_change/skipped/manual_review rows。
- confirmed_revision、parse_task_id、confirm_task_id、confirmed/completed_at、error_code/message。
- 状态：uploaded、parsing、mapping_required、previewing、preview_ready、preview_stale、confirm_queued、importing、completed、failed、cancelled。

### import_rows

- import_job_id + row_number 唯一。
- raw_data 完整保存原字符串；normalized_data 使用 Canonical Field。
- matched_influencer_id、matched_platform_account_id、match_type、action、merge_plan、warnings、errors。
- preview_revision、plan_hash、committed_action、committed_at。
- action：create、update、no_change、skip、error、manual_review；match_type 不包含 Email。

### influencers

- 公司级共享。
- 只表示跨平台达人主体：display_name、owner_operator_id、crm_stage、status、deleted_at。
- 不保存 xhs_profile_id、xhs_account_id、douyin_id 或 wechat_channels_id。
- Import 创建主体时可从首个平台账号初始化 display_name；已有主体的人工业务字段永不被 Import 覆盖。

### influencer_platform_accounts

- influencer_id、platform、platform_account_id、account_name、account_handle、profile_url、normalized_profile_url、source、is_active。
- bio、gender、region_raw、verification_info、mcn_name、source_tags、creator_level、is_brand_partner 等平台公开资料。
- partial unique(platform, platform_account_id) 与 partial unique(platform, normalized_profile_url) 提供数据库级硬去重；account_handle 仅用于潜在匹配/Warning，不作为本阶段硬主键。
- 当前 Adapter 将 xhs_profile_id 映射为 platform_account_id，将 xhs_account_id 映射为 account_handle。当前样本没有 huitun_id，既不生成也不误用评分字段。

### influencer_source_states

- influencer_id、platform_account_id、source；platform_account_id + source 唯一。
- source_updated_at、source_data、source_data_hash、state_version、last_import_job_id/row_id。
- Freshness 只比较同 Source 状态，不使用 influencers.updated_at。

### influencer_contacts

- influencer_id、platform_account_id nullable、type、value、normalized_value、source、validation_status、is_current、possible_duplicate_contact。
- first_seen_at、last_seen_at、source_updated_at、first/last_import_job_id。
- 仅建立达人内部的合理唯一约束；不同达人允许相同 Email。manual Contact 永不被 Huitun Import 修改或删除。

### influencer_current_metrics

- influencer_id、platform_account_id、source；platform_account_id + source 唯一；source_updated_at、metrics、metrics_hash、last_import_job_id/row_id。
- 作为快速查询投影，只由更新的同源数据刷新。

### influencer_metric_snapshots

- influencer_id、platform_account_id、source、source_updated_at、import_job_id/row_id、captured_at、metrics、metrics_hash。
- 历史不可变；唯一(platform_account_id, source, source_updated_at, metrics_hash) 防止重复快照。

AuditAction 扩展 Import 上传、Mapping 更新、Preview 创建/重建、Confirm 请求、Preview Stale、完成、失败、取消。Audit after 只保存 job/revision/reason/count，不保存 Contact 原文。

## 3. Parser、Adapter 与安全

- StorageProtocol 与 LocalStorageAdapter 位于 backend_core；Local 实现使用 `/data/imports`、随机 key、原子落盘和流式 SHA-256。
- 默认最大文件 25 MiB，可通过环境变量收紧；只允许 `.csv`/`.xlsx`。
- 服务端同时检查扩展名、声明 MIME、magic/content 和实际 Parser；拒绝 `.xlsm`、OLE、脚本和伪装类型。
- CSV 严格解析 UTF-8/UTF-8 BOM，并可安全探测 GB18030；记录编码和分隔符，拒绝空文件、重复 Header、异常行宽。
- XLSX 在解压前检查 entry 数、路径穿越、加密、宏/ActiveX/external link、总解压大小和压缩比。使用 openpyxl read-only；公式绝不执行，公式单元格忽略并产生 Warning。
- Parser 产生 RawTabularRecord；Source Adapter 输出通用 CanonicalInfluencerRecord，其中身份结构为 platform_identity(platform, platform_account_id, account_handle, profile_url)，公开资料、Contact 和 Metrics 分区明确。HuitunCsvAdapter/HuitunExcelAdapter 的中文字段映射集中在单一 mapping 定义；GenericCsvAdapter 只使用 Canonical Header/显式 Mapping。通用 Planner 不读取灰豚中文列名，也不引用 xhs_profile_id 变量名。
- `--`、空串、纯空白和大小写 NULL 标准化为 null，但 raw_data 不变。
- Profile Normalizer 只接受小红书 profile URL，去 query/fragment/trailing slash，提取 xhs_profile_id。
- Email trim、domain lowercase、基础语法校验；无效不写正式 Contact；Bio 中 Contact 不提取。
- 明确可靠的复合字段可解析附加结构值，任何失败仅 Warning，raw 字段始终保留。

## 4. Planner、Freshness 与 Plan Hash

Preview 与 Confirm 共用 Matcher、FreshnessEngine、MergePlanner 和 PlanValidator。

匹配顺序由 Adapter 产生的通用 IdentityCandidate 驱动：platform+platform_account_id、数据源真实 external account id（当前样本无）、platform+normalized_profile_url。Matcher 先匹配 PlatformAccount，再取得 Influencer 主体。Email 永不匹配 Influencer。身份冲突进入 manual_review。

同源合并规则：新时间可更新 PlatformAccount 公开资料；旧时间不覆盖；同时间同值 no-op、补空允许、非空冲突保留 Existing 并 Warning；无时间只补空。incoming null 永不覆盖非空。account_handle 变化但 platform_account_id 相同时保持同一 PlatformAccount/Influencer，较新值可更新并产生 ACCOUNT_HANDLE_CHANGED。人工业务数据永不进入 merge_plan。

每个 Preview 递增 import_jobs.preview_revision。Mapping 变化、重 Parse 或重 Preview 都创建新 revision。

Plan Hash 使用 canonical JSON（UTF-8、key 排序、稳定 decimal/datetime/null 表达）加 SHA-256。输入包含 job/row identity、normalized_data、matched Influencer/PlatformAccount、match id/type、action、merge plan、source time、相关 PlatformAccount Source State hash/version、current metrics hash、相关 Contact source state、mapping_hash、preview_revision。不包含人工 Notes、CRM 等不相关状态。

Confirm 携带 preview_revision。PlanValidator 在任何写入前锁定 Job、取得按 identity 排序的 PostgreSQL advisory locks/row locks，并重新计算同一 Planner 的 relevant preconditions。任何 action/match/merge/hash 漂移都使业务写入事务整批回滚；随后用独立状态事务将 Job 标记为 preview_stale 并记录 PREVIEW_STALE。用户显式点击重新生成后才进入 previewing，不静默改变 action 或自动 Confirm。

## 5. 状态机、异步任务与幂等

合法主流程：uploaded→parsing→mapping_required 或 previewing→preview_ready→confirm_queued→importing→completed。并发相关状态变化允许 confirm_queued/importing→preview_stale→previewing→preview_ready。任一处理态可进入 failed；非 completed/importing 可取消。非法跳转由核心状态机拒绝。

Celery 只有两个入口：parse_import_job(import_job_id) 与 confirm_import_job(import_job_id, preview_revision)。任务只创建 DB/Storage 依赖并调用 backend_core service。

Parse 先在事务外安全读取文件，在单一 DB 事务中替换该 revision 的 rows/preview；失败不留下半套 Preview。Confirm 当前规模采用每 Job 单一 PostgreSQL 事务，任何 Influencer/PlatformAccount/Contact/Source State/Snapshot 写入失败全部回滚。

同 Job completed + 同 revision 再 Confirm 返回已有 result；confirm_queued/importing 不发第二个 task。数据库约束和锁是最终并发保障。相同文件的新 Job 保留独立 Audit，但重新匹配和 freshness 不产生重复达人或快照。

## 6. API

所有 mutation 要求已认证、已选择 Operator、CSRF 和 Department Role≥operator；Viewer 仅可读。Manager/Operator 仅本部门，Super Admin 可全部。

- POST `/api/v1/collection-jobs`
- GET `/api/v1/collection-jobs`
- GET `/api/v1/collection-jobs/{id}`
- POST `/api/v1/import-jobs`：multipart，返回 202。
- GET `/api/v1/import-jobs/{id}`
- GET `/api/v1/import-jobs/{id}/rows`：分页与状态筛选。
- PUT `/api/v1/import-jobs/{id}/mapping`：保存 Mapping、递增 revision、排队 Preview。
- POST `/api/v1/import-jobs/{id}/preview`：显式重建 Preview，返回 202。
- POST `/api/v1/import-jobs/{id}/confirm`：必须提交 preview_revision，返回 202 或幂等结果。
- POST `/api/v1/import-jobs/{id}/cancel`

统一 Envelope。典型错误：INVALID_FILE、FILE_TOO_LARGE、MIME_MISMATCH、UNSAFE_XLSX、MAPPING_INVALID、INVALID_STATE_TRANSITION、PREVIEW_STALE、PERMISSION_DENIED。

## 7. UI

登录后显示最小内部布局和“达人采集”。流程为：创建任务→上传→解析轮询→Mapping→Preview 轮询→统计和行预览→确认→Importing→Result。

Mapping 表明确展示 Source Field→Canonical Field，可修改且禁止多个 Source Field 映射同一单值 Canonical Field。Preview 显示文件名/SHA/type、总/有效、新建/更新/无变化/缺 Email/有效 Email/无效 Email/疑似 Contact/Warning/Error，以及前若干标准化行和错误行。

PREVIEW_STALE 显示固定提示“达人数据在预览后发生了变化，请重新生成预览后确认。”并提供重新生成按钮。Viewer 隐藏 mutation 控件只是体验优化，后端仍强制拒绝。

## 8. 测试与真实样本

建立完全脱敏的 Huitun CSV/XLSX fixture，保留真实 37 Header 和格式特征，不保留真实名称、Profile ID、Email、Bio 或联系方式。覆盖用户列出的文件安全、Mapping、Normalize、去重、Contact、Preview/Confirm、事务、Audit、RBAC、幂等与并发场景。

真实 CSV 不复制进仓库。提供测试/验收入口从外部路径读取，逐行运行真实 Adapter/Planner，并输出聚合统计，不输出原始敏感字段。最终还验证 Migration 首次/重复升级、PostgreSQL 重启持久化、七服务健康及数据库/Redis 无宿主端口。

## 9. 冲突与边界自检

- PRODUCT_PRD 旧的 Email 去重优先级已被 RESOLVED-006 和本阶段明确规则取代，无 blocker。
- DATABASE_SCHEMA 的旧 import/influencer 草案由本阶段可追溯、非破坏模型具体化，不删除未来字段能力。
- 当前样本没有 huitun_id；Huitun Adapter 不生成或误解析。未来 Source Adapter 可提供真实 external account identity，但通用层不硬编码字段名。
- 多平台长期约束已落实为 Influencer+PlatformAccount+Source；本阶段只注册 xiaohongshu，不创建 Douyin/WechatChannels Connector 或后续需求协同业务。
- CRM Stage 只保留最小数据边界，不实现 CRM 业务。
- 不引入 Kafka、Kubernetes、Event Sourcing、CQRS 或 Phase 1C 页面/API。

# 数据库设计

> PostgreSQL

## 0. 文档权威说明

本文件后半部分保留的是早期 V1 长期概念草案，**不是当前物理 Schema，也不是当前 Phase 2 实现授权**。

权威优先级：

1. 当前已部署物理结构：`infrastructure/migrations/versions/0001`–`0003_phase1b` 与 `packages/backend_core` ORM Models。
2. Phase 2 冻结目标：`docs/PHASE_2_SCOPE.md` 与下方“Phase 2 Frozen Delta”。
3. 本文件“Legacy V1 Conceptual Model”：只提供未来域名词参考，任何实现都必须在对应阶段重新冻结。

当前正式基线：

- Phase 1A–1C 使用 `Influencer + InfluencerPlatformAccount + SourceState/SourceIdentity + Contact + CurrentMetrics + MetricSnapshot`，不是把小红书/灰豚字段直接塞入 Influencer。
- Import 使用 `StoredImportFile + CollectionJob + ImportJob + ImportRow`、持久化 Preview Revision 和人工 Confirm。
- 当前 Alembic head 是 `0003_phase1b`。
- Phase 2 Task 0 只冻结设计；仓库中尚不存在 `0004` 或 `0005` migration 文件。

---

## A. Phase 2 Frozen Delta

### A.1 聚合模型

```text
CollectionJob (one originating collection)
  └─ ImportJob (one Bulk Batch)
       ├─ ImportJobFile (one file occurrence)
       │    └─ StoredImportFile (content-addressed/raw blob)
       └─ ImportRow
            └─ ImportJobFile + original row_number
```

不新增 `ImportBatch` 或 `BatchRow`。

### A.2 `import_job_files`（计划由 0004 新增）

- id UUID PK
- import_job_id UUID FK
- stored_file_id UUID FK
- position INT
- client_file_id VARCHAR
- original_filename VARCHAR
- declared_mime VARCHAR nullable
- status ENUM(uploaded, parsing, mapping_required, ready, failed, excluded)
- source_acquired_at TIMESTAMPTZ nullable
- source_acquired_at_origin ENUM(server_default, user_confirmed, legacy_unknown)
- detected_fields JSONB
- field_mapping JSONB
- mapping_hash VARCHAR nullable
- raw_rows INT
- warning_rows INT
- error_rows INT
- error_code VARCHAR nullable
- error_message TEXT nullable
- parse_task_id VARCHAR nullable
- parse_attempts INT
- parse_started_at / parse_completed_at nullable
- excluded_at TIMESTAMPTZ nullable
- created_at / updated_at

约束：

- unique(import_job_id, position)
- unique(import_job_id, client_file_id)
- unique(import_job_id, stored_file_id)
- unique(id, import_job_id)
- position >= 1

语义：

- 同 Job 相同 SHA 会解析到同一个 StoredImportFile，因此命中 unique(job, stored_file) 并幂等返回已有 occurrence。
- 不同 Job 可关联相同 StoredImportFile，但必须创建各自 occurrence 并重新 Parse/Preview。
- 新 Bulk Draft `/import-jobs/{id}/files` 上传缺省 acquisition time 时由 Service 写入服务器接受时间，origin 为 `server_default`；上传显式值或 Preview 前显式 PATCH 的 origin 为 `user_confirmed`。Legacy 单文件兼容 endpoint 保持 acquisition unknown。
- 跨历史 Job 复用 SHA 且没有显式提供 acquisition time 时，文件保持 Preview blocker，直到显式 PATCH 确认/修正时间。
- Legacy occurrence 回填 `source_acquired_at=NULL`、origin=`legacy_unknown`；Migration 不伪造来源观察时间。`legacy_unknown` 必须对应 NULL，其他两种 origin 必须对应非空 acquisition time。
- `preview_revision > 0` 表示文件集合、Mapping 与 acquisition time 已冻结，不新增 `files_frozen_at`。

### A.3 `import_jobs` / `import_rows` / `collection_jobs`（0004 additive changes）

`import_jobs`：

- 状态 enum 增加 `draft`。
- 新增 `failed_stage` nullable（preview/confirm）。
- Legacy 单文件列 `stored_file_id/original_filename/mime_type/file_size/sha256/detected_fields/field_mapping/mapping_hash` 在回填 occurrence 后变为 nullable/deprecated；不再是多文件事实源。
- 继续只绑定一个 originating CollectionJob。

`import_rows`：

- 新增 `import_job_file_id`。
- 删除并替换现有 `uq_import_rows_job_number(import_job_id, row_number)`。
- unique(import_job_file_id, row_number)。
- 复合 FK `(import_job_file_id, import_job_id)`，禁止 Row 指向其他 Job 的文件。
- Freshness 查询索引 `(matched_platform_account_id, committed_at DESC, import_job_id)`。

`0004` 不得脱离应用兼容桥单独部署：现有 `POST /import-jobs` 创建的每个新 Legacy single-file Job 也必须同时创建 position=1、`client_file_id=legacy:{import_job_id}`、acquisition NULL/origin `legacy_unknown` 的 occurrence，现有 Parse/Mapping/Preview 路径同步它的最小文件状态，所有新 ImportRow 写入该 file FK。Phase 1B 单文件回归必须在 `0004` head 通过。

`collection_jobs`：

- 新增 `screening_rules JSONB`，JSON 内固定 `schema_version=1`。
- 新增 `screening_rules_revision INT NOT NULL DEFAULT 1`，CHECK `>=1`。
- JSONB 只保存 schema version/platform/source_tags exact 规则；Followers min/max 继续使用 CollectionJob 现有列。JSON、revision 与范围列共同进入 rule hash；缺失/非法输入产生 UNKNOWN。
- Legacy CollectionJob 回填 `{"schema_version":1,"platforms":[],"source_tags_exact_any":[]}` 与 revision=1。

### A.4 Freshness

- Freshness 粒度为 `PlatformAccount + Source`。
- `last_huitun_observed_at` 只能由成功 Confirm 的文件 lineage 中可靠 `source_acquired_at` 推导。
- `last_huitun_imported_at` 可由成功 ImportRow 的 committed_at 推导，但不得命名为 observed。
- 阈值来自 validated Settings，并按 UTC elapsed duration：fresh `<=7*24h`、aging `>7*24h且<=30*24h`、stale `>30*24h且<=90*24h`、very_stale `>90*24h`。
- 不新增 FreshnessPolicy 表、Influencer.last_observed_at 或 Metrics 投影列。

### A.5 `refresh_queues`（计划由 0005 新增）

- id UUID PK
- department_id UUID FK
- created_by_operator_id UUID FK
- status ENUM(open, exported, completed, cancelled)
- as_of TIMESTAMPTZ
- requested_limit INT
- today_total_limit INT
- refresh_limit INT
- policy_version INT
- criteria_snapshot JSONB
- created_at / updated_at / exported_at / completed_at / cancelled_at

Queue 由 Department 拥有；额度只是用户输入参数，不创建 DailyQuotaPlan。`criteria_snapshot` 是服务器生成的 validated rule/limit 快照，MVP 不接受任意客户端 criteria JSON。

### A.6 `refresh_queue_items`（计划由 0005 新增）

- id UUID PK
- department_id UUID FK
- queue_id UUID FK
- influencer_id UUID FK
- platform_account_id UUID FK
- source
- priority_tier
- priority_reasons JSONB
- identity_snapshot JSONB
- baseline_last_observed_at TIMESTAMPTZ nullable
- baseline_source_updated_at TIMESTAMPTZ nullable
- status ENUM(pending, fulfilled_changed, fulfilled_no_change, stale_return, unresolved, cancelled)
- fulfilled_import_job_id UUID nullable
- fulfilled_import_row_id UUID nullable
- fulfilled_at TIMESTAMPTZ nullable
- last_return_import_job_id UUID nullable
- last_return_import_row_id UUID nullable
- created_at / updated_at

约束：

- unique(queue_id, platform_account_id, source)
- Queue unique(id, department_id)，Item 使用复合 FK `(queue_id, department_id)`。
- active Item 对 `(department_id, platform_account_id, source)` 使用 partial unique，状态范围为 pending/stale_return/unresolved。
- PlatformAccount 必须属于 Influencer。
- Fulfillment 与 Last Return 的 Job/Row 各自 all-null 或 all-non-null，并必须属于同一回流 Import。
- ImportJob 的 `(refresh_queue_id, department_id)` 必须指向同 Department Queue；Operator 保持独立 Audit FK，允许 super_admin 跨部门管理。
- `identity_snapshot` 只保存真实公开 Identity，不保存 Contact 或完整 Metrics。

`import_jobs` 在 0005 增加可选 `refresh_queue_id`。NO_CHANGE 只有在返回文件 `source_acquired_at` 严格晚于非空 Item baseline，且历史复用 SHA 的 acquisition origin 已为 user_confirmed（或 SHA 从未复用）时才可核销；baseline 为空时 NO_CHANGE 保持 unresolved。

### A.7 Raw File Retention

任何仍被 `ImportJobFile` lineage 引用的 StoredImportFile 均不得物理删除。现有 `expires_at` 不是删除授权；Phase 2 MVP 不实现 archive/delete lifecycle。

### A.8 Migration 顺序

1. `0004_phase2_bulk_import`
2. `0005_phase2_refresh_queue`

禁止合并为一个 Migration，也禁止创建空 Migration。`0004 → 0003` 仅在没有 Phase 2 创建的 Bulk Job/新 occurrence、所有 Screening 规则仍是 Migration 默认空值时允许；只由 Migration 回填的 Legacy 单 occurrence 可恢复。不得只在多文件时拒绝，因为单 occurrence Bulk 的 Draft/File/acquisition/Mapping/task metadata 也会丢失。`0005 → 0004` 仅在 Queue/Item 和 Queue 引用全空时允许；其他情况必须安全拒绝。

---

# Legacy V1 Conceptual Model（非当前物理 Schema）

以下第 1–25 节不得用于推导当前表、字段、权限或 Phase 2 实施范围。

## 1. departments

- id UUID PK
- name VARCHAR UNIQUE
- password_hash VARCHAR
- status ENUM(active, disabled)
- session_days INT
- created_at
- updated_at

---

## 2. operators

- id UUID PK
- department_id FK
- name
- role ENUM(super_admin, manager, operator, viewer)
- status
- created_at
- updated_at

Unique:
- department_id + name

---

## 3. sessions

- id UUID PK
- department_id
- operator_id nullable
- token_hash
- ip
- user_agent
- expires_at
- revoked_at
- created_at

---

## 4. influencers

- id UUID PK
- platform ENUM(xiaohongshu, other)
- platform_user_id nullable
- huitun_id nullable
- nickname
- profile_url
- bio
- category
- sub_category
- region
- mcn
- owner_operator_id nullable
- crm_stage
- status
- ai_score nullable
- ai_summary nullable
- last_contacted_at nullable
- next_followup_at nullable
- created_at
- updated_at
- deleted_at nullable

Unique partial indexes:
- platform + platform_user_id
- huitun_id

---

## 5. influencer_metrics

- id
- influencer_id
- followers
- likes_total
- notes_total
- notes_7d
- avg_likes
- avg_collects
- avg_comments
- avg_shares
- viral_rate
- quote_price
- huitun_index
- captured_at
- source

---

## 6. influencer_contacts

- id
- influencer_id
- type ENUM(email, wechat, phone, other)
- value
- normalized_value
- source
- verification_status
- verified_at
- is_primary
- created_at
- updated_at

Unique:
- type + normalized_value + influencer_id

---

## 7. influencer_tags

- id
- influencer_id
- tag
- source ENUM(import, ai, manual)
- created_at

---

## 8. collection_jobs

- id
- name
- department_id
- owner_operator_id
- industry
- sub_industry
- purpose
- target_action
- follower_min
- follower_max
- target_count
- ai_filter_suggestion JSONB
- status
- created_at
- updated_at

---

## 9. import_jobs

- id
- collection_job_id
- source
- filename
- status
- total_rows
- success_rows
- duplicate_rows
- error_rows
- result JSONB
- created_by_operator_id
- created_at
- completed_at

---

## 10. import_rows

- id
- import_job_id
- row_number
- raw_data JSONB
- normalized_data JSONB
- result_status
- influencer_id nullable
- error_reason
- created_at

---

## 11. playbooks

- id
- name
- category
- status
- created_at
- updated_at

---

## 12. playbook_versions

- id
- playbook_id
- version
- content JSONB
- change_note
- created_by_operator_id
- created_at

Unique:
- playbook_id + version

---

## 13. templates

- id
- playbook_version_id
- type ENUM(subject, initial, followup)
- name
- body
- step_index nullable
- status
- created_at

---

## 14. campaigns

- id
- name
- department_id
- owner_operator_id
- collection_job_id nullable
- playbook_version_id
- status
- daily_limit
- send_start_time
- send_end_time
- min_interval_seconds
- max_interval_seconds
- review_mode
- review_count
- config JSONB
- started_at
- paused_at
- completed_at
- created_at
- updated_at

---

## 15. campaign_leads

- id
- campaign_id
- influencer_id
- status
- subject_variant_id nullable
- template_variant_id nullable
- personalization JSONB
- sequence_step INT
- first_sent_at nullable
- last_sent_at nullable
- replied_at nullable
- reply_class nullable
- wechat_added BOOLEAN DEFAULT false
- crm_stage
- stop_reason nullable
- created_at
- updated_at

Unique:
- campaign_id + influencer_id

---

## 16. mailboxes

- id
- name
- email
- provider_type
- encrypted_credentials JSONB
- daily_limit
- today_sent
- status
- bounce_rate
- last_sync_at
- created_at
- updated_at

---

## 17. emails

- id
- campaign_lead_id
- mailbox_id
- step
- subject
- body
- status
- provider_message_id nullable
- idempotency_key UNIQUE
- scheduled_at
- sent_at
- delivered_at
- bounced_at
- replied_at
- created_at
- updated_at

---

## 18. email_events

- id
- email_id
- event_type
- provider_event_id nullable
- payload JSONB
- occurred_at
- created_at

---

## 19. replies

- id
- influencer_id
- campaign_lead_id
- email_id nullable
- provider_message_id
- content_text
- content_html nullable
- received_at
- ai_classification nullable
- ai_confidence nullable
- ai_summary nullable
- suggested_action nullable
- ai_raw_result JSONB nullable
- processed_by_operator_id nullable
- processed_at nullable
- created_at

---

## 20. crm_events

- id
- influencer_id
- campaign_lead_id nullable
- from_stage nullable
- to_stage
- event_type
- note
- operator_id
- created_at

---

## 21. tasks

- id
- influencer_id nullable
- campaign_id nullable
- assigned_operator_id
- type
- title
- due_at
- status
- payload JSONB
- completed_at
- created_at

---

## 22. suppression_list

- id
- contact_type
- normalized_value
- reason ENUM(unsubscribe, hard_bounce, complaint, manual, invalid)
- source
- created_at

Unique:
- contact_type + normalized_value

---

## 23. analytics_daily

- id
- date
- department_id nullable
- operator_id nullable
- campaign_id nullable
- industry nullable
- subject_variant nullable
- template_variant nullable
- sent
- delivered
- bounced
- replied
- positive_replied
- wechat_added
- qualified
- high_intent
- created_at

---

## 24. audit_logs

- id
- department_id nullable
- operator_id nullable
- action
- entity_type
- entity_id nullable
- before JSONB nullable
- after JSONB nullable
- ip
- user_agent
- created_at

---

## 25. 索引重点

必须建立：
- influencers(platform, platform_user_id)
- influencers(huitun_id)
- influencer_contacts(normalized_value)
- campaign_leads(campaign_id, status)
- emails(status, scheduled_at)
- replies(received_at)
- tasks(assigned_operator_id, due_at, status)
- analytics_daily(date, campaign_id)

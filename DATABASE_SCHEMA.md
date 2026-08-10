# 数据库设计

> PostgreSQL

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

# API 规范

Base: `/api/v1`

统一响应：

```json
{
  "success": true,
  "data": {},
  "error": null,
  "request_id": "..."
}
```

分页：

```json
{
  "items": [],
  "page": 1,
  "page_size": 50,
  "total": 1000
}
```

---

## Auth

### POST /auth/login
输入：
- department_id
- password
- remember_me

### POST /auth/select-operator
- operator_id

### POST /auth/logout

### GET /auth/me

---

## Departments

### GET /departments
登录页只返回 active 部门基础信息。

### POST /admin/departments
### PATCH /admin/departments/{id}
### POST /admin/departments/{id}/reset-password

---

## Operators

### GET /operators
### POST /operators
### PATCH /operators/{id}

---

## Collection Jobs

### POST /collection-jobs
### GET /collection-jobs
### GET /collection-jobs/{id}
### POST /collection-jobs/{id}/generate-filter-suggestion

---

## Imports

### POST /imports
multipart upload

### GET /imports/{id}
### GET /imports/{id}/rows
### POST /imports/{id}/confirm-mapping

---

## Influencers

### GET /influencers
支持筛选。

### GET /influencers/{id}
### PATCH /influencers/{id}
### POST /influencers/batch/assign
### POST /influencers/batch/tag
### POST /influencers/batch/add-to-campaign
### POST /influencers/{id}/stop-contact

---

## AI

### POST /ai/influencers/{id}/analyze
### POST /ai/influencers/{id}/personalization
### POST /ai/campaigns/{id}/generate-preview
### POST /ai/replies/{id}/classify
### POST /ai/replies/{id}/suggest-response

AI API 都必须返回结构化字段与 prompt_version。

---

## Playbooks

### GET /playbooks
### POST /playbooks
### GET /playbooks/{id}/versions
### POST /playbooks/{id}/versions

正在运行的 Campaign 引用固定版本。

---

## Campaigns

### POST /campaigns
### GET /campaigns
### GET /campaigns/{id}
### PATCH /campaigns/{id}
### POST /campaigns/{id}/start
### POST /campaigns/{id}/pause
### POST /campaigns/{id}/resume
### POST /campaigns/{id}/cancel
### POST /campaigns/{id}/duplicate

---

## Campaign Leads

### GET /campaigns/{id}/leads
### POST /campaign-leads/{id}/approve
### POST /campaign-leads/{id}/skip
### POST /campaign-leads/{id}/regenerate

---

## Emails

### GET /emails
### GET /emails/{id}
### POST /emails/{id}/approve
### POST /emails/{id}/send
### POST /emails/{id}/cancel

---

## Inbox

### GET /replies
### GET /replies/{id}
### POST /replies/{id}/classify
### POST /replies/{id}/send-response
### POST /replies/{id}/mark-processed

---

## CRM

### GET /crm/board
### POST /crm/influencers/{id}/move
### POST /crm/influencers/{id}/followup
### POST /crm/influencers/{id}/note

---

## Analytics

### GET /analytics/overview
### GET /analytics/trend
### GET /analytics/by-industry
### GET /analytics/by-template
### GET /analytics/by-subject
### GET /analytics/by-operator
### GET /analytics/by-follower-range

---

## Mailboxes

### GET /mailboxes
### POST /mailboxes
### PATCH /mailboxes/{id}
### POST /mailboxes/{id}/test
### POST /mailboxes/{id}/sync

---

## Audit

### GET /audit-logs

仅管理员。

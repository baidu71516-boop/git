# Phase 3A WO-3A-1 Persistence Design

Status: approved for implementation

The frozen Phase 3A contract remains the source of truth. This record fixes
only the repository-level implementation boundary for WO-3A-1.

## Module boundary

- `backend_core.growth` owns Candidate Pool and Campaign persistence:
  `candidate_pools`, `targeting_policies`, `candidate_pool_runs`,
  `candidate_pool_members`, `campaigns`, and `campaign_members`.
- `backend_core.outreach` owns channel endpoint, work, history, and template
  persistence: `outreach_targets`, `outreach_tasks`, `outreach_events`,
  `message_templates`, and `message_template_versions`.

All eleven tables are registered through `backend_core.db.models`. Models use
the existing UUID/timestamp, PostgreSQL enum, JSONB, and `RESTRICT` FK
conventions. No service, repository, HTTP, worker, UI, provider, sequence,
Inbox, Email, or AI code is part of this order.

## Integrity and migration

The migration adds a composite Contact `(id, influencer_id)` uniqueness seam,
then uses composite FKs to prove account and Contact ownership. Campaign member
account ownership and target references are database-enforced. Target channel
shapes are represented with DB check constraints. The event table receives the
smallest PostgreSQL trigger-based update/delete guard compatible with the
current migration approach.

The new `0006` migration is additive and preserves all Phase 2 data. Targeted
model and PostgreSQL migration tests cover metadata registration, closed enums,
unique/composite FK/check constraints, event protection, migration paths, and
existing data preservation.

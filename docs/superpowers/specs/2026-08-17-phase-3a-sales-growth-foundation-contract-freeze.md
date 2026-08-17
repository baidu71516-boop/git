# Phase 3A - Sales Growth Foundation Contract Freeze

Status: DESIGN FROZEN (proposed)  
Date: 2026-08-17  
Repository baseline: `phase-3a-growth-foundation@9cea3f9d256a3b11f91f285f7cb80658c97bad5f`  
Working tree at audit start: clean  
Scope of this artifact: design only. No code, migration, commit, push, deploy, UI, provider, sequence, inbox, or AI implementation is authorized.

## Executive decision

Phase 3A should add a Department-owned growth domain around the existing company-level Influencer library. Candidate Pools are a hybrid: a saved, immutable deterministic policy version plus immutable materialized results for each run. Campaign membership is unique by `(campaign_id, influencer_id)` and pins one preferred PlatformAccount. An OutreachTarget then identifies the actual channel endpoint by reference to the canonical Contact or PlatformAccount. An OutreachTask is the unit of work; an append-only OutreachEvent is the business history.

The design is ready for Phase 3A implementation. Buyer Pool activation remains gated by a human-owned category taxonomy, not by missing architecture. Future account-value signals, Email, Sequence, Inbox, and AI remain explicit later gates.

## A. Existing capability reuse

### Verified Git reality

- `Influencer` is company-level and has no `department_id`. It owns `owner_operator_id`, global `crm_stage`, status, and soft-delete timestamp.
- `InfluencerPlatformAccount` supports multiple accounts per Influencer, but the current `Platform` enum contains only `xiaohongshu`. It already exposes `profile_url`, normalized identity, `source_tags`, and active state.
- `InfluencerContact` is the only Contact truth. It supports Email, WeChat, Phone, and Other, current-state, validation, duplicate warning, provenance, and optional PlatformAccount linkage.
- Current Metrics are JSONB per PlatformAccount/source; immutable MetricSnapshot history exists. `followers_count`, `notes_7d`, and `notes_60d` are real imported metrics.
- Freshness is derived from source/import observation timestamps. Current APIs expose status, age, refresh requirement, and source observation/import times.
- `CollectionJob` is Department-owned and already stores `industry`, `subdirection`, follower bounds, target count, versioned deterministic screening rules, and owner.
- Import has persisted files, rows, Preview revision/hash, Confirm revalidation, non-destructive Merge, contact protection, and idempotent task patterns. It must not be forked.
- Session permission comes from `DepartmentPermission.role`; selected Operator is attribution, not authorization. Viewer is read-only. Super Admin has existing cross-Department scope.
- Influencer reads are company-wide for all four roles. Viewer sees Contact values as the constant `***`; roles other than Viewer see the current value.
- Audit is persisted separately and records actor, department, entity, before/after, IP, and user agent.
- Current Influencer filtering really supports `contact_filter`, `notes_7d_filter`, `notes_60d_filter`, `profile_url`, tag, followers, owner, CRM stage, and freshness. Current list pagination is page/offset based.
- `campaigns`, `crm`, `email`, and `inbox` packages are placeholders only. No Campaign, Outreach, Email, Sequence, Inbox, or AI production capability exists.

### Reuse rule

Phase 3 must reuse canonical Influencer identity, PlatformAccount, Contact, Metrics, Freshness, CollectionJob context, Import provenance, Owner, global CRMStage, Session/RBAC/CSRF, Audit, hashing, strict Pydantic contracts, row-lock/CAS patterns, and service/repository layering. It must not create a second contact table, a second global CRM state, or an alternative identity matcher.

## B. Candidate Pool model

### Alternatives considered

1. Saved dynamic query only: cheap and always current, but cannot reproduce who matched, the evidence, or the state seen by a salesperson. Rejected.
2. One mutable snapshot only: reproducible, but cannot rerun the same intent or distinguish policy changes from data changes. Rejected.
3. Saved immutable policy versions plus immutable materialized runs: reproducible, rerunnable, explainable, and operationally fast. Selected.

### Frozen semantics

- Candidate Pool is Department-owned and has kind `POTENTIAL_SELLER` or `POTENTIAL_BUYER`.
- TargetingPolicy is immutable once used. Editing creates the next version; policy JSON has a closed `schema_version`, canonical hash, typed criteria, and deterministic missing-value behavior.
- Running a pool creates `CandidatePoolRun` with server-captured timezone-aware `as_of`, policy version/hash, source/input watermarks, status, counts, and failure details.
- `as_of` is the evaluation clock, not a promise of bitemporal database time travel. Reproduction comes from stored evidence and hashes. Clients may not request an arbitrary historical `as_of` against current-only source tables.
- Results are account candidates: every materialized row contains both `influencer_id` and `platform_account_id`. This preserves account-level evidence while Campaign later deduplicates by Influencer.
- Persist `MATCH` and `UNKNOWN` rows with closed `reason_codes`, redacted evidence JSON, and evidence hash. Do not persist every `NOT_MATCH` row at 100k scale; keep aggregate counts and rerun metadata.
- `CandidateSelection` is not a separate entity. A run member is the materialized selection/evaluation; a CampaignMember's `source_pool_run_id` is the provenance when selected.
- Candidate evidence may contain metric values, tags, timestamps, contact types/counts/status, and source IDs. It may never contain raw contact values.

## C. Seller targeting

`SELLER_V1` is a deterministic, versioned policy. It can combine:

- contact availability: `HAS_CONTACT`, `HAS_EMAIL`, `NO_CONTACT`, or no constraint, using current canonical Contacts;
- followers: configurable inclusive min/max;
- track/tag: configurable exact-any values over canonical account tags;
- activity: configurable numeric comparisons over `notes_7d` and/or `notes_60d`;
- freshness/platform/source constraints.

No threshold is hard-coded into code. Missing or invalid configured evidence yields `UNKNOWN`, not a permissive match. Representative reason codes are `CONTACT_AVAILABLE`, `EMAIL_AVAILABLE`, `NO_CURRENT_CONTACT`, `FOLLOWERS_IN_RANGE`, `FOLLOWERS_MISSING`, `TRACK_MATCH`, `TRACK_MISSING`, `LOW_ACTIVITY_MATCH`, and `ACTIVITY_MISSING`. There is no AI score.

## D. Buyer/category mismatch targeting

Buyer Pool must bind a same-Department `source_collection_job_id`. Its candidate universe is the canonical PlatformAccounts with committed Import provenance under that CollectionJob, deduplicated by account; it must not guess context from whichever import happens to be latest. The collection context is the exact `CollectionJob.industry` and `subdirection` snapshot. The creator side comes from a platform-specific classification adapter: future structured `creator_category`/`content_tags`, with current Xiaohongshu fallback limited to imported canonical `creator_tags/source_tags`. Each run snapshots both sides and their provenance. Free text, nickname, bio, and AI inference are prohibited.

The policy version contains a taxonomy version and explicit normalization/relationship rules. Comparison is:

- `NOT_MATCH / CATEGORY_ALIGNED` when normalized categories are equal, parent/child compatible, or have any explicitly compatible intersection;
- `MATCH / CATEGORY_MISMATCH` only when both sides are known and the taxonomy explicitly says they are incompatible;
- `UNKNOWN` for missing context/classification, unmapped labels, ambiguity, stale evidence when freshness is required, or absent comparison rules.

Closed unknown reason codes: `COLLECTION_CONTEXT_MISSING`, `CREATOR_CLASSIFICATION_MISSING`, `COLLECTION_CATEGORY_UNMAPPED`, `CREATOR_CATEGORY_UNMAPPED`, `AMBIGUOUS_CLASSIFICATION`, `CLASSIFICATION_STALE`, and `NO_COMPARISON_RULE`.

The business label is only "suspected category change / potential account-buyer candidate." No schema, reason code, API, or UI may represent `purchased_account=true` from mismatch evidence.

## E. Account Signals future seam

Define a provider-neutral input contract, not a Phase 3A table or connector:

`AccountSignalFact(platform_account_id, platform, signal_type, value_state, observed_value, observed_at, source, source_reference, schema_version)`.

`value_state` is `KNOWN | UNKNOWN | NOT_SUPPORTED`; future signal types may include purchase intent, sell intent, last published/inactivity, nickname/avatar/IP changes, and commercialization. Targeting engines consume a `CandidateFactBundle` assembled from existing canonical facts plus zero or more signal facts. Absence of a provider returns `NOT_SUPPORTED` and never blocks Seller/Buyer V1. Persistence and provider access stay out until the Access Gate passes.

## F. Campaign ownership

Campaign is Department-owned, matching the current auth model and the prior frozen ownership decision. A company-level Influencer can be referenced from any Department Campaign without changing Influencer ownership or CRMStage.

Campaign lifecycle is `DRAFT -> ACTIVE <-> PAUSED -> CLOSED`; `CLOSED` is terminal. Activation requires at least one active Member and one valid enabled-channel Target. Pause blocks new execution but preserves Tasks and history. Review configuration retains the tracked decisions `ALL | FIRST_N | SAMPLE | AUTO`, with default `FIRST_N=50`; `SAMPLE` is deterministic from Member ID, and `AUTO` means deterministic no-review eligibility, never AI approval. Phase 3A persists and validates this contract; Phase 3B is the first UI consumer.

- Create: selected Operator, non-Viewer, Campaign department fixed to the authenticated Department.
- Read: all roles in the same Department; Super Admin cross-Department; Viewer remains masked.
- Write: selected Operator and non-Viewer in the same Department; Super Admin keeps the existing explicit cross-Department override, fully audited.
- Operator ownership is assignment/audit metadata, not row-level authorization. The current session model cannot honestly offer per-Operator ACLs.
- Viewer can read Campaign, Member, Target metadata, Task status, and Event history, but cannot mutate or retrieve raw target/contact values.

## G. CampaignMember

CampaignMember references required `influencer_id` plus required `preferred_platform_account_id`. A composite FK must prove that the account belongs to the Influencer. The account is the preferred Campaign context, while each OutreachTarget can reference the channel-specific account/contact.

Uniqueness is permanently `(campaign_id, influencer_id)`. The same Influencer cannot be added twice to one Campaign through another account. Removal sets `removed_at`; re-add restores the same member instead of inserting a second history. The same Influencer may join multiple Campaigns.

## H. OutreachTarget

OutreachTarget is channel-aware and unique by `(campaign_id, influencer_id, channel)`. It references, rather than copies, the canonical endpoint:

- Email: required current Email `contact_id`;
- WeChat: required current WeChat `contact_id`;
- Xiaohongshu/Douyin private message: required matching-platform `platform_account_id`;
- Manual: exactly one Contact or PlatformAccount reference.

Service validation enforces Contact type, ownership, current state, validation rules, account platform, and Department scope. Updating a target reference uses optimistic concurrency and Audit.

Reference versus snapshot boundary: the target remains a live reference until execution. The `OUTREACH_SENT` event freezes the identifiers, channel, masked display, validation/source state, execution time, and a non-reversible endpoint fingerprint. Phase 3A does not duplicate plaintext destinations. Phase 3C may add an encrypted exact Email recipient snapshot only after the production key gate; changing canonical Contact later never rewrites historical Events.

## I. Channel model

| Channel | Architecture supported | Current production | First enabled phase | Execution |
|---|---:|---:|---:|---|
| `EMAIL` | yes | disabled | 3C | automated only after provider/suppression gates |
| `XIAOHONGSHU_PRIVATE_MESSAGE` | yes | disabled | 3B | manual |
| `DOUYIN_PRIVATE_MESSAGE` | yes | disabled | future platform enablement | manual |
| `WECHAT` | yes | disabled | 3B | manual |
| `MANUAL` | yes | disabled | 3B | manual |

`OutreachChannel` may contain all five values, but an enablement registry gates creation/execution. Douyin cannot be enabled while the canonical Platform enum/account data lacks Douyin. No UI or API may claim automated private messaging or automated WeChat.

## J. Contact semantics

Reuse existing `ContactFilter` definitions exactly for discovery:

- `HAS_CONTACT`: at least one current Contact;
- `HAS_EMAIL`: at least one current Email Contact;
- `NO_CONTACT`: no current Contact.

These are derived queries, never cached booleans. Availability is not the same as execution readiness: Email requires current + valid Email; WeChat `UNVERIFIED` enters `REVIEW_REQUIRED`; invalid endpoints cannot become `READY`. Candidate/Campaign membership may include no-contact Influencers for research or manual work, but endpoint-specific Task creation must satisfy its channel contract. Contact masking is applied after every join, including Candidate evidence, Campaign reads, Today API, exports, and Event reads.

## K. OutreachTask state machine

Minimal V1 states are `REVIEW_REQUIRED`, `READY`, `SENT`, `STOPPED`, and `FAILED`. `APPROVED` is redundant: approval is the transition to `READY`. `WAITING_REPLY` and `REPLIED` belong to Inbox and are not V1 Task states.

Legal transitions:

| From | To | Actor / condition |
|---|---|---|
| create | `REVIEW_REQUIRED` | deterministic review/contact rule requires review |
| create | `READY` | deterministic rule permits work |
| `REVIEW_REQUIRED` | `READY` | selected non-Viewer approves |
| `REVIEW_REQUIRED` | `STOPPED` | selected non-Viewer rejects/stops |
| `READY` | `SENT` | selected non-Viewer marks manual execution; future system sender uses system actor |
| `READY` | `FAILED` | an actual attempt failed with reason |
| `READY` | `STOPPED` | selected non-Viewer stops |
| `FAILED` | `READY` | selected non-Viewer explicitly retries |

`SENT` and `STOPPED` are terminal in V1. Every transition requires `expected_version`; the service locks/CAS-updates the row and appends one Event in the same transaction. Invalid transitions return 409.

## L. OutreachEvent

OutreachEvent is append-only business history, distinct from Audit. Minimal types are `TASK_CREATED`, `REVIEW_APPROVED`, `OUTREACH_SENT`, `OUTREACH_FAILED`, `OUTREACH_STOPPED`, and `OUTREACH_RETRIED`. It stores Task/Campaign/Influencer/channel, actor type/operator, occurred time, from/to state, reason code, idempotency key/request hash, and optional redacted target/message snapshots.

Audit answers who changed the system and captures request/security context. OutreachEvent answers what happened in the outreach lifecycle. A transition writes both when it is a critical mutation. Event update/delete is prohibited at repository and database privilege/trigger level.

## M. Duplicate protection

- Same Influencer in one Campaign: DB unique CampaignMember; duplicate bulk add is idempotent.
- Same Campaign + Influencer + channel: DB unique OutreachTarget.
- Same target + logical step: DB unique `(outreach_target_id, step_key)`. V1 uses `initial`; failed retries reuse the Task. Future Sequence uses immutable step keys.
- Concurrent review: `expected_version` CAS; one wins, stale reviewer gets current version with 409.
- Repeated mark-sent: unique Event idempotency key plus request hash. Same key/payload returns the original result; same key/different payload is 409; a different key after `SENT` is an invalid transition and cannot create another sent Event.
- Cross-Campaign contact: allowed. Before Task readiness, query OutreachEvent history by company Influencer + channel and return a warning with last-contact facts.
- Historical policy is Campaign-configurable: `ALLOW_WITH_WARNING` default, `REQUIRE_CONFIRMATION`, or `BLOCK_WITHIN_WINDOW`. Window is configuration, not a global hard-coded ban. Suppression/unsubscribe will be a mandatory Email gate in 3C and can override Campaign policy.

## N. Priority

V1 priority is `NORMAL` by default and `HIGH` by explicit manual override. Persist `priority_source = DEFAULT | MANUAL | POLICY` and optional reason codes. Phase 3A enables only DEFAULT/MANUAL. A later deterministic policy may set priority only with reason codes and its policy version. AI priority is prohibited.

## O. Scheduling

Phase 3A stores required `due_at` on every Task. Today/overdue work is derived from `due_at` in `Asia/Shanghai`, never from `created_at`. It does not add `scheduled_at`; that field means automated dispatch and belongs to Email/Sequence when those contracts exist. Future D0/D4/D9 steps create distinct Tasks with explicit `due_at` and immutable `step_key`.

## P. Template seam

`MessageTemplate` is mutable Department-owned metadata (name, channel, active/archive state). `MessageTemplateVersion` is immutable and unique by `(template_id, version)`, with subject/body, variable schema, checksum, and creator. Approval pins a version to a Task. Execution freezes the rendered content in the sent Event; later template edits create a new version and never alter history.

Phase 3A may create only these persistence seams and nullable Task/Event references. Template CRUD/rendering belongs to 3B for manual channels and 3C for Email. No provider, mailbox, SMTP, Sequence, or sending logic is part of the seam.

## Q. Today Outreach API

Frozen read contract:

`GET /api/v1/outreach-tasks/today`

Query fields are closed and non-repeatable: `work_kind=FIRST_TOUCH|FOLLOW_UP|ALL`, `channel`, `campaign_id`, `owner_operator_id`, `track`, `followers_min`, `followers_max`, `contact_filter`, `priority`, `cursor`, and `limit` (default 50, max 100).

Eligibility is Department-scoped, non-removed Member, Task `READY`, and `due_at < next_business_day`; overdue work is included. `FOLLOW_UP` can return empty until 3D creates such Tasks. The response includes `business_date`, `timezone=Asia/Shanghai`, `as_of`, `items`, and `next_cursor`.

Ordering is frozen as `priority_rank DESC, due_at ASC, id ASC`. Pagination is opaque keyset pagination over the full sort tuple. Cursor includes a query hash and is rejected if reused with different filters. A mutation requires starting a fresh pagination session; the API does not claim cross-request MVCC snapshot isolation.

Each item may expose Campaign/Member/account summary, task kind/status/version, channel, due time, priority, owner, follower/tag summaries, contact availability, history warning, and masked target display. It must not expose "new reply", "unread", or Inbox-derived "high intent" before 3E. Existing global CRMStage may be shown only under its truthful CRM label, never as an Inbox signal.

## R. RBAC

| Operation | Viewer | Operator / Manager | Super Admin |
|---|---|---|---|
| same-Department read | yes, Contacts masked | yes | yes |
| same-Department mutation | no | yes, selected Operator + CSRF | yes, selected Operator + CSRF |
| cross-Department read | no | no | yes |
| cross-Department mutation | no | no | explicit override, selected Operator + CSRF + Audit |

For non-Super-Admin cross-scope IDs, growth APIs should return 404 to avoid existence disclosure. Selected Operator role must not replace DepartmentPermission role.

## S. Concurrency and idempotency

- Mutable aggregates (`Campaign`, CampaignMember, OutreachTarget, OutreachTask) have integer `version >= 1`; mutation contracts require strict `expected_version`.
- Services use row locks/CAS and commit state update, OutreachEvent, and Audit atomically.
- Create/bulk/run APIs require `Idempotency-Key`. Persist `(department_id, operation_scope, key, request_hash, result_entity_id)` or an equivalent unique domain key; never rely on Redis/Audit as the fact source.
- Pool runs are unique by Pool + idempotency key; Campaign bulk add is set-based and replay-safe; Task transition events are unique by Task + idempotency key.
- Database uniqueness is the final guard. Integrity conflicts map to deterministic existing-result or 409 behavior; they are not retried into duplicates.

## T. Performance and index design

Candidate materialization over 100k Influencers runs asynchronously, uses set-based SQL, streams/batches inserts, and records durable run status. Pool/member and Campaign/member listing use keyset pagination. A run-to-Campaign bulk add uses `INSERT ... SELECT` from persisted run members; arbitrary ID uploads are chunked and bounded. APIs prefetch summaries in a fixed number of queries and prohibit per-row Contact/Metrics/Event loads.

Required/representative indexes:

- partial Contact `(influencer_id, type)` where `is_current = true`, plus `(influencer_id)` current;
- guarded PostgreSQL expression indexes for Huitun `followers_count`, `notes_7d`, and `notes_60d` JSONB integer values;
- GIN on `influencer_platform_accounts.source_tags`;
- Pool `(department_id, status, created_at, id)`, Run `(pool_id, created_at, id)`, RunMember `(run_id, result, id)` and unique `(run_id, platform_account_id)`;
- Campaign `(department_id, status, updated_at, id)`, Member unique `(campaign_id, influencer_id)` and `(campaign_id, removed_at, id)`;
- Target unique `(campaign_id, influencer_id, channel)`;
- Task unique `(outreach_target_id, step_key)`, Today queue `(department_id, status, priority, due_at, id)`, owner queue `(department_id, assigned_operator_id, status, due_at, id)`, and Campaign queue;
- Event `(department_id, influencer_id, channel, occurred_at DESC, id DESC)` and `(task_id, occurred_at, id)`.

Load gates must cover 100k Influencers, 10k Campaign members, a 100k candidate run, a 10k set-based bulk add, and 500 daily Tasks without N+1. Query plans must be captured on PostgreSQL, not inferred from SQLite.

## U. UI capability matrix

| Surface | Phase 3A | Phase 3B | Future only |
|---|---|---|---|
| Candidate Pool | backend model/run/read contracts | configure, run, inspect MATCH/UNKNOWN, select | external account signals after access gate |
| Campaign | backend CRUD/member/target contracts | Campaign workspace and bulk add | variants/A-B only when separately frozen |
| Review | state/API contract | concurrent-safe review UI | AI suggestions in 3F, never auto-fact |
| Today Outreach | real read API | first-touch/manual workbench | reply/unread indicators only in 3E |
| Manual PM/WeChat | reference/event semantics | manual execute + mark sent | no automated platform claim |
| Email | disabled seam only | disabled | 3C provider/mailbox/suppression |
| Sequence | `due_at` seam only | no engine | 3D |
| Inbox | none | none | 3E |
| AI | none | none | 3F |

Phase 3A has no new UI acceptance boundary. Phase 3B UI may render only fields returned by frozen APIs.

## V. Proposed schema/migration design

The Phase 3A migration plan, when separately authorized, should create:

1. `candidate_pools`: Department, owner, kind, name, optional source CollectionJob, status, current policy pointer, version, timestamps.
2. `targeting_policies`: Pool, immutable version/schema/definition/hash, creator, timestamps; unique Pool + version.
3. `candidate_pool_runs`: Pool/policy, server `as_of`, input watermark, status/counts/error, idempotency/request hash, timestamps.
4. `candidate_pool_members`: Run, Influencer, PlatformAccount, `MATCH|UNKNOWN`, reason codes/evidence/hash, timestamps; unique Run + PlatformAccount.
5. `campaigns`: Department, owner/creator, name, status, review settings, duplicate policy/window, version, timestamps.
6. `campaign_members`: Department/Campaign, Influencer, preferred PlatformAccount, source Run, added-by, removed-at, version, timestamps; unique Campaign + Influencer.
7. `outreach_targets`: Department/Campaign/Member/Influencer, channel, Contact or PlatformAccount reference, version, timestamps; unique Campaign + Influencer + channel.
8. `outreach_tasks`: Department/Target, kind, step key, state, priority/source/reasons, assignee, required due-at, template version reference, version, create idempotency, timestamps; unique Target + step key.
9. `outreach_events`: denormalized Department/Campaign/Influencer/channel/Task, event/actor/status transition, occurred-at, idempotency/request hash, redacted snapshots/metadata, timestamps; append-only.
10. `message_templates` and `message_template_versions`: minimal immutable-version seam; no sending logic.

Also add the Contact composite uniqueness needed for `(contact_id, influencer_id)` referential integrity and the performance indexes in section T. Use existing UUID/timestamp conventions, PostgreSQL enums for core states, JSONB only for versioned policy/evidence/snapshot documents, `RESTRICT` on business-history FKs, and soft/archive states rather than destructive delete. No migration is created by this freeze.

## W. Phase 3A-3F scope

### Phase 3A - Growth Foundation

IN: proposed persistence, deterministic policy engine, pool runs/materialization, Campaign/Member/Target/Task/Event services, duplicate rules, Today API, RBAC/Audit/CSRF, idempotency/concurrency, template seam, performance tests.  
OUT: UI, providers, automated sends, Sequence, replies/Inbox, AI.  
Dependency: deployed Phase 2 schema and current Git baseline.  
Acceptance: migration round-trip; closed APIs; state/uniqueness/concurrency tests; Viewer masking; Department scope; set-based performance gates; no provider/UI claims.

### Phase 3B - Outreach Workbench

IN: Candidate/Campaign/Review/Today UI, manual XHS PM/WeChat/Manual execution, manual templates, history warnings.  
OUT: automated PM/WeChat, Email send, Sequence, Inbox, AI.  
Dependency: 3A.  
Acceptance: a salesperson can materialize candidates, add members, review, execute manually, mark once, and see truthful history.

### Phase 3C - Email

IN: mailbox/provider, encrypted credentials/snapshots, suppression/unsubscribe, valid-recipient checks, send idempotency, truthful sent/delivered-null semantics, Email templates.  
OUT: multi-step Sequence and Inbox classification unless separately included.  
Dependency: 3A/3B plus mailbox credentials, production key, quota and suppression gates.  
Acceptance: one Email step is sent at most once with immutable recipient/template evidence and all pre-send checks.

### Phase 3D - Sequence

IN: versioned steps, D0/D4/D9 or configurable schedule, explicit future Tasks/due-at/scheduled-at, stop rules, scheduler recovery.  
OUT: Inbox ingestion and AI.  
Dependency: 3C for automated Email; 3B for manual channels.  
Acceptance: deterministic step creation/execution and no resend of an already sent logical step.

### Phase 3E - Inbox

IN: inbound threads/messages, reply correlation, unread/new-reply facts, reply-driven stops, manual intent labels.  
OUT: AI classification unless 3F is present.  
Dependency: 3C message identities and 3A events.  
Acceptance: real replies, not inferred CRM/task states, power Inbox and Today indicators.

### Phase 3F - AI

IN: explainable suggestions for classification, drafting, and prioritization with model/prompt/input/result provenance and human/rule validation.  
OUT: AI as canonical fact or autonomous unrestricted sending.  
Dependency: stable 3A-3E facts and AI provider approval.  
Acceptance: no AI output directly mutates trusted facts or bypasses channel/suppression/review rules.

## X. Implementation Work Orders

These are future orders only; none is authorized by this document.

1. **WO-3A-1 - Persistence foundation (Terra).** Create enums/models/migration for all 3A tables and constraints, register metadata, and add migration/model tests. Dependency: Phase 2 head. Boundary: no service/API/UI.
2. **WO-3A-2 - Deterministic targeting (Terra).** Implement typed Seller/Buyer policies, category comparison adapters, run worker, evidence redaction, and materialization repository/API. Dependency: WO-1. Boundary: no Campaign/AI/external signals.
3. **WO-3A-3 - Campaign and Outreach domain (Terra).** Implement Campaign/Member/Target/Task/Event services, state machine, duplicate policy, CAS/idempotency, Audit and RBAC. Dependency: WO-1; may run parallel with WO-2. Boundary: no provider/UI.
4. **WO-3A-4 - HTTP and Today query (Terra).** Implement closed APIs, cursor contract, bulk add from run, fixed-query DTO assembly, OpenAPI tests, and error mapping. Dependencies: WO-2/3.
5. **WO-3A-5 - Mechanical release gates (Luna).** Run migration round-trip, state/property/concurrency/security tests, Viewer leak scans, PostgreSQL EXPLAIN/load gates, lint/type/test/build, and produce evidence. Dependencies: WO-1 through WO-4. Boundary: fix only mechanical failures; escalate contract changes.
6. **WO-3A-6 - Final contract audit (Sol).** Compare implementation/API/schema/query plans against this freeze, inspect gaps and security regressions, and issue READY/BLOCKED. Dependency: WO-5. Boundary: audit only unless a separate fix order is authorized.

## Y. Model routing

- Sol: this contract freeze, irreducible cross-domain/security decisions, and final audit only.
- Terra: all principal backend/migration/API implementation work.
- Luna: exhaustive tests, fixtures, migration/query/performance gates, and mechanical reconciliation.
- Spark: no Phase 3A assignment because Phase 3A has no UI. Reserve Spark for tightly specified 3B components after backend contracts pass.

## Z. Known product decisions requiring human input

These are activation inputs, not Phase 3A architecture blockers:

1. Seller policy values: actual follower/activity/tag/contact thresholds per sales motion. Recommendation: require explicit policy configuration; ship no hidden threshold.
2. Buyer taxonomy: authoritative category IDs, aliases, hierarchy, compatible/incompatible relations, and which CollectionJobs are eligible. Recommendation: Buyer Pool stays disabled until one reviewed taxonomy version exists.
3. Cross-Campaign history policy: default time window for `REQUIRE_CONFIRMATION`/`BLOCK_WITHIN_WINDOW`. Recommendation: ship `ALLOW_WITH_WARNING` with full-history evidence and no blocking window by default.
4. WeChat readiness: whether `UNVERIFIED` values may be manually attempted. Recommendation: allow target creation but require review; never auto-ready an invalid value.
5. Exact historical destination retention for Email. Recommendation: decide retention/access policy before 3C; only encrypted snapshots after the production master-key gate.

## AA. Final

**READY** for Phase 3A implementation planning and work-order execution after human sign-off of this freeze.

Not ready, by design: Buyer Pool production activation without taxonomy; any future account-signal claim without Access Gate; Phase 3B UI; Phase 3C Email; Phase 3D Sequence; Phase 3E Inbox; Phase 3F AI; automated private messages or WeChat.

## Verification evidence

- Git baseline, branch, recent commits, models, migrations `0001` through `0005`, APIs, services, repositories, security rules, Phase 2 freeze, and tracked product decisions were inspected directly.
- Focused read/RBAC/filter tests: 99 passed.
- Focused API auth/Influencer contract tests: 6 passed.
- No repository source file, migration, or application code was modified by this design task.

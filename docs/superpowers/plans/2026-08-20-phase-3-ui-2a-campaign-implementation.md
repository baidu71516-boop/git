# Phase 3 UI-2A：拓客活动基础管理 Implementation Plan

> Boundary: this plan implements only Campaign List, Create Campaign, Campaign Detail, and Edit Campaign. It must not add Campaign Member, activity-member counts, influencer operations, Candidate Pool import, lifecycle controls, Review, Send, Email, Sequence, Inbox, Reply, AI, or KPI UI.

## 1. Establish typed Campaign contracts and presenters

**Files**

- Create `apps/web/src/features/campaigns/types.ts`
- Create `apps/web/src/features/campaigns/formatters.ts`
- Create `apps/web/tests/campaign-formatters.test.ts`

**Implementation**

1. Define the exact UI DTOs for `CampaignResult`, owner projection, cursor page, create input and full-PUT input. Preserve nullable `review_count`, `duplicate_window_days`, and the owner projection (`id`, `name`, `status`) in the type layer.
2. Add a closed status presenter: `DRAFT` → 草稿, `ACTIVE` → 进行中, `PAUSED` → 已暂停, `CLOSED` → 已关闭, all unrecognized values → 未知状态. Use the existing status-badge tone vocabulary without introducing lifecycle actions.
3. Reuse the project Asia/Shanghai date formatter for `created_at` and `updated_at`; return the existing safe placeholder for absent or invalid values.
4. Add an owner presenter that renders the backend name directly and exposes a low-priority disabled marker only when `owner.status === "disabled"`. Never fall back to an ID.

**Tests**

- Verify all fixed and unknown status mappings.
- Verify Asia/Shanghai time rendering and the disabled-owner presentation.
- Assert UUID-like owner IDs never become display fallback text.

## 2. Implement contract-precise API functions and React Query hooks

**Files**

- Create `apps/web/src/features/campaigns/api.ts`
- Create `apps/web/src/features/campaigns/queries.ts`
- Create `apps/web/tests/campaign-api.test.ts`

**Implementation**

1. Add API functions for the formal endpoints: `GET /api/v1/campaigns`, `GET /api/v1/campaigns/{id}`, `POST /api/v1/campaigns`, `PUT /api/v1/campaigns/{id}`, and `GET /api/v1/operators`. Because `apiRequest` adds `/api/v1`, feature paths remain `/campaigns` and `/operators`, while tests assert the fully resolved URLs.
2. Build the list path with only `limit=50` and optional opaque `cursor`; preserve the token without parsing or normalizing it. Validate absent envelope data through the existing `ApiClientError` pattern.
3. Thread the existing canonical Department context, if any, through every Campaign and owner-options request. In normal Session Department scope, omit `X-Department-ID`; only use one already-resolved canonical cross-department header for all related requests. Do not add a selector or infer a department.
4. Use `useInfiniteQuery` for list append semantics, `useQuery` for detail/options, and shared retry-read logic that does not retry deterministic 4xx responses. Set `retry: false` on every create/edit mutation.
5. Implement create-attempt state: emit an Idempotency-Key on first submit, retain key plus semantic payload only for a user-triggered retry after transport ambiguity/5xx, invalidate it when payload changes or Modal lifecycle ends, and surface `IDEMPOTENCY_KEY_REUSED` as a distinct error rather than a retry affordance.
6. Keep full-PUT construction in one helper accepting a fresh detail baseline and the two editable values. It must carry all hidden fields unchanged, retain nulls, and use `baseline.version` as `expected_version`.

**Tests**

- Assert resolved GET paths, `limit=50`, opaque cursor forwarding, formal request methods, CSRF propagation from the shared client, and no hidden create defaults.
- Assert POST carries exactly name and optional owner, PUT carries the full retained baseline and `expected_version`, and null fields survive.
- Assert idempotency header reuse only for a user-initiated same-payload retry; a changed payload and new Modal attempt use a new key; no mutation automatic retry occurs.
- Assert a canonical Department header is consistently applied only when supplied by the existing app context.

## 3. Build the Campaign list and create flow

**Files**

- Create `apps/web/src/features/campaigns/campaign-list.tsx`
- Create `apps/web/src/features/campaigns/components/campaign-table.tsx`
- Create `apps/web/src/features/campaigns/components/create-campaign-modal.tsx`
- Create `apps/web/src/features/campaigns/campaign-list-view.tsx`
- Create `apps/web/tests/campaign-list.test.tsx`

**Implementation**

1. Render the fixed PageHeader: 拓客活动, 管理销售拓展活动和负责人。Render the primary 新建活动 action only for non-viewers; if the app indicates a missing selected Operator, retain list reading and use the existing mutation-context disabled/prompt behavior only on this action.
2. Render one fixed Table with 活动名称、状态、负责人、更新时间、操作. Its only action is 查看详情 to `/campaigns/{id}`. Use backend owner projection and update timestamp as received. Add no filters, sorting, counts, cards, totals, page controls, or member content.
3. Keep Header visible while the table region renders Skeleton. Implement the exact frozen empty/error states and an explicit 加载更多 button that appends pages; after the final page show only a low-priority 已经到底了 indicator.
4. Implement the 560px create Modal. Validate required/max-200 name, load active canonical operators, permit a blank owner, and allow owner-option reload failure without fabricated values. Surface safe 422, permission, missing-operator, ambiguity/5xx, and idempotency-reuse feedback.
5. On success show 拓客活动已创建 and navigate only to `/campaigns/{id}`. Block duplicate clicks while pending.

**Tests**

- Cover exact columns, owner projection/disabled marker, fixed backend order, cursor append, no total/page controls, empty/loading/error, and Viewer visibility.
- Cover create body and optional owner, options failure plus blank-owner create, missing selected Operator write behavior without read blockage, duplicate click prevention, safe errors, idempotency behavior, and success routing.

## 4. Build Campaign detail and safe full-PUT edit flow

**Files**

- Create `apps/web/src/features/campaigns/campaign-detail.tsx`
- Create `apps/web/src/features/campaigns/components/edit-campaign-modal.tsx`
- Create `apps/web/src/features/campaigns/campaign-detail-view.tsx`
- Create `apps/web/tests/campaign-detail.test.tsx`

**Implementation**

1. Render Header Skeleton plus basic-information Skeleton during detail load. The loaded page shows only activity name, status, owner, created time, and updated time; owner preserves disabled historical display. Do not render IDs, version, hidden policy fields, members, or lifecycle controls.
2. Map 404 to the concealment message and return action, map real 403 to its distinct message, and keep a safe generic retry state for other read errors.
3. Show 编辑活动 only for non-viewers and non-CLOSED details. On action, refetch the detail and only open the Modal with the returned full snapshot as the write baseline.
4. In the 560px edit Modal, expose only name and owner. Preserve a disabled current owner as the retained current selection; active options may replace it. If options fail, present the known projected owner and allow name-only save.
5. Submit the full PUT helper from step 2. For `VERSION_CONFLICT`, close Modal, discard baseline, refetch, and show server data with the exact reload message. For `CAMPAIGN_CLOSED`, close and refetch. Do not resubmit, merge values, overwrite, or change the selected Operator.

**Tests**

- Cover exact detail GET, all required fields, disabled owner, 404/403/generic states, Viewer and CLOSED edit hiding.
- Assert opening Edit first refreshes detail, then PUT uses that returned baseline; hidden configuration and null preservation must be exact.
- Cover disabled retained owner, active-owner switch, owner-options failure name-only save, VERSION_CONFLICT reload flow, CAMPAIGN_CLOSED flow, and disabled mutation retry.

## 5. Wire routes, navigation, auth shell, and styles

**Files**

- Create `apps/web/src/app/campaigns/page.tsx`
- Create `apps/web/src/app/campaigns/[id]/page.tsx`
- Modify `apps/web/src/components/auth-shell.tsx`
- Modify `apps/web/src/components/navigation/sidebar-nav.tsx`
- Modify `apps/web/src/app/globals.css`
- Create `apps/web/tests/campaign-routing.test.tsx`

**Implementation**

1. Mount list and detail via AuthShell’s new `campaigns` workspace and add “拓客活动” between “今日触达” and “达人库”. Leave all other navigation unchanged.
2. Ensure `workspaceRequiresOperator` does not make Campaign reads require selected Operator. Pass auth role and existing canonical context into Campaign components so mutations follow current UX, while viewers still receive normal list/detail rendering.
3. Add restrained existing-style layout rules only for table, detail surface, owner auxiliary label, footer, and Modal alignment. Preserve white surfaces, light borders, Chinese-first high-density styling; do not perform visual-polish or App Shell redesign.

**Tests**

- Assert formal routes mount the intended workspace and sidebar order/hrefs are exact.
- Assert Campaign reads are available to viewers and non-viewers without a selected Operator; only mutation controls are withheld/disabled according to current mutation context.

## 6. Validate and hand off

**Files**

- Modify only files introduced or identified in steps 1–5, plus focused test fixtures if needed.

**Validation**

1. Run the focused Campaign tests: `pnpm --filter @influencer-outreach/web test -- campaign-api campaign-formatters campaign-list campaign-detail campaign-routing` (adapt the package command only if the workspace scripts require it).
2. Run `pnpm --filter @influencer-outreach/web typecheck`, `pnpm --filter @influencer-outreach/web lint`, and the full Web test suite if focused tests pass.
3. Run `git diff --check`, inspect `git diff --cached --name-only` before any commit, and confirm no UI-2B/lifecycle terms or files were added.
4. Report the exact test results, commit(s), and remaining limitation: UI-2B remains blocked by member display projection.

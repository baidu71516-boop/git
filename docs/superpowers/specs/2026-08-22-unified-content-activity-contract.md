# Unified Content Activity — Douyin + Xiaohongshu Contract Freeze

Status: **DESIGN FROZEN — DOCUMENTATION ONLY**

Date: 2026-08-22

Baseline: `codex/douyin-content-activity-provider-freeze@5fb84212ef1f07803fd18a1d5a357bf46c3821c5`

Worktree: `/Users/mac/Downloads/influencer_outreach_douyin_content_activity`

Activity semantics: `CURRENT_PUBLIC_VISIBLE`

Historical design lineage:
`b09f423e6b59bc840d313fa950d08be78eddde89:docs/superpowers/specs/2026-08-22-xhs-content-activity-grey-dolphin-ingestion-design.md`

Scope: one platform-neutral Content Activity business contract, provider boundary,
identity and activity lineage, observation and coverage states, pagination safety,
data minimization, provider-proof verdicts, and the future implementation entry
point.

This artifact does not authorize or implement product code, a migration, an API,
a worker, a Web change, a provider call, a deployment, Phase 3B work, or a Sales
Growth release change. It does not authorize storing TikHub credentials or raw
provider payloads. UI visual design is outside this contract.

## 1. Executive freeze

Content Activity answers exactly this question:

> What is the latest publication that is currently publicly visible on this
> creator's public profile, as of a recorded observation?

It does not answer which publication was historically latest. A later trusted
observation may move `last_publication_at` backward or clear it to no public
content when a newer publication is deleted, hidden, made private, prohibited, or
otherwise stops being publicly visible.

For one validated observation run:

```text
last_publication_at = MAX(valid current-public-visible published_at)
```

The maximum is over the normalized eligible publications in that one observation,
not over observation history. Product projections must never apply a monotonic
`GREATEST(previous_last_publication_at, incoming_last_publication_at)` rule.

Pinned or sticky content remains eligible when it is currently public. Pinning is
ordering metadata only: it forbids positional inference but does not exclude a
publication. `list[0]`, the first non-sticky item, provider array order, update
time, observation time, import time, file time, and historical aggregate counts
are all forbidden substitutes for the maximum.

The supplied functional evidence supports these scoped verdicts:

- Douyin TikHub functional provider proof: **PASS**.
- Xiaohongshu TikHub functional provider proof for
  `CURRENT_PUBLIC_VISIBLE`: **PASS**.

These are technical data proofs for implementation planning. They are not
commercial, licensing, terms-of-use, support, quota, or SLA approvals.

## 2. Evidence authority and supersession

This freeze distinguishes four evidence classes:

1. Repository facts were inspected at the exact baseline above.
2. Grey Dolphin lineage was inspected at exact commit `b09f423...`. That document
   memorialized accepted supplied evidence; it did not independently repeat
   provider calls or commit the raw paired CSVs.
3. The internal Douyin identity-equivalence proof and TikHub account/publication
   samples below are accepted supplied evidence; this documentation task did not
   repeat live identity or activity-provider calls.
4. The normalized contract and safety rules are decisions frozen by this document.

The earlier Grey Dolphin document is a sibling-lineage commit, not an ancestor of
this baseline. Its evidence is preserved by the exact Git reference above and by
the findings restated here. It is not copied into the current tree and is not
invalidated.

Preserved Grey Dolphin findings:

- note-level `发布时间` exists with second precision;
- official-note URLs expose stable-looking note IDs;
- both image/text and video notes occur;
- selectable 7/30/90/180-day, one-year, two-year, and custom ranges exist, with
  approximately two years available in the tested workflow;
- the paired export had 53 rows unfiltered and 47 filtered, with exactly six rows
  removed by the hidden/deleted filter;
- a random CSV cannot prove that filter's provenance;
- the supplied note-only CSV could not bind itself to a creator or canonical
  PlatformAccount;
- nickname and filename are not authoritative creator identity; and
- manual one-creator-at-a-time export is not an acceptable production workflow.

Superseded conclusion: the prior
`XHS_CONTENT_ACTIVITY_INGESTION_DESIGN_NEEDS_PROVIDER_CONFIRMATION` verdict was a
gate for a scalable Grey Dolphin CSV/batch/fallback adapter. It predates the
accepted TikHub Xiaohongshu proof and does not block TikHub's functional proof.
Grey Dolphin historical records are never present-day visibility truth by
themselves. Complete visibility-filter provenance can establish
`CURRENT_PUBLIC_VISIBLE` only as of that record's `observed_at`; a product
freshness policy must decide whether it is still usable as current.

## 3. Accepted functional provider evidence

### 3.1 Douyin

The preferred identity and activity path is:

```text
existing internal Douyin identity capability
  -> sec_uid
  -> internal InfluencerPlatformAccount
  -> TikHub Douyin App V3
  -> current public works
  -> MAX(valid create_time)
```

Proven activity endpoint:
`/api/v1/douyin/app/v3/fetch_user_post_videos`, using the supplied proof shape
`sec_user_id`, `max_cursor=0`, `count=20`, `sort_type=0`, and `channel=normal`.
Those parameters are adapter details, not canonical business fields.

The endpoint name and supplied samples establish a scoped works/video proof, not
the complete Douyin publication-type universe. Traversal completeness and
content-type completeness are independent. A production adapter may claim
`FULL_CURRENT_PUBLIC_SET` only after its versioned platform policy proves that the
endpoint covers every current-public Douyin publication type included by Content
Activity. A potentially newer type outside that proven universe makes coverage
incomplete or untrusted; endpoint exhaustion alone is insufficient.

Accepted samples:

| Account | Evidence | Frozen conclusion |
|---|---|---|
| 张国伟_国家伟大 | Older pinned works preceded newer work; TikHub maximum `2026-08-22 14:21:45 Asia/Shanghai`; Grey Dolphin `2026-08-22 14:21` | Latest-publication match; proves `list[0]` unsafe |
| 南宁科比 | TikHub maximum `2026-06-12 21:52:29 Asia/Shanghai`; Grey Dolphin `2026-06-12 21:52` | Roughly 70-day inactive, minute-aligned latest-publication match |
| 南宁科比, consecutive works | TikHub `2026-06-12 20:34:48` -> Grey Dolphin `06-12 20:34`; the `2026-05-21 18:42`, `2026-05-21 09:41`, and `2026-05-20 09:30` values also align at supplied precision | Consecutive minute-aligned publication-time agreement |
| 叶北北 | Douyin ID `Leo66627`, UID `104813602020`, identity-matched `sec_uid`; latest public work epoch `1730111245` = `2024-10-28 18:27:25 Asia/Shanghai`; `has_more=1` | More old history does not itself invalidate the observed latest-publication sample; account is well beyond 180 days inactive |

The only supplied Douyin visibility-deny evidence frozen here is:

```text
is_delete == false
is_private == false
is_prohibited == false
private_status == 0
```

The adapter must version a positive eligibility mapping around the exact response
schema it supports. This freeze does not assign meanings to unobserved fields or
values. An unrecognized status/schema combination is untrusted, not public by
default and not silently discarded.

Verdict: **DOUYIN_TIKHUB_FUNCTIONAL_PROVIDER_PROOF_PASS**.

### 3.2 Xiaohongshu

Proven identity/activity endpoints:

- `/api/v1/xiaohongshu/app_v2/get_user_info`
- `/api/v1/xiaohongshu/app_v2/get_user_posted_notes`

Initial resolution may use profile `share_text` or a share URL. The returned
`userid` is the stable provider/platform identity used by the accepted samples and
must be persisted in platform/provider identity lineage after resolution. It must
not be described as universally immutable without a versioned revalidation rule.

Accepted samples:

| Account | Evidence | Frozen conclusion |
|---|---|---|
| po老师（英语版） | `userid=6540a349000000000400842f`; older sticky notes preceded current notes; maximum epoch `1787053474` = `2026-08-18 19:44:34 Asia/Shanghai`; Grey Dolphin exact-to-second match; type `video` | Identity, pinned-order, latest time, and video proof PASS |
| 邹邹老师呀 | `userid=63269947000000002303a227`; latest three at `2026-07-04 21:45:50`, `2026-06-15 16:05:09`, and `2026-06-03 18:30:34`, all exact-to-second Grey Dolphin matches; raw type `normal`; old sticky notes led the list | Image/text notes participate; video-only logic and list order are forbidden |
| 自媒体创业 | `userid=6097b0410000000001003595`, `red_id=4217549300`; current notes response had `has_more=false`; current endpoint/profile maximum epoch `1719579078` = `2024-06-28 20:51:18 Asia/Shanghai`; current public profile omits Grey Dolphin's later historical `2025-12-04 21:46:05` item | Strong proof that historically published is not currently publicly visible; the hidden/deleted/non-public historical item must not keep the account active |

Raw provider type `normal` is adapter-specific. It normalizes to the platform-neutral
image/text category for this evidence. Both image/text and video publications are
eligible. A new type may normalize to `OTHER` only when endpoint and visibility
semantics establish it as a publication; an unknown potentially newer item cannot
be silently omitted.

Verdict:
**XIAOHONGSHU_TIKHUB_CURRENT_PUBLIC_VISIBLE_FUNCTIONAL_PROVIDER_PROOF_PASS**.

## 4. Identity and provider lineage

The canonical owner of an observation is the internal
`InfluencerPlatformAccount.id`. The repository currently enables only
`xiaohongshu` in its `Platform` enum. Future Content Activity must consume an
accepted canonical Douyin account/identity capability through a separately
authorized release-integration step. It must not duplicate, modify, or couple to
the excluded Phase 3B connector lineage, and the baseline gap does not block this
contract freeze.

Identity resolution and activity collection are independent provenance roles:

| Platform | Identity source | Preferred platform/provider identity | Additional lineage | Activity source |
|---|---|---|---|---|
| Douyin | Existing internal Douyin protocol capability | namespaced `douyin.sec_uid` binding | UID and Douyin short/current account ID when available | TikHub Douyin App V3 |
| Xiaohongshu | TikHub resolution at present | namespaced `xiaohongshu.userid` binding | profile share input for bootstrap; `red_id` only as display/cross-check evidence | TikHub Xiaohongshu App V2 |

Nickname is never identity authority. A share URL is bootstrap input, not a
required refresh credential. `red_id`, handle, nickname, and profile URL remain
display/cross-check identity unless separately proven stable.

The observation references an identity binding. Provider-specific identity lives
in a provider-lineage component with this neutral shape:

```text
ProviderAccountIdentity {
  platform
  namespace
  opaque_external_id
  identity_source
  resolved_at
  provenance_ref
}
```

Thus `sec_uid` and Xiaohongshu `userid` do not become provider-shaped columns in
the cross-platform observation. `activity_source_provider` explicitly means the
activity source and never implies that the same provider resolved identity.

## 5. Normalized publication and unified maximum rule

Each provider adapter normalizes potentially eligible rows to an internal
candidate before aggregation:

```text
NormalizedPublicationCandidate {
  platform
  publication_id_namespace
  canonical_publication_id
  published_at_utc
  publication_type = VIDEO | IMAGE_TEXT | OTHER | UNKNOWN
  visibility_decision = ELIGIBLE | DENY | UNTRUSTED
  is_pinned_or_sticky
  provenance_ref
}
```

Eligibility requires all of the following:

1. the response belongs to the requested identity;
2. the item came from an approved current-public-profile listing contract;
3. the adapter's versioned visibility/publication mapping accepts it; and
4. no proven deleted/private/prohibited/non-public condition applies.

Processing order and invariants:

1. Reject explicit deny rows.
2. Treat unknown visibility or publication eligibility as observation-untrusted.
3. Require a valid platform-native publication ID and unambiguous publication
   timestamp for every potentially public row.
4. Normalize timestamps to UTC.
5. Deduplicate by `(platform, publication_id_namespace,
   canonical_publication_id)`.
6. A duplicate with conflicting time, visibility, or material identity fails the
   observation closed; the adapter does not choose a convenient version.
7. Include eligible pinned/sticky and non-pinned items in the same candidate set.
8. Compute `MAX(published_at_utc)` across that set.

If multiple publications share the maximum second, `last_publication_at` remains
exact. `latest_publication_id` is the lexicographically smallest namespaced key at
that maximum and is explicitly a deterministic representative, not a claim that
it was uniquely later. `co_latest_publication_count` records the tie count; the
representative's normalized type supplies `latest_publication_type`.

## 6. Canonical Content Activity observation

`ContentActivityObservation` is the immutable normalized outcome of one refresh
attempt. It is platform-neutral and conceptually contains:

Three architectures were assessed. Separate Douyin/TikHub and
Xiaohongshu/TikHub business records would leak provider shapes and make replacement
unsafe. One overloaded status enum would conflate refresh success, coverage, and
the business result. The selected architecture is one immutable neutral
observation with identity/activity lineage and three orthogonal state dimensions.

| Field | Frozen meaning |
|---|---|
| `schema_version` | Closed contract version |
| `platform_account_id` | Internal `InfluencerPlatformAccount.id` |
| `platform` | Canonical platform enum, consistent with the bound account |
| `activity_semantics` | Exactly `CURRENT_PUBLIC_VISIBLE` |
| `attempt_started_at` / `observed_at` | UTC traversal start and accepted completion/as-of time |
| `observation_status` | Whether the attempt completed and can be trusted |
| `coverage_status` | What coverage/completion evidence supports the result |
| `activity_result` | Business fact, if one was established |
| `identity_binding_ref` | Reference to namespaced platform/provider identity lineage |
| `identity_source` | Logical source role; separate from activity source |
| `activity_source_provider` | Activity provider registry key, not raw JSON shape |
| `provider_product` / `adapter_version` | Versioned provider endpoint family and adapter contract |
| `visibility_policy_version` | Exact eligibility mapping used |
| `provider_account_identity` | Namespaced opaque identity in provider lineage, or a binding reference to it |
| `last_publication_at` | UTC instant; present only for exact publication-found results |
| `latest_publication_id` | Namespaced deterministic representative at the maximum |
| `latest_publication_type` | Normalized representative type |
| `co_latest_publication_count` | Count of publications tied at the maximum second |
| `coverage_start_at` / `coverage_end_at` | Required when a bounded source supplies coverage semantics |
| `timestamp_encoding` | For example `UNIX_SECONDS`; never inferred from a request timezone |
| `source_timezone` / `timezone_basis` | Required when raw timestamps are timezone-dependent |
| `normalized_timezone` | Always `UTC` |
| `request_ref` / `provenance_ref` | Secret-free internal references only |
| `scan_terminal_reason` | Exhausted, completion-predicate, bounded, or failure reason |
| `scanned_page_count` / `scanned_item_count` | Non-secret operational evidence |
| `provider_error_class` / `provider_error_code` | Sanitized, closed diagnostics when relevant |

This is a conceptual contract, not a frozen physical table or migration. A future
model must still follow repository requirements for IDs, timestamps, enum-backed
core states, service/repository boundaries, audit, and set-based reads.

TikHub response shapes, Grey Dolphin columns, `HUITUN_CSV`, `sec_uid`, Xiaohongshu
`userid`, signed URLs, and provider request parameters are not canonical
cross-platform observation fields. They remain inside adapter or identity lineage.

## 7. Orthogonal status, coverage, and result model

Uncertainty must not become inactivity. Three dimensions are frozen rather than
overloading one enum.

### 7.1 `observation_status`

- `COMPLETE`: the attempt is trusted for the declared coverage.
- `IDENTITY_UNRESOLVED`: no authoritative account binding was established.
- `ACCESS_RESTRICTED`: private/restricted/not-accessible ambiguity prevents a
  current-public determination.
- `PROVIDER_AUTH_ERROR`: provider authentication failed.
- `PROVIDER_RATE_LIMITED`: the provider rate limit blocked determination.
- `PROVIDER_ERROR`: transport/upstream/provider failure not covered above.
- `RESULT_INCOMPLETE`: traversal or provider result did not establish sufficient
  coverage.
- `RESULT_UNTRUSTED`: schema, visibility, identity, timestamp, duplicate, or
  snapshot consistency was unsafe.
- `UNKNOWN`: no narrower closed state applies.

### 7.2 `coverage_status`

- `FULL_CURRENT_PUBLIC_SET`: trusted endpoint exhaustion established the full
  current-public set, including coherent snapshot and platform content-type
  completeness.
- `LATEST_BOUND_PROVEN`: the full history was not enumerated, but a versioned
  completion predicate proved unseen rows cannot contain a later eligible item.
- `LOOKBACK_BOUNDED`: a complete, visibility-proven bounded interval supports only
  a left-censored lower-bound result.
- `INCOMPLETE`: a finite budget, provider marker, or traversal anomaly prevented
  sufficient coverage.
- `UNKNOWN`: coverage meaning cannot be established.

### 7.3 `activity_result`

- `PUBLICATION_FOUND`
- `NO_PUBLIC_CONTENT`
- `AT_LEAST_LOOKBACK_INACTIVE`
- `UNDETERMINED`

Required combinations and nullability:

| Observation | Coverage | Result | Required fact |
|---|---|---|---|
| `COMPLETE` | `FULL_CURRENT_PUBLIC_SET` or `LATEST_BOUND_PROVEN` | `PUBLICATION_FOUND` | publication time, representative ID/type, tie count, and provenance present |
| `COMPLETE` | `FULL_CURRENT_PUBLIC_SET` | `NO_PUBLIC_CONTENT` | publication fields null; no exact inactivity value |
| `COMPLETE` | `LOOKBACK_BOUNDED` | `AT_LEAST_LOOKBACK_INACTIVE` | complete coverage bounds and visibility provenance; exact time/ID/days null |
| any unresolved/restricted/error/incomplete/untrusted/unknown | `INCOMPLETE` or `UNKNOWN` | `UNDETERMINED` | no publication or inactivity claim |

The earlier Grey Dolphin four states map without controlling real-time providers:

```text
EXACT                         -> trusted PUBLICATION_FOUND
AT_LEAST_LOOKBACK_INACTIVE   -> trusted LOOKBACK_BOUNDED lower bound
NO_PUBLIC_CONTENT             -> trusted FULL_CURRENT_PUBLIC_SET empty result
UNKNOWN                       -> UNDETERMINED
```

A zero-row bounded file without affirmative evidence does not prove either
`AT_LEAST_LOOKBACK_INACTIVE` or `NO_PUBLIC_CONTENT`.

## 8. Observation history and current projection

Observations are append-only evidence. The current projection is selected from the
newest trusted observation under a versioned source-trust and freshness policy,
using `observed_at`, not ingestion order and never the greatest publication time.

- A newer trusted observation may move the value backward.
- A newer trusted `NO_PUBLIC_CONTENT` observation clears the current exact
  publication fact.
- A failed, restricted, incomplete, or untrusted attempt is retained separately
  and does not erase the last trusted observation.
- If an older trusted value is displayed after a failed refresh, it is explicitly
  `last-known`, carries its original `observed_at`, and is not relabeled current.
- A late-arriving historical/fallback observation cannot overwrite a newer trusted
  current-public observation.
- Materially conflicting concurrent trusted providers are untrusted until a
  documented provider-precedence rule resolves them; they are never reconciled by
  `MAX(last_publication_at)`.
- Equal-`observed_at` conflicting results are quarantined rather than silently
  tie-broken.

`CURRENT_PUBLIC_VISIBLE` is an as-of observation, not a continuous real-time
guarantee. A future product freshness policy must define when an otherwise exact
observation becomes stale for filtering.

## 9. Pagination and pinned/sticky safety

The default frozen policy is **trusted exhaustion or unknown**.

Pagination bounds protect the system, not the truth claim. Reaching a bound never
converts a partial maximum into `last_publication_at`. The supplied TikHub samples
prove pinned-order risk and functional matches; they do not establish a generic
first-page, post-pinned ordering, snapshot-consistency, or early-stop guarantee.

Every adapter must:

1. start from its initial opaque cursor and use finite configured page, item, and
   deadline budgets;
2. validate response schema, requested-account identity when echoed, provider
   incomplete markers, and any snapshot token or consistency signal;
3. validate cursor progress and reject a missing required continuation cursor when
   `has_more=true`, cursor cycles, and contradictory `has_more` state;
4. normalize visibility before aggregation;
5. fail untrusted when a potentially public row has unknown eligibility, missing
   ID, ambiguous/malformed timestamp, or conflicting duplicate;
6. include pinned/sticky publications in the candidate set;
7. continue until trusted `has_more=false`, or until a versioned adapter completion
   predicate proves no unseen page can contain a later eligible publication;
8. for more than one page, require a provider-consistent snapshot/cursor guarantee
   or an approved versioned validation/re-read protocol that establishes equivalent
   consistency;
9. separately prove that the listing contract covers every publication type in
   the platform's Content Activity policy;
10. compute the maximum only after all applicable terminal, snapshot, and
    content-universe conditions; and
11. record the terminal reason and non-secret page/item counts.

An early-stop completion predicate requires documented or provider-tested ordering
or a page upper-bound guarantee plus runtime validation. Seeing one non-sticky
item, apparently descending timestamps, a plausible maximum, or an old pinned
prefix is insufficient. No such generic predicate is established by the supplied
samples, so initial exact mode must traverse to trusted exhaustion. Each deployed
adapter version must publish finite budgets before enablement; the numeric limits
depend on approved quotas and are not invented in this design.

`has_more=false` proves endpoint exhaustion, not snapshot consistency or content-
type completeness. A multi-page traversal may emit `FULL_CURRENT_PUBLIC_SET` only
when the adapter contract establishes a coherent provider snapshot or an approved
validation protocol establishes equivalent consistency. Without that evidence,
exhaustion returns `RESULT_UNTRUSTED / UNKNOWN / UNDETERMINED`.

When a budget is reached with more pages possible, return:

```text
observation_status = RESULT_INCOMPLETE
coverage_status = INCOMPLETE
activity_result = UNDETERMINED
```

Do not emit official `last_publication_at` or inactivity from the partial maximum.

An exhausted all-pinned result can be exact because pinning does not make a public
publication ineligible. An all-pinned partial page must continue or fail
incomplete. An empty first page alone does not prove no public content. Exact
`NO_PUBLIC_CONTENT` requires successful identity and access, approved complete-
listing/content-type semantics, trusted `has_more=false`, coherent snapshot
semantics when applicable, and no contradictory evidence. Empty data plus
`has_more=true`, a provider incomplete marker, contradictory trusted profile
evidence, a cursor anomaly, or a violated asserted ordering rule fails closed.

A paginated scan spans time and may not be an atomic snapshot. Store scan start
and completion, retain only a secret-free snapshot reference if the provider
supplies one, and treat detected mutation symptoms such as conflicting duplicates
or cursor cycles as untrusted.

## 10. Timezone, observation time, and inactivity

All canonical publication instants are stored and compared in UTC.

- TikHub Unix `create_time` seconds are timezone-independent instants. Record the
  encoding as `UNIX_SECONDS`, normalize to UTC, and use `Asia/Shanghai` only for
  display and proof comparison.
- A provider request timezone such as `America/Los_Angeles` is not publication
  timezone provenance and must not affect normalization.
- A bounded Grey Dolphin naive timestamp retains its raw value plus the explicit
  source timezone and basis. The earlier design's `Asia/Shanghai` /
  `ASSUMED_PENDING_VALIDATION` rule remains specific to that adapter.
- Ambiguous units, out-of-range timestamps, or malformed timestamps on potentially
  public rows make the observation untrusted.
- A publication timestamp later than the observation's authoritative
  `observed_at` is untrusted. This contract does not apply a clock-skew tolerance
  or clamp a future timestamp to zero inactive days.

Capture one authoritative `as_of_utc` for a read/filter operation. For an exact
publication result only:

```text
inactive_days = floor((as_of_utc - last_publication_at_utc) / 86400)
```

Do not persist `inactive_days`. It is null for no-public-content, bounded,
restricted, failed, incomplete, untrusted, unknown, and stale results. A bounded
source may expose an explicitly labeled lower-bound duration as of its
`coverage_end_at`; it never invents an exact last-publication time or a permanently
aging integer.

When complete bounded coverage plus affirmative evidence proves that publication
predates `coverage_start_at`, the only allowed lower-bound calculation is:

```text
inactive_days_at_least =
  floor((coverage_end_at_utc - coverage_start_at_utc) / 86400)
```

It is labeled as of `coverage_end_at` and does not continue aging without a newer
observation.

Filter elapsed-duration thresholds by timestamp. The inclusive 180-day rule is:

```text
last_publication_at_utc <= as_of_utc - duration("180 days")
```

Do not subtract local calendar dates.

## 11. Provider abstraction

The future shared boundary is conceptually:

```text
ContentActivitySourceAdapter.observe(
    platform_account,
    provider_identity_binding,
    finite_scan_budget,
    secret_free_request_ref,
) -> ContentActivityObservation
```

For batch refresh, the result is cardinality-preserving: every requested account
receives exactly one normalized observation outcome, including explicit identity,
access, provider, incomplete, or untrusted failures.

Provider-specific adapters own:

- endpoint and request parameters;
- authentication and rate limiting;
- identity mapping and response-account validation;
- response/schema parsing;
- positive visibility and publication eligibility mapping;
- timestamp and publication-type normalization;
- pagination/completion predicates and scan budgets;
- canonical publication-ID extraction and deduplication;
- provider error mapping; and
- sanitized provenance.

The business service owns normalized state invariants, observation persistence,
trusted-current selection, elapsed-time derivation, and set-based projections.
Library, detail, Candidate, and Campaign consumers never read TikHub JSON or Grey
Dolphin columns and must not perform frontend N+1 hydration.

## 12. Security and data minimization

Raw request/response retention is disabled by default. Never persist, log, trace,
emit in metrics, or expose through API/UI:

- Authorization Bearer tokens, API keys, cookies, or credentials;
- raw authorization headers or raw request URLs/query strings/bodies;
- profile `share_text` or share URLs after identity resolution;
- signed temporary CDN, `cache_url`, subtitle, image, video, or media-stream URLs;
- media bytes or unnecessary image/video payloads;
- giant/full provider responses;
- raw provider error bodies/messages that may contain request data; or
- hashes of tokens or signed URLs as purported safe provenance.

Persist only the minimum allowlisted evidence required for the internal account
binding, namespaced publication ID, publication time, normalized type, result and
coverage states, provider/adapter/mapping version, timestamps, scan summary,
sanitized error class/code, and opaque provenance references.

Adapters must request only the fields needed for those facts. They must not invoke
media/detail/download endpoints, dereference, download, or preflight signed
media/CDN/cache/subtitle URLs, fetch image/video/subtitle payloads, or request
optional media expansions merely because the provider response offers them.

`request_ref` is an internally generated opaque correlation ID. A provider request
ID may be retained only through an allowlist that proves it contains no secret.

Any future nonzero raw-debug retention requires a separately approved policy that
defines redaction before write, encryption, least-privilege access, Audit, finite
TTL, and verified deletion. This freeze does not authorize that policy.

## 13. Grey Dolphin future role

Grey Dolphin is no longer the mandatory source or blocking gate for the
Xiaohongshu MVP. It remains eligible for future evaluation as:

- fallback provider;
- bulk validation or provider cross-check;
- manual/enterprise import;
- historical observation; or
- an authorized batch/API source.

A future Grey Dolphin adapter must emit this same normalized contract. Its
`AT_LEAST_LOOKBACK_INACTIVE` result is allowed only when a closed lookback interval
was completely examined and observation-time visibility provenance proves hidden,
deleted, private, and non-public items were excluded. An unfiltered or
unknown-provenance CSV is `RESULT_UNTRUSTED / UNKNOWN / UNDETERMINED`.

A Grey Dolphin file adapter must bind every immutable file SHA-256 and manifest
entry to a stable internal PlatformAccount/provider identity. A multi-creator
delivery must include the requested-account roster and exactly one success, zero,
or explicit error outcome for every requested account. A note-only file,
nickname, or filename cannot establish creator association or distinguish a true
zero from an omitted/failed creator.

Historical Grey Dolphin evidence must not overwrite a newer trusted
current-public observation. A Grey Dolphin commercial/batch/API/RPA confirmation
gate remains specific to that future adapter.

## 14. Future product surface

Content Activity remains a sales-growth signal on existing surfaces:

- Influencer Library: last-publication time, exact inactivity days when available,
  state, freshness/as-of metadata, and inactivity threshold filters;
- Influencer detail: current-public last-known/trusted result, observation source,
  status, coverage, and latest attempt error;
- Candidate and Campaign workflows: reuse the same normalized account-bound fact.

This contract does not add a dashboard, navigation section, content feed, chart,
AI score, provider control panel, or unrelated redesign. A Campaign reuse path
must preserve the exact `platform_account_id` that satisfied the account-level
filter.

## 15. Future implementation sequence and acceptance gates

No step begins in this documentation task. The clean future sequence is:

1. design and migrate canonical observation persistence plus trusted-current and
   latest-attempt projections;
2. implement the provider adapter interface and closed enums;
3. integrate an accepted canonical Douyin account/identity capability through a
   separately authorized release step, without changing or duplicating Phase 3B,
   then implement the TikHub Douyin adapter;
4. implement Xiaohongshu share-resolution identity binding and TikHub adapter;
5. add refresh orchestration, finite budgets, quota/rate-limit handling, and
   concurrency/idempotency rules;
6. add provider-neutral Library/API projections and timestamp filters;
7. add minimal detail display;
8. reuse the same account-bound observation in Candidate/Campaign flows;
9. add observability, sanitized diagnostics, current/last-known freshness policy,
   and conflict handling;
10. run targeted unit, integration, real PostgreSQL, API, Worker, Web, security,
    and set-based performance tests; and
11. pass a separate provider-governance/commercial readiness gate before
    production enablement.

The implementation acceptance matrix must cover at least:

- both accepted platforms and identity-source/activity-source separation;
- pinned/sticky prefixes, unsorted fixtures, and `list[0]` prohibition;
- image/text, video, `OTHER`, and unsafe unknown types;
- independent traversal and platform publication-type completeness, including
  unknown potentially newer Douyin types;
- visibility deny flags only within versioned proven mappings;
- exact maximum, duplicate collapse, conflicting duplicate failure, and tied
  maximum representative/count behavior;
- UTC normalization, `Asia/Shanghai` display, request-timezone irrelevance, and
  ambiguous/future timestamp failure;
- endpoint exhaustion, cursor cycles, incomplete markers, empty first page,
  all-pinned pages, budget exhaustion, multi-page snapshot consistency, and a
  versioned early-stop predicate;
- all status/coverage/result combinations and nullability invariants;
- a newer trusted result moving backward or to no-public-content;
- failed attempts preserving but not relabeling last-known trusted evidence;
- Grey Dolphin visibility provenance and left-censoring;
- elapsed-day derivation and exact 180-day boundary comparison;
- secret/raw-payload exclusion from database, logs, traces, metrics, and API; and
- provider-neutral set-based Library/Candidate/Campaign reads without N+1.

## 16. Remaining readiness work and final verdict

There is no remaining technical-data blocker to this documentation-only contract
freeze or to beginning a separately authorized implementation plan.

Production enablement still requires implementation evidence for:

- provider ordering, exhaustion, cursor, and coherent snapshot semantics required
  for exact multi-page traversal and any early-stop optimization; until proven,
  fail untrusted/unknown;
- platform publication-type universe evidence, especially for Douyin's proven
  video-named endpoint;
- versioned response-schema and visibility mappings;
- finite scan-budget values derived from approved quotas;
- accepted canonical Douyin PlatformAccount/identity release integration without
  duplicating or modifying Phase 3B;
- a product freshness policy for current versus last-known observations; and
- full implementation and release gates.

TikHub pricing, license, terms, production data-use rights, quotas, support, and
SLA remain a separate provider-governance follow-up. Their absence does not
falsify the accepted functional data proof and this document does not invent an
approval.

Final verdict: **UNIFIED_CONTENT_ACTIVITY_CONTRACT_FREEZE_PASS**

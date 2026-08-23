# Content Activity Provider Capability and Douyin Identity Prerequisites

Status: **PREREQUISITE CONTRACT FROZEN — DOCUMENTATION ONLY**

Date: 2026-08-23

Worktree: `/Users/mac/Downloads/influencer_outreach_content_activity_impl`

Baseline: `codex/content-activity-implementation-p0@40deabe004a31e8fe83c19d7f2c3791d7c1f5690`

Authoritative semantic contract:
`docs/superpowers/specs/2026-08-22-unified-content-activity-contract.md`

Capability policy:
`TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1`

This document closes architecture prerequisites only. It does not implement or
authorize production source, a migration, API/Web/Worker changes, a live TikHub
call, secret entry, production access, production deployment, or Phase 3B work.
It does not modify or supersede the authoritative semantic contract.

## 1. Executive conclusion

The original `UNIFIED_CONTENT_ACTIVITY_P0_IMPLEMENTATION` blocker is confirmed:

1. the baseline has no canonical Douyin PlatformAccount or namespaced
   `douyin.sec_uid` binding; and
2. before this document, no versioned TikHub capability policy defined when a
   provider result could become trusted `CURRENT_PUBLIC_VISIBLE` evidence.

This prerequisite contract closes the second blocker at the architecture level
and freezes the minimum contract needed to close the first later. Its product
decision is:

```text
XHS_FIRST_IMPLEMENTATION = APPROVED
```

The Douyin blockers do not block D1A because the canonical persistence and
identity-lineage design is platform-neutral from its first migration. D1A may
implement XHS only under the strict v1 acceptance gates below. D1A must not add
`Platform.DOUYIN`, call a Douyin activity endpoint, or emit a trusted Douyin
observation.

The provider decisions are:

```text
XHS_APP_V2_SINGLE_RESPONSE_TERMINAL = TRUST_ELIGIBLE, NOT AUTOMATICALLY TRUSTED
XHS_APP_V2_MULTI_RESPONSE_TRAVERSAL = UNTRUSTED
DOUYIN_APP_V3_PROVIDER_CALL_BUDGET = 0
DOUYIN_APP_V3_TRUSTED_ACCEPTANCE = BLOCKED
```

`has_more=false` alone is never sufficient for trust. Every universal and
platform-specific gate in this policy must pass in the same coherent observation.

## 2. Frozen semantic inheritance

All implementation remains governed by the authoritative contract. This document
does not reinterpret these invariants:

- activity semantics is exactly `CURRENT_PUBLIC_VISIBLE`;
- `last_publication_at = MAX(valid current-public-visible published_at)` within
  one coherent trusted observation;
- pinned/sticky items remain eligible but never establish ordering;
- `list[0]`, provider ordering, update time, import time, observation time,
  historical aggregates, and historical `GREATEST` are forbidden substitutes;
- a newer trusted observation may move `last_publication_at` backward or clear it;
- `inactive_days` is derived at read time and is never persisted;
- UNKNOWN, provider failure, incomplete traversal, stale evidence, and no-public-
  content are not exact inactivity;
- identity source and activity provider are independent lineage roles; and
- observation status, coverage status, and activity result remain orthogonal.

## 3. Original blocker confirmation

### 3.1 Platform and account state

Repository evidence at the baseline establishes:

- `packages/backend_core/src/backend_core/influencers/enums.py:6-7` defines only
  `Platform.XIAOHONGSHU`;
- `infrastructure/migrations/versions/0003_phase1b_import.py:40` creates
  `platform_enum` with only `xiaohongshu`, and no later baseline migration extends
  it;
- executable migrations form one chain through
  `0007_phase3a_persistence_amendment`;
- `InfluencerPlatformAccount` is a neutral internal owner with UUID identity,
  platform-scoped account/profile uniqueness, and a composite `(id, platform)`
  key suitable for platform-consistent child relations; and
- its nullable `platform_account_id`, handle, and profile URL fields do not
  provide namespacing, resolver provenance, verification state, or append-only
  identity replacement history.

The internal `InfluencerPlatformAccount.id` remains the canonical owner of every
Content Activity observation. Provider identity does not replace this ID.

### 3.2 Existing external identity abstraction

`PlatformAccountSourceIdentity` is not reusable as the canonical provider identity
binding:

- it keys identity by import `DataSource`, platform, and external account ID;
- `DataSource` currently contains only `huitun`, `generic`, and `manual`;
- first/last ImportJob and ImportRow references are mandatory;
- creation occurs inside the import merge path with real import provenance; and
- it has no namespace, resolver source/version, verified/current state, or
  secret-free resolver provenance reference.

Using it for TikHub or a Douyin protocol resolver would require fabricated import
lineage and would collapse identity provenance into data-import provenance.
Therefore it remains unchanged and continues to serve import matching/dedup only.

### 3.3 Executable Douyin state

The tracked baseline contains no executable `sec_uid`, `sec_user_id`, TikHub
client, TikHub adapter, or Douyin identity resolver. Existing Douyin outreach
enumeration is not account identity capability: the service explicitly returns
`CHANNEL_UNAVAILABLE` because canonical Douyin account data is absent.

Consequently:

```text
CANONICAL_DOUYIN_IDENTITY_CURRENT_STATE = ABSENT
DOUYIN_ACTIVITY_ADAPTER_CURRENT_STATE = BLOCKED_BY_IDENTITY
```

Historical discussions or code in another worktree do not change this baseline
fact.

## 4. XHS-first decision

`XHS_FIRST_IMPLEMENTATION = APPROVED` for D1A because:

1. the internal account owner is already platform-neutral;
2. a shared `ProviderAccountIdentity` relation can hold both
   `xiaohongshu.userid` and future `douyin.sec_uid` bindings;
3. shared observation, trusted projection, and latest-attempt projection tables
   need no platform-specific duplication;
4. the frozen XHS functional proof permits a conservative single-response
   capability policy; and
5. future `Platform.DOUYIN` and Douyin bindings can be added without rewriting XHS
   observation history or API semantics.

Approval is conditional on all of the following:

- D1A persistence is platform-neutral;
- the XHS identity intake in section 10 is implemented exactly and fail closed;
- the executable adapter pins an allowlisted sanitized v1 response schema/fixture;
- all v1 capability gates are enforced before a trusted observation is written;
- UNKNOWN is excluded from inactivity filters and targeting matches;
- Content Activity remains separate from existing Huitun/data freshness; and
- production provider governance remains closed.

## 5. Capability Policy v1

The immutable policy identifier is:

```text
TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1
```

Its subordinate contract registry is closed, not dynamically extensible:

```text
TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1
TIKHUB_XHS_APP_V2_GET_USER_POSTED_NOTES_RESPONSE_SCHEMA_V1
TIKHUB_XHS_APP_V2_CURRENT_PUBLIC_VISIBILITY_POLICY_V1
```

These are the only response/visibility contract IDs that V1 may accept. The
initial D1A implementation must define each ID with one strict source-controlled
schema artifact, sanitized positive/negative fixtures, and a content digest in
the adapter registry. That first implementation review is the one-time
materialization of the names and acceptance rules frozen here; until all three
artifacts exist and pass review, XHS trusted writes remain disabled. After that
review, changing an artifact, accepted field/value, semantic-success predicate,
or digest under the same ID is forbidden. Any such change requires a new
subordinate ID and a new capability-policy version.

The following names are reserved for future Douyin evidence but are **not** in
V1's accepted registry:

```text
TIKHUB_DOUYIN_APP_V3_FETCH_USER_POST_VIDEOS_RESPONSE_SCHEMA_V1_RESERVED
TIKHUB_DOUYIN_APP_V3_CURRENT_PUBLIC_VISIBILITY_POLICY_V1_RESERVED
```

Their presence in source, fixtures, or configuration cannot enable a Douyin V1
call or trusted result.

It governs only technical eligibility to create trusted Content Activity
evidence. It is not a general TikHub feature catalog and does not approve pricing,
terms, production rights, quota, support, or SLA.

Every observation must record at least:

- `provider = TIKHUB`;
- exact `provider_product` and endpoint family/version;
- `adapter_version`;
- `capability_policy_version = TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1`;
- exact schema/visibility policy version used by that adapter;
- `identity_binding_ref`, `identity_verification_ref`, and separate
  `identity_source`;
- `activity_source_provider`;
- secret-free request/provenance references;
- attempt start and observed/completion instants in UTC;
- scan terminal reason and non-secret page/item counts; and
- sanitized provider error class/code when applicable.

An observation is interpreted permanently under the recorded policy. A future
policy revision may select or supersede it in a new projection policy, but must
not rewrite what the old observation meant when recorded.

## 6. Universal trusted-acceptance gates

A provider response is trust-eligible only when every applicable gate below passes
in one observation. Failure of one gate prevents an exact activity result.

### 6.1 Identity

- The requested internal `InfluencerPlatformAccount` exists and is active.
- A current `VERIFIED_CURRENT` policy-accepted `ProviderAccountIdentity` exists.
- The binding platform and namespace match the requested adapter.
- The response belongs to the requested identity whenever the response exposes
  identity evidence.
- No identity conflict, replacement race, or unresolved bootstrap exists.

### 6.2 Transport and provider semantics

- The HTTP request succeeds within configured connect/read/deadline bounds.
- The response uses the exact endpoint/product version registered by v1.
- The response parses under the adapter's strict, allowlisted v1 schema.
- Every required provider business-success marker is present and has the exact
  accepted value pinned by the sanitized v1 fixture/schema.
- HTTP success without provider semantic success is not success.
- Missing, contradictory, or unknown success markers fail closed.

The baseline contains no executable TikHub response fixture. D1A must add minimal
sanitized fixtures that pin the exact response envelope, business-success values,
publication ID path, timestamp path, type path, visibility/access markers, and
pagination fields. Implementers must not guess raw field paths or success codes.

### 6.3 Publication candidates

- Every potentially public item has a valid platform-native publication ID.
- Every timestamp that could affect the maximum parses unambiguously as UNIX
  seconds and normalizes directly to UTC.
- No candidate publication timestamp is later than authoritative `observed_at`.
- Every type and visibility combination is recognized by the exact policy.
- Explicit deny items are excluded only through a proven mapping.
- Unknown eligibility is observation-untrusted, not silently discarded.
- Duplicates collapse only when their material facts agree; conflicts fail closed.
- Pinned/sticky and non-pinned eligible items participate in the same candidate
  set.
- The maximum is computed only after all applicable coverage gates pass.

### 6.4 Coverage and coherence

- No truncation, hidden limit, incomplete marker, missing cursor, cursor anomaly,
  unsupported completeness indicator, or page/item/deadline budget exhaustion is
  present.
- The terminal condition is explicit and belongs to this exact policy version.
- The content universe is classified complete for the exact platform/product
  policy.
- The observation satisfies the policy's coherence classification.
- No policy-defined uncertainty is present.

`has_more=false` is only one coverage fact. It does not prove identity, semantic
success, schema validity, visibility, content-universe completeness, timestamp
validity, absence of hidden limits, or coherence by itself.

## 7. XHS App V2 capability matrix

| Capability field | Frozen v1 decision |
|---|---|
| Provider | `TIKHUB` |
| Product | `XIAOHONGSHU_APP_V2` |
| Endpoint | `/api/v1/xiaohongshu/app_v2/get_user_posted_notes` |
| Response schema | `TIKHUB_XHS_APP_V2_GET_USER_POSTED_NOTES_RESPONSE_SCHEMA_V1` only |
| Visibility policy | `TIKHUB_XHS_APP_V2_CURRENT_PUBLIC_VISIBILITY_POLICY_V1` only |
| Canonical identity | current `VERIFIED_CURRENT` `xiaohongshu.userid` binding |
| Identity bootstrap input | bounded resolver input only; never canonical identity or refresh credential |
| Activity request identity | the verified `xiaohongshu.userid` through an input mapping proven by the exact response/adapter contract |
| Publication timestamp | valid UNIX-seconds `create_time` |
| Forbidden timestamp | `last_update_time` and any update/import/observation surrogate |
| Closed content types | `normal -> IMAGE_TEXT`, `video -> VIDEO` |
| Unknown content type | observation-untrusted |
| Current-public basis | recognized current-public posted-notes listing contract |
| Private/deleted/hidden mapping | no row-level deny flag/value is proven in v1; membership in the strict complete current-public listing is the positive basis, and any returned decision-bearing status outside the exact schema is observation-untrusted |
| Sticky/pinned | eligible when otherwise valid; provides no ordering |
| Page budget for exact v1 path | one provider response |
| Terminal condition | first response contains explicit boolean `has_more=false` |
| Coherence classification | `SINGLE_RESPONSE_COHERENT_ONLY` |
| Multi-response classification | untrusted under v1 |
| Content universe | closed v1 `{normal, video}` for this exact product/schema |
| Trusted result | only after all universal and XHS-specific gates pass |
| Evidence version | authoritative contract plus this v1 policy and pinned adapter schema version |

`SINGLE_RESPONSE_COHERENT_ONLY` means that one request produces one immutable
parsed response envelope and all identity echoes, semantic-success markers,
publication candidates, completeness markers, item counts, and `has_more` are
evaluated from that same envelope. V1 forbids stitching retries or responses,
mixing requested/echoed identities, accepting conflicting duplicates, or
accepting internally contradictory status, count, or pagination evidence.

### 7.1 XHS trusted terminal rule

A non-empty XHS response may produce:

```text
observation_status = COMPLETE
coverage_status = FULL_CURRENT_PUBLIC_SET
activity_result = PUBLICATION_FOUND
```

only when all of these are true simultaneously:

1. canonical `xiaohongshu.userid` is resolved through an allowed verified binding;
2. HTTP transport succeeded;
3. provider semantic success passed the strict adapter schema;
4. endpoint/product/schema versions match v1;
5. every candidate row belongs to the closed `normal`/`video` universe;
6. every decision-bearing timestamp/ID/type/visibility field is recognized and
   valid;
7. current-public visibility/access semantics are recognized;
8. no truncation, hidden limit, unsupported completeness indicator, or
   contradictory profile evidence exists;
9. the first response explicitly contains `has_more=false`;
10. no schema drift, unknown content type, duplicate conflict, future timestamp,
    or policy-defined uncertainty exists; and
11. the single-response coherence rule passes.

Only then is `last_publication_at` the maximum eligible `create_time` across that
response. In particular:

```text
has_more=false alone != trusted
```

### 7.2 XHS empty result

An empty first response may produce trusted `NO_PUBLIC_CONTENT` only when identity
and access were affirmatively established, the recognized complete-listing schema
was returned, the first response explicitly has `has_more=false`, all content-
universe and coherence gates pass, and no contradictory evidence exists.

An empty body, missing data, restricted/private ambiguity, semantic error, unknown
schema, or empty list without affirmative access/completeness is
`UNDETERMINED`, not `NO_PUBLIC_CONTENT` and never infinite inactivity.

### 7.3 XHS multi-page rule

If the first response has `has_more=true`, v1 must not save its partial maximum as
trusted evidence. The immediate result is:

```text
observation_status = RESULT_INCOMPLETE
coverage_status = INCOMPLETE
activity_result = UNDETERMINED
```

The D1A/V1 runtime stops after that first response and makes no second activity
call. If separately authorized nonproduction fixture simulation or capability-
evidence work examines a hypothetical later `has_more=false`, the combined
multi-response traversal still cannot become trusted under v1 because no
snapshot token, provider snapshot guarantee, or approved re-read validation
protocol is frozen. Its result is:

```text
observation_status = RESULT_UNTRUSTED
coverage_status = UNKNOWN
activity_result = UNDETERMINED
```

Completing traversal does not cure coherence uncertainty. Supporting trusted
multi-page XHS activity requires new capability evidence and a new explicit
policy version. V1 must never be silently relaxed.

### 7.4 XHS content-universe rule

V1 recognizes only `normal` and `video` under the exact App V2 schema. A returned
new type, unknown visibility/status value, or schema contract that can hide or
omit a potentially newer publication makes the observation untrusted.

V1 has no inferred private/deleted/hidden row-level exclusion. It trusts only
membership in the recognized complete current-public listing. If the response
introduces an explicit private, deleted, hidden, restricted, or access field or
value not enumerated by the strict schema, the entire observation is untrusted;
the adapter must not silently drop that row. Evidence that the endpoint omits
any current-public type also invalidates the entire observation.

The v1 closed set is a versioned technical policy decision based on the accepted
XHS functional evidence; it is not a permanent claim about future Xiaohongshu or
TikHub product behavior. Contract drift requires a new policy version.

## 8. Douyin App V3 capability matrix

| Capability field | Frozen v1 decision |
|---|---|
| Provider | `TIKHUB` |
| Product | `DOUYIN_APP_V3` |
| Endpoint | `/api/v1/douyin/app/v3/fetch_user_post_videos` |
| Required identity | current `VERIFIED_CURRENT` `douyin.sec_uid` binding |
| Provider request identity field | adapter-local `sec_user_id` mapped from the verified binding |
| Forbidden identity substitutes | nickname, display URL, profile URL heuristic, fuzzy match, short ID without approved binding |
| Publication timestamp | valid UNIX-seconds `create_time` |
| Positive visibility tuple | exact recognized `is_delete=false`, `is_private=false`, `is_prohibited=false`, `private_status=0` |
| Other visibility combination | untrusted unless a later policy proves an explicit deny mapping |
| Pinned/top | include `is_top` when otherwise eligible; provides no ordering |
| Pagination | App V3 cursor/`has_more`; exact semantics not enabled in v1 |
| Snapshot/coherence | unproven for multi-response traversal |
| Content universe | `VIDEO_WORKS_SCOPED_ONLY / NOT_FULL_CURRENT_PUBLIC_SET` |
| Provider call budget | `0` for all of V1; any nonzero budget requires a new explicit capability-policy version |
| Trusted acceptance | disabled |

The frozen functional proof establishes a works/video activity capability and
positional-order risk. It does not establish that this video-named endpoint covers
every current-public Douyin publication type.

Therefore, even after D1B supplies a verified `douyin.sec_uid`, a hypothetical
one-response `has_more=false` result remains:

```text
observation_status = RESULT_UNTRUSTED
coverage_status = UNKNOWN
activity_result = UNDETERMINED
```

Content-universe completeness must be proven and a later policy version must
open trusted acceptance. Identity completion does not automatically close the
provider-completeness blocker, and V1 still authorizes no Douyin activity call.

Douyin trusted acceptance requires both:

```text
A. canonical verified namespaced douyin.sec_uid integration
B. sufficient Douyin App V3 publication-universe completeness evidence
```

Both are mandatory. Multi-response trusted activity additionally requires an
approved coherence/snapshot rule.

## 9. UNKNOWN and fail-closed rules

The following conditions cannot create or update a trusted activity projection:

- identity unavailable, unverified, superseded, revoked, or conflicting;
- provider timeout, transport failure, 5xx, authentication error, or rate limit;
- HTTP success with provider semantic failure;
- endpoint/product/adapter/capability version mismatch;
- malformed or unrecognized response schema;
- missing or malformed required publication ID/timestamp;
- future publication timestamp;
- unknown publication type;
- unsupported or unknown visibility/access status;
- duplicate conflict;
- hidden truncation or unsupported completeness indicator;
- page/item/deadline budget exhaustion;
- missing cursor, cursor cycle, or contradictory `has_more`;
- traversal incomplete;
- snapshot/coherence uncertainty;
- content-universe completeness uncertainty; or
- any decision-bearing schema drift.

Mapped provider failures use the narrow frozen `observation_status` where known.
Incomplete traversal uses `RESULT_INCOMPLETE / INCOMPLETE / UNDETERMINED`.
Schema, visibility, identity, duplicate, timestamp, coherence, or content-
universe uncertainty uses `RESULT_UNTRUSTED / UNKNOWN / UNDETERMINED` unless a
narrower frozen status applies.

No UNKNOWN attempt clears the last trusted projection. It updates latest-attempt
state only. UNKNOWN never matches an inactivity threshold.

## 10. XHS canonical identity intake

### 10.1 Current repository facts

The baseline does not already contain a verified `xiaohongshu.userid` relation.
It contains an XHS-only import bootstrap:

- `packages/backend_core/src/backend_core/imports/adapters.py:62-63` fixes the
  mapped adapter to `Platform.XIAOHONGSHU`;
- import may receive `platform_account_id`, `external_source_id`, or `profile_url`;
- `packages/backend_core/src/backend_core/imports/normalizers.py:46-73` accepts
  only an allowlisted Xiaohongshu host and `/user/profile/{id}` path, produces a
  canonical normalized profile URL, and extracts that path ID;
- the adapter stores the supplied/extracted value in the generic
  `InfluencerPlatformAccount.platform_account_id`, while
  `packages/backend_core/src/backend_core/influencers/models.py:73-94` stores
  profile URLs separately; and
- source external identity is import-lineage-bound, not provider-verified identity.

The repository's URL contract is a normalized profile URL, not a provider share
URL or `share_text` contract. These fields establish a syntactically validated
bootstrap/cross-check path, not a verified TikHub-returned
`xiaohongshu.userid`. D1A must not relabel all existing generic account IDs as
verified provider identities.

### 10.2 D1A intake source and verification

The canonical D1A intake source is TikHub Xiaohongshu App V2 identity resolution,
using `/api/v1/xiaohongshu/app_v2/get_user_info` under a pinned strict adapter
schema identified by
`TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1`. The canonical value is the
resolver-returned opaque `userid`; it is not the nickname, `red_id`, raw share
text, profile URL, or an unchecked imported account ID.

The resolver flow is:

1. load the internal XHS PlatformAccount and any current `VERIFIED_CURRENT`
   `xiaohongshu.userid` binding;
2. if a policy-accepted binding exists, select it without re-resolving merely to
   obtain a refresh credential;
3. otherwise require a bounded bootstrap input accepted by the exact identity-
   resolver schema: the existing allowlisted normalized profile URL may be used
   only if that schema proves it is accepted; a provider share URL/text may be
   supplied ephemerally but must never be persisted or reused after resolution;
4. if no such proven bootstrap input is available, stop without a provider call;
5. call the App V2 identity resolver only when feature/config/governance gates
   permit it;
6. require HTTP and provider semantic success under the pinned identity schema;
7. extract the TikHub-returned `userid` as an opaque value;
8. if the generic stored account ID is present, require exact agreement with the
   returned `userid`; a mismatch is `IDENTITY_CONFLICT` and must not overwrite or
   merge either identity;
9. require the allowlisted resolver source/version contract itself to certify
   verification, assign `verified_at` on the server, and create immutable bounded
   verification evidence; caller self-assertion is insufficient;
10. atomically bind the returned `userid` as `xiaohongshu.userid` under section
    11; and
11. only the resulting current `VERIFIED_CURRENT`, policy-accepted binding and
    verification evidence may support a trusted activity observation.

Canonical identity, bootstrap input, and activity-request input remain separate.
The activity adapter may call `get_user_posted_notes` only through a request-
identity mapping that the exact adapter contract proves is derived from the
verified `xiaohongshu.userid`. Raw share URL/text is bootstrap-only, must be
discarded after identity resolution, and cannot be retained or reused as a
refresh credential. The existing normalized profile URL remains display/cross-
check data and is not canonical identity. If the activity endpoint cannot be
called from the verified identity without a retained share credential, trusted
XHS activity remains disabled rather than weakening the authoritative contract.

No profile/share URL or share text is copied into provider identity lineage,
request provenance, logs, traces, Audit, or Content Activity persistence.

Fail-closed outcomes include:

- no current `VERIFIED_CURRENT` binding and no schema-proven ephemeral bootstrap
  input:
  `IDENTITY_UNRESOLVED`;
- malformed/unrecognized resolver schema or semantic failure: provider or
  untrusted outcome as narrowly mapped;
- returned `userid` missing or invalid: `IDENTITY_UNRESOLVED` or
  `RESULT_UNTRUSTED`;
- returned `userid` conflicts with the existing account identity or any other
  canonical account: `IDENTITY_CONFLICT`; and
- an unverified/import-only account ID without resolver confirmation: unusable for
  trusted Content Activity.

## 11. ProviderAccountIdentity contract

D1A must add one platform-neutral identity-lineage relation conceptually named:

```text
ProviderAccountIdentity
```

It is separate from `PlatformAccountSourceIdentity` and is never a raw TikHub
response table.

### 11.1 Binding and verification records

`ProviderAccountIdentity` stores only verification-complete canonical binding
episodes. An unverified resolver candidate or failed resolution attempt must not
reserve a globally unique external identity in this relation; it remains
sanitized attempt evidence outside the canonical binding table.

Minimum binding fields are:

- internal immutable `id`;
- `platform_account_id`;
- canonical `platform`;
- closed namespaced `namespace` such as `xiaohongshu.userid` or
  `douyin.sec_uid`;
- exact opaque external identity value;
- resolver/identity source registry key;
- resolver contract/version;
- closed `verification_state` for the binding lifecycle;
- immutable `resolved_at` and initial `verified_at` in UTC;
- immutable, secret-free, bounded initial `provenance_ref`;
- optimistic `lock_version`;
- nullable `superseded_at`/`revoked_at` as applicable; and
- ordinary created/updated timestamps.

Closed `verification_state` values are:

```text
VERIFIED_CURRENT
SUPERSEDED
REVOKED
```

Only `VERIFIED_CURRENT` plus policy acceptance is usable for a trusted Content
Activity observation.

Same-value reverification is append-only in a supporting neutral relation such
as `ProviderAccountIdentityVerification`. Each event records its immutable
identity-binding reference, resolver/source and contract version, server-assigned
`verified_at`, bounded secret-free evidence/provenance reference, policy outcome,
and idempotency key. A Content Activity observation references both the exact
binding and the exact accepted verification event used for its decision. This
prevents a later reverification from changing the evidence attached to an older
observation.

### 11.2 Exact constraints and indexes

The physical model must enforce:

1. a composite FK `(platform_account_id, platform)` to
   `InfluencerPlatformAccount(id, platform)`;
2. global uniqueness of `(platform, namespace, opaque_external_identity)` so one
   namespaced external identity has one permanent canonical owner and cannot be
   silently assigned to another account, including after terminal state;
3. a partial unique index on `(platform_account_id, namespace)` where
   `verification_state` is
   `VERIFIED_CURRENT`, permitting at most one current verified binding per account
   namespace;
4. an index on `(platform_account_id, namespace, verification_state)` for current
   binding reads;
5. non-blank bounded namespace, identity source/version, opaque identity, and
   provenance reference rules;
6. a platform/namespace registry invariant (`xiaohongshu.userid` belongs to XHS;
   `douyin.sec_uid` belongs to Douyin); and
7. `verification_state`/timestamp checks:
   - `VERIFIED_CURRENT` requires `verified_at` and has neither terminal time;
   - `SUPERSEDED` requires `superseded_at` and forbids `revoked_at`;
   - `REVOKED` requires `revoked_at` and forbids `superseded_at`;
   - `resolved_at <= verified_at <=` the applicable terminal time;
   - all decision-bearing times are server-assigned UTC values no later than the
     authoritative transaction clock; and
8. verification-event uniqueness on
   `(identity_source, resolver_contract_version, idempotency_key)`, plus an index
   on `(provider_account_identity_id, verified_at)`.

Exact opaque identity comparison is used. No case folding, URL derivation,
nickname lookup, handle match, or fuzzy normalization is permitted.

### 11.3 Lifecycle and conflict handling

Identity values are not destructively overwritten:

- exact same-value replay with the same scoped idempotency key returns the same
  binding/event; new accepted evidence appends a new immutable verification event
  and never edits original resolution/verification provenance;
- an automated resolver may create an initial binding or reverify the exact
  current value; a different value is `IDENTITY_CONFLICT` unless an explicitly
  authorized, audited correction workflow approves replacement;
- replacement requires caller-supplied
  `expected_current_identity_id + expected_current_lock_version`, locks the
  account and current binding, and fails stale expectations closed;
- inside one transaction, approved replacement first marks and flushes the old
  current row `SUPERSEDED`, then inserts the new fully verified current row and
  verification event; any failure rolls the whole transaction back;
- historical observations retain their original `identity_binding_ref` and
  `identity_verification_ref`;
- revoked identities remain as provenance and cannot be selected as current;
- equal external identity presented for any different canonical account is
  `IDENTITY_CONFLICT` and fails closed; and
- conflicts are not auto-selected, auto-merged, auto-reassigned, or overwritten.

`SUPERSEDED` and `REVOKED` are terminal. V1 does not reactivate a terminal value
or reassign its permanent owner. Returning to a prior value, canonical-account
merge, or ownership correction requires a separately frozen audited policy; the
current seam fails closed.

Human/admin binding, supersession, revocation, and conflict rejection require
closed Audit actions. Automated resolver activity uses bounded operational
provenance consistent with repository policy. Neither path stores credentials,
raw provider bodies, URLs, or unbounded error messages.

## 12. Douyin canonical identity bridge

D1B adds the minimum Influencer-domain bridge:

```text
Internal InfluencerPlatformAccount
  -> ProviderAccountIdentity(namespace = douyin.sec_uid)
  -> verified opaque sec_uid
```

The automated initial-bind/same-value-reverify seam is conceptually:

```text
bind_verified_canonical_identity(
  platform_account_id,
  platform,
  namespace,
  opaque_external_identity,
  identity_source,
  resolver_contract_version,
  verification_outcome = POLICY_VERIFIED,
  verification_evidence_ref,
  idempotency_key,
  expected_current_identity_id = null,
  expected_current_lock_version = null,
)
```

The idempotency key is scoped by
`(identity_source, resolver_contract_version)` and is bounded, opaque, and
secret-free. The seam accepts an already resolved, policy-verified identity
result from an allowlisted resolver contract. It validates platform, namespace,
exact ownership, verification outcome/evidence, idempotency, expected-current
state, and conflict constraints. The service assigns `resolved_at` and
`verified_at` from its authoritative server/transaction clock; caller timestamps
and caller self-asserted verification cannot establish trust. A different-value
replacement uses the separate audited CAS correction path in section 11.3.

Both expected-current values must be null for an initial bind and both must match
the locked row for same-value reverification; a half-present or stale pair fails
closed.

The seam does not perform enrollment, leases, exchange, credentials, execution
fencing, or activity collection.

A future internal Douyin protocol capability may act as `IDENTITY_RESOLVER` and
call this seam. TikHub remains the activity provider and does not become canonical
identity authority merely because its activity endpoint accepts `sec_user_id`.

If D1B must create a new Douyin PlatformAccount rather than bind an existing one,
it must also define an honest account-creation origin/service path. The current
non-null account `source` is import-oriented; D1B must not label a protocol-created
account `generic`, `manual`, or `huitun` merely to satisfy the column. This is a
D1B implementation prerequisite, not authorization to weaken current semantics.

## 13. Platform.DOUYIN model impact

D1B, not D1A, must account for all of these surfaces:

The audited impact is repository-backed, not inferred from adding one enum value:

- import adapters are XHS-fixed at
  `packages/backend_core/src/backend_core/imports/adapters.py:62-63`, and the URL
  parser is XHS-specific at `imports/normalizers.py:46-73`;
- screening contracts accept `Platform` values at
  `imports/preview_domain.py:53-64`, so enum expansion can widen validation unless
  an enabled-platform registry remains explicit;
- public account/query contracts are in
  `influencers/schemas.py:105-151,169-177`, and the HTTP query allowlist begins at
  `apps/api/app/http/influencers.py:37`;
- account-bound metrics use the neutral PlatformAccount owner at
  `influencers/models.py:237-250`;
- Seller/Candidate platform facts and Buyer XHS-only behavior are at
  `growth/targeting.py:169-180,393-408,799-806`, with set-based fact loading and
  XHS classification at `growth/repository.py:503-515,687-700`; and
- executable assumptions are asserted across
  `apps/api/tests/test_bulk_import_http.py:2234`,
  `packages/backend_core/tests/test_targeting.py`,
  `apps/web/src/features/imports/types.ts:192`, and
  `apps/web/src/features/refresh-queues/types.ts:18`.

### 13.1 Database and Python models

- add `Platform.DOUYIN` to the Python enum;
- extend PostgreSQL `platform_enum` through a new Alembic revision after current
  head; never edit historical migration `0003`;
- add/validate account creation and identity-binding constraints;
- register new neutral models with Alembic metadata; and
- test migration upgrade, uniqueness, concurrency, and practical downgrade
  limitations of PostgreSQL enum expansion.

### 13.2 Import and validation

- existing Huitun/Generic adapters and XHS URL normalizer remain XHS-only;
- adding the enum must not silently make Douyin import/screening accepted;
- any schema that automatically accepts all `Platform` enum values needs an
  explicit enabled-platform registry; and
- nickname, Douyin URL, handle, or fuzzy matching remains forbidden.

### 13.3 API, UI, metrics, and targeting

- API serialization may expose a canonical Douyin account only after the new
  model path exists;
- current Web display strings do not constitute backend identity capability;
- account-bound metrics need no platform-specific table duplication;
- Seller platform filters may handle Douyin only with correct account facts;
- existing Buyer classification remains XHS-only unless separately authorized;
- Content Activity missing/untrusted facts remain targeting `UNKNOWN`; and
- adding a Douyin account must not enable Douyin outreach, which remains guarded
  as unavailable.

## 14. Phase 3B boundary

The Phase 3B post-lock foundation remains frozen and separate. This contract does
not authorize:

- cherry-picking or copying Phase 3B code;
- reusing enrollment, machine exchange, lease, execution, credential, or secret
  contracts;
- reading connector tables from Content Activity; or
- combining Connector delivery state with provider activity state.

The only permitted future seam is a secret-free resolved identity result entering
the Influencer-domain binding service. Content Activity depends on the resulting
canonical binding, not on how the resolver obtained it.

```text
PHASE_3B_BOUNDARY_PRESERVED = YES
```

## 15. Shared canonical persistence requirement

D1A must create platform-neutral persistence from day one. Core tables or model
equivalents are conceptually:

```text
ProviderAccountIdentity
ProviderAccountIdentityVerification
ContentActivityObservation
ContentActivityProjection
```

The projection must separately reference:

- newest trusted observation under the frozen trust/freshness policy; and
- latest attempt observation, including failure/incomplete/untrusted status.

Core table names must not be prefixed with Xiaohongshu or Douyin. Provider raw
schemas remain inside adapters. No `xiaohongshu_content_activity_*` followed by a
future duplicate `douyin_content_activity_*` design is permitted.

The immutable observation carries platform account ownership, activity semantics,
three orthogonal status axes, identity/activity lineage, provider/product/adapter/
capability versions, trusted result evidence, UTC provenance, scan summary, and
sanitized diagnostics defined by the authoritative contract.

The trusted projection may move backward or clear on a newer trusted result. A
failed/latest attempt updates only latest-attempt state and preserves the last
trusted projection. `inactive_days` is absent from persistence.

D1A may contain only XHS rows. Later `Platform.DOUYIN` rows reuse the same identity,
observation, projection, repository, API, Worker, and Candidate paths.

```text
SHARED_CANONICAL_PERSISTENCE_DESIGN = APPROVED
```

## 16. Migration impact

No migration is created by this documentation task.

Expected future migration split:

### D1A migration

- add platform-neutral `ProviderAccountIdentity` and immutable verification
  events;
- add platform-neutral immutable Content Activity observation history;
- add platform-neutral trusted/latest-attempt projection;
- add enum/check/index/FK constraints required by the frozen states;
- add set-based list/filter/Candidate indexes; and
- keep actual platform rows XHS-only under the existing platform enum.

### D1B migration

- add `douyin` to PostgreSQL `platform_enum` and Python `Platform`;
- add any honest canonical Douyin PlatformAccount creation-origin contract;
- reuse, not duplicate, D1A provider identity lineage; and
- add bridge-specific Audit/idempotency/uniqueness support where not already
  supplied by D1A.

### D1C migration

No provider-specific core table should be required. A migration is justified only
for a genuinely new neutral contract, not for TikHub raw response fields.

## 17. Implementation sequencing

### 17.1 D1A — unified persistence and XHS value

D1A is authorized to start after this prerequisite contract passes:

- shared ProviderAccountIdentity, observation, and projection persistence;
- XHS `userid` resolution/intake under section 10;
- strict XHS App V2 adapter with sanitized schema fixtures;
- one-response exact policy and fail-closed multi-page behavior;
- bounded refresh orchestration;
- Library API/UI fields and timestamp filters;
- Influencer detail projection;
- account-bound Candidate XHS activity constraint; and
- tests for identity, exactness, UNKNOWN, data freshness separation, security,
  PostgreSQL persistence, set-based reads, API, Worker, Candidate, and Web.

D1A must not access production, auto-backfill production, or enable provider use
without configuration and governance gates.

### 17.2 D1B — Douyin identity foundation

D1B is a separate authorized integration:

- `Platform.DOUYIN` model/migration expansion;
- canonical Douyin PlatformAccount creation/binding rules;
- verified `douyin.sec_uid` ProviderAccountIdentity lifecycle;
- resolver intake service, idempotency, Audit, and concurrency tests;
- explicit XHS-only gates preserved in Import/Buyer classification; and
- no TikHub Douyin activity call.

D1B closes only the identity prerequisite. It does not close Douyin publication-
universe or multi-page coherence prerequisites.

### 17.3 D1C — Douyin provider activity

D1C starts only after:

1. D1B supplies a verified current `douyin.sec_uid` binding;
2. complete Douyin publication-universe evidence is accepted;
3. strict sanitized App V3 schema/visibility evidence is versioned;
4. terminal and coherence rules are accepted; and
5. a new explicit capability policy version opens trusted acceptance.

D1C then implements the App V3 adapter through the same persistence, refresh,
Library, and Candidate contracts. It must not create provider-specific core tables
or frontend N+1 hydration.

## 18. Capability versioning and future expansion

V1 is immutable after acceptance. These changes require a new explicit policy
revision/version:

- new endpoint or provider product version;
- response schema or semantic-success change;
- new publication/content type;
- visibility/access semantic change;
- pagination or cursor semantic change;
- enabling XHS multi-response trusted traversal;
- widening the XHS closed content universe;
- enabling Douyin trusted acceptance;
- changing a completion predicate or snapshot validation protocol; or
- any expansion of exact/trusted coverage.

Unknown endpoint version, schema, type, visibility semantic, or pagination
semantic defaults to fail closed. New code must not reinterpret old observations
under a widened rule.

## 19. Security and data minimization

This prerequisite contract preserves the authoritative security boundary:

- no token, Authorization header, cookie, credential, raw request URL/body, raw
  provider response, share text, signed/cache/media/subtitle URL, media bytes, or
  giant/unbounded provider error is persisted, logged, audited, or exposed;
- identity provenance is opaque, bounded, and secret-free;
- a raw provider share URL/text is ephemeral identity-bootstrap input only and is
  discarded after resolution; the existing normalized profile URL remains
  display/cross-check data and neither is copied into provider identity lineage;
- adapters request only fields necessary for identity and activity facts; and
- no media/detail/download/preflight request is authorized.

## 20. Production provider-governance gate

Technical functional proof and this capability policy do not approve TikHub
commercial production use. Production enablement remains blocked on explicit
approval of:

- pricing;
- licensing and terms;
- production data-use rights;
- quotas and finite page/item/deadline/concurrency values;
- support and SLA;
- secret provisioning/rotation; and
- implementation and release evidence.

The XHS v1 one-response semantic budget is an exactness boundary, not a quota or
commercial claim.

## 21. Remaining blockers

### XHS D1A enablement blockers

- strict sanitized identity and activity response schemas/fixtures must be pinned;
- exact provider semantic-success values and raw field paths must be proven in
  those fixtures, not guessed;
- finite item/deadline/concurrency values and activity observation freshness must
  be configured and tested; and
- provider-governance and complete implementation/release gates remain required
  before production enablement.

These do not block beginning XHS-first implementation; they block trusted runtime
enablement until satisfied.

### XHS multi-page blocker

- no coherent provider snapshot or approved re-read protocol is frozen.

### Douyin blockers

- no `Platform.DOUYIN` or canonical Douyin PlatformAccount path exists;
- no verified namespaced `douyin.sec_uid` binding exists;
- the App V3 endpoint has only works/video-scoped evidence, not a complete
  publication universe;
- no multi-response coherence rule is frozen; and
- trusted acceptance remains disabled until a new capability policy version.

## 22. Documentation acceptance and final verdict

This contract freezes:

- the original blocker evidence;
- XHS-first authorization;
- strict single-response XHS trust eligibility;
- fail-closed XHS multi-page handling;
- disabled Douyin v1 provider calls/trust;
- platform-neutral identity and persistence;
- exact identity uniqueness and supersession semantics;
- D1A/D1B/D1C sequencing;
- Phase 3B separation; and
- capability-policy versioning and production-governance boundaries.

Final verdict:

```text
CONTENT_ACTIVITY_IMPLEMENTATION_PREREQUISITES_PASS
```

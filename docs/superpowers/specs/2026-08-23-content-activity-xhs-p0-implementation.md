# Content Activity — Xiaohongshu-first P0 implementation note

## Status and scope

This note materializes the already-frozen D1A contracts for an implementation
review.  It does not amend either frozen contract.  D1A adds platform-neutral
identity, observation, and projection persistence, then enables only the
strict Xiaohongshu App V2 path.  `DOUYIN_CONTENT_ACTIVITY` remains blocked:
there is no `Platform.DOUYIN`, `douyin.sec_uid`, Douyin request, or trusted
Douyin observation in this change.

## Account-bound model

Content Activity is a fact about one `InfluencerPlatformAccount`, never an
implicitly combined creator-level fact.  The neutral persistence has four
roles:

1. `ProviderAccountIdentity` holds a namespaced, opaque, verified identity
   binding episode; its append-only verification events preserve revalidation
   evidence.
2. `ContentActivityObservation` is immutable, one per provider attempt, and
   records the exact identity binding and verification event used by that
   decision.
3. `ContentActivityProjection` has two independent pointers: the most recent
   attempt and the most recent trusted-current observation.  An unsuccessful
   attempt never clears trusted facts.
4. `ContentActivityRefreshRequest` is a bounded, PostgreSQL-authoritative
   delivery record.  It is not a Refresh Queue and does not place provider
   inputs in Celery messages.

The identity uniqueness and lifecycle rules are fail-closed: an external
identity is permanently owned by one `(platform, namespace)` canonical
account; a current binding is singular per account/namespace; supersession is
an explicit compare-and-swap transition; conflicts never overwrite, merge, or
select a winner.  `xiaohongshu.userid` is the only namespace enabled in D1A.
When a current binding is explicitly superseded, immutable observations remain
as history and latest-attempt diagnostics remain visible, but the account's
trusted-current projection is cleared in the same transaction.  A replacement
userid therefore cannot inherit the former userid's publication fact; it needs
its own later trusted observation.

Equal-`observed_at` material conflicts are quarantined as an explicit
untrusted latest attempt. They preserve the prior trusted observation as
history, but cannot silently leave its pointer looking current: list filters
and Candidate targeting fail closed until a later ordered trusted observation
exists.

## Strict XHS V1 adapter

The adapter registry fixes these contract IDs:

```text
TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1
TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1
TIKHUB_XHS_APP_V2_GET_USER_POSTED_NOTES_RESPONSE_SCHEMA_V1
TIKHUB_XHS_APP_V2_CURRENT_PUBLIC_VISIBILITY_POLICY_V1
```

Each registry entry has a source-controlled schema artifact, positive and
negative sanitized fixture, and SHA-256 digest.  The public TikHub App V2
documentation establishes the request endpoint and the `data.data.notes`
cursor container; the fixture pins the exact v1 envelope and accepted field
paths.  Runtime parses only that allowlist, records no raw provider body, URL,
token, signed media URL, or unbounded provider error.

The identity resolver is the only source of a canonical `xiaohongshu.userid`.
An existing generic account ID/profile URL is bootstrap or cross-check data,
not a canonical provider identity.  An ephemeral caller-supplied resolver
input is never stored or sent through the task queue.  Without a current
verified binding or a schema-proven ephemeral resolver input, the account is
`IDENTITY_UNRESOLVED` and cannot receive trusted activity.

The activity request uses the verified `userid` as `user_id`, empty cursor,
and exactly one response.  A trusted current-public result requires every
frozen V1 gate, including semantic success, strict envelope, exact identity,
recognized visibility, closed `normal`/`video` types, valid timestamps, no
future/duplicate contradiction, and explicit boolean `has_more=false`.
`last_publication_at` is the maximum eligible `create_time`, not response
order.  A first `has_more=true` writes
`RESULT_INCOMPLETE / INCOMPLETE / UNDETERMINED` and makes no page-two request.
Unknown schema, type, visibility, timestamp, or completeness evidence writes
an untrusted/unknown attempt.  An empty response can become
`NO_PUBLIC_CONTENT` only through the same complete trusted path.

## Refresh and governance

An admin-only CSRF-protected identity-resolution entry may use an ephemeral
bootstrap input.  Account and bounded-batch refresh creation only accepts
platform-account identifiers; the worker fetches an already-verified identity
from PostgreSQL and sends ID-only messages on the existing `analytics` queue.
The durable request/lease/reconcile protocol uses bounded retries, provider
timeouts, concurrency and response-size limits.  There is no startup bulk
refresh and no Celery automatic retry.

The feature is off by default.  Technical schema capability does not approve
TikHub production pricing, rights, quota, support, or SLA.  The initial
operational backfill remains a design only: 10 accounts, then 50, then 100,
observing success, unknown, rate-limit, and latency distributions before any
larger explicitly-governed rollout.

## Read and targeting semantics

Influencer detail exposes activity on its exact XHS platform account.  List
filters use account-correlated trusted projection predicates and timestamp
cutoffs for active-within-7 and inactive-30/60/90/180; they do not persist
`inactive_days`, aggregate accounts, or treat `NO_PUBLIC_CONTENT` as infinite
inactivity.  A multi-account list row remains explicitly ambiguous until an
account is selected, rather than borrowing `platform_accounts[0]`.
When a later incomplete, failed, or quarantined attempt exists, the retained
trusted value is shown only as last-known; it is excluded from current activity
filters and never contributes a sales-facing inactivity inference.

Candidate targeting receives an optional, strict, versioned Content Activity
constraint separate from existing data freshness.  It evaluates with the
captured Candidate Run `as_of`: a current trusted qualifying timestamp can
match or not-match, while untrusted, incomplete, stale, missing, failed, and
no-public-content cases are `UNKNOWN`.  Influencer reads hydrate projections
set-wise; Candidate materialization uses set-wise immutable-observation
history rather than mutable projections, and neither has per-row activity
queries.  Candidate materialization reconstructs both latest attempt and trusted-current evidence
from immutable observations at `observed_at <= run.as_of`, including temporal
identity validity.  A provider observation or identity replacement that occurs
after reservation cannot change an already-reserved run's activity result.

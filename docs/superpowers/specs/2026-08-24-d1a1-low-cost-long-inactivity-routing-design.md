# D1A.1 — Low-cost long-inactivity routing

## Decision

D1A.1 adds a cost-aware `LONG_INACTIVITY` decision path without changing the
frozen `CURRENT_PUBLIC_VISIBLE` Content Activity contract.  It consumes the
two existing Huitun (Grey Dolphin) aggregate metrics only:

- `notes_7d` (`近7天笔记数`)
- `notes_60d` (`近60天笔记数`)

Their existing Huitun source-observation/update provenance establishes whether
the aggregate snapshot is fresh enough to use.  Neither value creates a
publication timestamp, an `inactive_days` value, or trusted Content Activity
evidence.

## Evidence streams

The implementation keeps two independent streams:

1. Existing fresh, trusted `CURRENT_PUBLIC_VISIBLE` Content Activity remains
   exact evidence and retains its present semantics.
2. Grey Dolphin is a coarse, business-usable `LONG_INACTIVITY` fact.  It is
   derived from one coherent persisted Huitun metrics snapshot and its
   provenance, and is never written into the trusted Content Activity
   projection.

The smallest compatible design is a typed derived fact built from the existing
current Huitun metrics plus their source state/provenance, rather than a new
activity table.  This avoids duplicating imported source data while retaining
the source, observed time, precision, status, and the two original values in
the decision/audit result.  A migration is therefore not expected.

`GREY_DOLPHIN_ACTIVITY_FRESHNESS_DAYS` controls this source-specific TTL and
defaults to seven days.  It is intentionally separate from the trusted
Content Activity TTL and is a configurable operational policy, not a permanent
business assertion.

## Grey Dolphin interpretation

Grey Dolphin evidence is usable only when both values are non-negative
integers from one coherent snapshot, provenance is present, the snapshot is
within a configurable Grey Dolphin TTL, and `notes_7d <= notes_60d`.
Otherwise it fails closed as `UNKNOWN`/invalid or stale evidence.

For usable evidence:

| Observation | `LONG_INACTIVITY >= 30` | `>= 60` | `>= 90` / `>= 180` |
| --- | --- | --- | --- |
| `notes_7d > 0` | `NOT_MATCH` | `NOT_MATCH` | `NOT_MATCH` |
| `notes_7d == 0`, `notes_60d == 0` | `MATCH` | `MATCH` | `UNKNOWN` |
| `notes_7d == 0`, `notes_60d > 0` | `UNKNOWN` | `NOT_MATCH` | `NOT_MATCH` |

The `notes_7d > 0` row proves recent activity and may cheaply exclude every
supported long-inactivity threshold.  The zero-60-day row is deliberately
described and displayed as approximate (`近60天无更新` / `约60天以上未更新`),
not as an exact duration.

## Routing and execution boundary

For each candidate, evaluation priority is:

1. fresh trusted Content Activity cache;
2. fresh coherent Grey Dolphin fact;
3. `UNKNOWN`;
4. only when explicitly requested by the execution planner, bounded provider
   enrichment of deterministic unresolved candidates.

Usable Grey Dolphin `MATCH` and `NOT_MATCH` decisions short-circuit TikHub.
Stale, missing, invalid, or insufficient Grey Dolphin evidence remains
fallback-eligible but causes no provider call by itself.

The new service/domain primitive accepts execution-only
`planned_assignable_target` and `max_provider_enrichment` parameters.  They
are not part of the immutable seller rule.  It evaluates the supplied
deterministically ordered candidates cheaply first and considers unresolved
activity candidates only while the full Candidate policy still needs eligible
candidates and provider budget remains.  A `LONG_INACTIVITY` match alone is
not assignable: the upper execution planner must supply or maintain the
authoritative complete-policy eligibility count when D1A.1 does not own that
evaluation.  For example, a target of 30 with 18 fully eligible candidates
needs 12 additional *fully eligible* candidates; D1A.1 must not stop merely
because it has found 12 activity matches that fail another Candidate
criterion.  It stops immediately when the upper planner reports the target
met or its own provider budget is met. Terminal target and budget flags are
derived independently from the final full-policy and provider-attempt counts,
so the last candidate may set either or both.

The operational surface is an opt-in Candidate Run request containing only
`planned_assignable_target` and `max_provider_enrichment`. Ordinary Candidate
Runs do not enrich unknowns. An explicit run is delivered through the existing
API → analytics worker → CandidatePoolService path; the service invokes the
planner, while each eligible XHS resolution uses the existing governed,
leased Content Activity request and global provider-call slot. The API carries
no provider result and the Web UI creates no provider trigger.

Manual refresh remains separately explicit and uses the same provider
governance plus lease/locking protections. There is no page-open trigger,
historical backfill, nightly scan, full-pool refresh, or automatic enrichment
of every unknown candidate.

## Observability and Candidate behavior

The returned planning/audit result reports candidates evaluated, cache and
Grey Dolphin resolutions, unresolved cases, avoided and attempted provider
calls, and whether the target or budget stopped enrichment.  It contains no
credentials, raw provider payloads, or signed URLs.

Candidate `MATCH`/`NOT_MATCH`/`UNKNOWN` precedence remains unchanged.
Ambiguous, stale, invalid, and provider-failure states are always `UNKNOWN`.
`ACTIVITY_DROP` is explicitly out of scope and is represented neither by the
two aggregates nor by the new long-inactivity boundary.

## Tests and gates

Targeted unit and service tests will cover every required Grey Dolphin
resolution, invalid/stale fallback, cache and no-call cases, bounded
deterministic planning, stop conditions, manual bounds, preserved provider
failure behavior, and counters.  Since this design avoids persistence changes,
no new migration is planned; affected Content Activity, Candidate, API/Worker,
and quality gates will be selected from the repository's existing commands.

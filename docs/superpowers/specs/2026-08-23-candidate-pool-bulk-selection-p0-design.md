# Candidate Pool Bulk Selection P0 Design

## Scope and invariants

This change improves bulk selection in the existing Candidate Result view only.
It does not change targeting, materialization, Campaign membership uniqueness,
RBAC, production deployment, or database schema.  It requires no migration.

Selection is scoped to one immutable `(candidate_pool_id, run_id)` pair.  A
different run starts with an empty selection.  Filter, cursor page, and page
size changes stay in the same scope and therefore keep selection state.

The existing Candidate-to-Campaign contract remains authoritative:

- Explicit selection may contain persisted `MATCH` and intentionally selected
  `UNKNOWN` members.
- `NOT_MATCH` remains count-only and cannot enter a Campaign.
- A Campaign cannot receive two platform accounts for one influencer.  This is
  fail-closed; the system never chooses an account automatically.

## Web selection contract

The reusable selection domain utility has two explicit modes:

- `EXPLICIT`: a bounded map of selected member metadata keyed by member ID.
  The metadata keeps the member result and influencer ID needed to preserve the
  existing UNKNOWN warning and multi-account guard.
- `ALL_MATCH`: the completed run's immutable `match_count` plus a bounded set
  of excluded MATCH member IDs.  It never materializes all MATCH IDs in the
  browser.

The utility exposes scope reset, single-row toggling, current-page select and
deselect, clear-all, select-all-MATCH, selected-count, selected-state, and
request-payload derivation.  It is independent of the Campaign modal so a
future Sales assignment flow can consume the same contract.

Current-page selection uses only rows displayed in the current cursor page.
The header checkbox reflects none / some / all selected displayed eligible
rows.  In `ALL_MATCH`, deselecting a page row adds it to exclusions and
selecting it again removes that exclusion.  Choosing ALL_MATCH replaces any
EXPLICIT selection because the server contract intentionally has no mixed
MATCH-plus-UNKNOWN mode.

## Cursor pagination

Candidate Result retains bare-UUID keyset pagination.  The Web client tracks a
cursor chain per visible filter and page-size and renders exactly one returned
page at a time.  Previous pages reuse their stored cursor; a next page uses the
server `next_cursor`.  Changing result filter or page size safely restarts the
cursor chain at page one, without changing selection.

Only the Candidate Member list endpoint changes its bounded maximum from 100
to 200.  Its query remains `id ASC`, `id > cursor`, `limit + 1`; offset
pagination is not introduced.  Supported Web page sizes are exactly 20, 50,
100, and 200.

## Campaign add contract

The existing request remains unchanged:

```json
{ "run_id": "...", "member_ids": ["..."] }
```

The compatible ALL_MATCH alternative is:

```json
{
  "run_id": "...",
  "selection_mode": "ALL_MATCH",
  "excluded_member_ids": ["..."]
}
```

The server accepts exactly one of these forms.  `ALL_MATCH` has no arbitrary
filter input: it means only persisted MATCH rows of the supplied completed run.
Every exclusion must itself be a MATCH member of that exact Department-scoped
run.  Unknown, wrong-run, cross-department, duplicate, and nonexistent
exclusions fail closed.

The explicit request hash is unchanged.  ALL_MATCH hashes its mode and
canonically sorted exclusions, retaining the same idempotency scope and replay
semantics.

## Bounded service processing

After existing authorization, CSRF, idempotency, Department scope, Campaign
lock, and completed-run checks, ALL_MATCH processing:

1. validates exclusions with bounded set queries;
2. preflights the existing multi-account-per-influencer conflict for
   `MATCH - exclusions`;
3. reads source members by UUID keyset in fixed chunks;
4. resolves existing Campaign members and writes inserts/restores in fixed
   chunks, aggregating result counts;
5. records one idempotency result and one audit record for the successful
   overall request.

There is no client enumeration of MATCH rows, no unbounded source-row list,
and no per-member query.  If the preflight finds an influencer with multiple
selected accounts, the API returns the existing ambiguity code plus
non-sensitive actionable conflict information.  The Web view preserves the
selection and displays the conflict members so the user can exclude one
account and retry.

The ambiguity preflight uses one ordered async stream, read in fixed-size
partitions. On PostgreSQL/psycopg this is a server-side cursor. It retains only
the current account pair in application memory, does not issue per-member
lookups, and does not require a schema or uniqueness-contract change.

## Verification

Web tests cover the selection domain state, current-page checkbox states,
cursor-page persistence, page-size/filter behavior, run reset, EXPLICIT and
ALL_MATCH payloads, success feedback, disabled states, and conflict feedback.

API and service tests cover the legacy explicit request byte-for-byte,
ALL_MATCH resolution and exclusions, invalid exclusions, Department/auth/CSRF/
idempotency guards, duplicate/rejoin/audit behavior, zero MATCH, page size 200,
and bounded chunk behavior. An opt-in isolated PostgreSQL 16 gate covers a
multi-chunk ALL_MATCH request, exclusions, replay, audit/idempotency, and
second-chunk rollback. The targeted quality gates include Web tests,
lint/typecheck, API and service tests, ruff, black, mypy, and `git diff --check`.

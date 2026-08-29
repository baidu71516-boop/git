# Rules Management Release A.2 production release prep

## Release boundary

- Release A production baseline: `659c0f83f5a6613d92acd46c8f3be21d7b5d09d4`.
- Frozen A2 code candidate: `47156f01db9e383de7dcc82ec481ee1706bcccee`.
- The baseline is an ancestor of the candidate. The full Git interval also contains
  the accepted Web release-gate tooling and manifest compatibility work between
  Release A and A2. The A2 feature payload itself is the single tip commit
  `47156f01db9e383de7dcc82ec481ee1706bcccee`.
- A2 changes Candidate Pool API/Web behavior and the shared Candidate Pool service
  contract. It does not change a migration, database model, Worker task, Scheduler
  task, or Nginx configuration.
- Targeted PostgreSQL integration for the exact candidate passed: 4 passed,
  0 failed (`RULES_A2_POSTGRES_GATE_PASS`).

## Migration

**NO MIGRATION.** The candidate adds no file under
`infrastructure/migrations/versions`, changes no persisted model, and retains the
existing Alembic head `0008_content_activity_p0`. Do not run an Alembic upgrade or
restore/modify the database for this release.

## Services and shortest deployment order

Deploy only `api` and `web`. Do not rebuild or restart `worker`, `scheduler`,
`postgres`, `redis`, or `nginx` for A2.

The Worker remains compatible because it already materializes every durable run
from `CandidatePoolRun.policy_id`; neither its task code nor
`CandidatePoolService.materialize_run()` changed in A2. Scheduler has no A2 code
path.

With the exact-candidate PostgreSQL gate passed:

1. Keep `/home/ubuntu/darensystem/releases/release-a-659c0f83f5a6` intact. Stage
   the exact candidate in a new immutable directory such as
   `/home/ubuntu/darensystem/releases/rules-a2-47156f01db9e`. Verify exact HEAD,
   clean status, sanitized production configuration, and the normal verified
   backup before changing a service.
2. In the A2 directory, build only `api` and `web` with the production Compose
   files.
3. Replace only `api` with `--no-deps`; wait for its container health and the
   existing loopback `/health/live` route to pass. Stop here on failure.
4. Replace only `web` with `--no-deps`; wait for its container health and the
   authenticated Candidate Pool route to load through the existing Nginx path.
5. Run the targeted smoke below for 5-10 minutes. No Nginx reload is required.

API precedes Web because the new Web client uses the A2 run request shapes. Keeping
dependencies out of each Compose update prevents an unrelated restart.

## Rollback

- Code target: return to
  `659c0f83f5a6613d92acd46c8f3be21d7b5d09d4` from the retained
  `/home/ubuntu/darensystem/releases/release-a-659c0f83f5a6` directory; do not
  reset or overwrite the A2 directory.
- Roll back `web` first, then `api`, each with `--no-deps`. This removes the A2 UI
  before removing the A2 API request contract. Recheck container health and the
  loopback/public health route after each replacement.
- Database: no schema rollback, dump restore, or row deletion. A2 writes ordinary
  existing Candidate Pool, policy, run, member, and audit records that the
  baseline schema already supports. Any later data correction is a separate,
  explicitly approved operation.

Stop instead of continuing when any of the following is true:

- the exact-candidate targeted PostgreSQL gate is absent, stale, or not PASS;
- release directory HEAD, tree cleanliness, artifact identity, production
  configuration, or backup verification does not match the approved evidence;
- the refreshed Web manifest is not reviewed and represented in the final release
  evidence;
- API health fails or produces new Candidate Pool 5xx errors; do not deploy Web;
- a run stays `PENDING`/`RUNNING`, Worker delivery fails, or counts/evidence are
  inconsistent;
- an old policy definition/hash changes, a historical rerun does not retain its
  requested `policy_id`, or a stale CAS request creates a policy/run instead of
  returning `VERSION_CONFLICT`;
- existing Candidate Pool list/detail/current-run/member behavior regresses; or
- the targeted smoke cannot be completed with attributable IDs and timestamps.

## Five-to-ten-minute targeted smoke

Use a dedicated production smoke department/operator and timestamped Seller Pool.
Retain the resulting IDs as audit evidence; do not delete production rows as part
of smoke.

1. **Candidate Pool / Seller Rule Builder:** open Candidate Pool, create one
   `POTENTIAL_SELLER` pool with an inline `SELLER_V1` rule, and verify list/detail,
   owner, `current_policy_id`, and pool `version` agree with the create response.
2. **Immutable policy version:** record policy v1 ID, definition, and
   `canonical_hash`; perform one adjusted rerun; verify v1 is byte-for-byte
   unchanged, v2 is appended, and the pool pointer/version advances exactly once.
3. **Historical rerun:** submit a run with the v1 `policy_id`; verify the durable
   run retains v1 even though v2 is current, reaches `COMPLETED`, and exposes a
   coherent member page/count summary.
4. **CAS-adjusted rerun:** submit `base_policy_id`, `expected_pool_version`, and an
   adjusted Seller rule. Verify the policy append, pool pointer change, and
   `PENDING` run are atomic. Replay the same idempotency key and confirm the same
   run; submit the stale base/version with a new key and confirm
   `VERSION_CONFLICT` with no extra policy or run.
5. **Existing behavior:** on one pre-A2 Candidate Pool, verify list/detail and an
   empty-body current-policy run still work, the run is materialized by the
   existing Worker, member pagination/counts remain coherent, and no unrelated
   Candidate Pool record changes.

## Required release decision

The exact-candidate PostgreSQL evidence is verified as 4 passed, 0 failed. The
release-prep state is:

`RULES_A2_RELEASE_PREP_READY`

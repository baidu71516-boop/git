# Task 12C Predeploy Closure

Date: 2026-08-15 (Asia/Shanghai)

This document records the bounded Task 12C predeploy gates for release candidate
`phase-2-integration` at Git SHA `58cc5c1997765146962be4a5cb51d5c782a24766`.
It is not a deployment authorization and does not start Task 13.

## Refresh Return Browser Matrix

The matrix used the isolated Compose project `task12c_refresh` at
`http://127.0.0.1:18080`, with the real API and PostgreSQL database. No
`dev-ui-preview` fixture was used as a gate result, and the full 2,000-row gate
was not rerun.

| Scenario | Observed result |
| --- | --- |
| `stale_return` | Displayed as `回流数据已过期`; did not fulfill the row. |
| `unresolved` | Displayed as `无法确认`; Confirm completed without queue fulfillment. |
| `ERROR` | Displayed in the error summary; did not fulfill the queue item. |
| `MANUAL_REVIEW` | Displayed as `需人工处理`; did not fulfill the queue item. |
| duplicate non-owner | Duplicate non-owner remained pending and did not participate in reconciliation. |
| missing return | Remained `待回流`/pending. |
| atomic Confirm | Valid rows in the same Confirm remained eligible; invalid, stale, unresolved and pending rows were not partially fulfilled. |
| refetch authority | Post-Confirm queue detail refetch matched backend status, rather than the optimistic browser result. |
| freshness | Stale or pending/confirmation-required rows did not advance `last_huitun_observed_at`. |

Evidence queues:

- `93ed4dab-fcd4-49d2-b010-f3d48dbdc4fe`: `fulfilled_changed=2`,
  `stale_return=1`, pending/missing return `=1`, error `=1`, manual review
  `=2`; browser labels were Chinese and the final queue summary was backend-refetched.
- `ad06625e-9036-4d9d-985a-89ccd337f910`: unresolved `=1`, missing return
  `=1`, duplicate non-owner rows remained pending; Confirm returned successfully
  with no queue fulfillment for those rows.

The temporary Playwright snapshots were kept outside Git under `/tmp` and are
removed during cleanup.

## Database Backup

The tested dump is PostgreSQL custom format (`pg_dump -Fc`):

- File: `/tmp/task12c_release_20260815.dump`
- Size: `127513` bytes
- SHA-256: `258730c07dd58e69846bd1d1f74df6077c2ef6c52515606fd3a3e4901e967120`

Minimal backup command (credentials are injected by the operator and are not
written into this runbook):

```bash
export SOURCE_POSTGRES_CONTAINER=task12c_refresh-postgres-1
export PGUSER='<database user from secret store>'
export PGDATABASE='<database name from secret store>'
export DUMP_PATH=/tmp/task12c_release_$(date +%Y%m%d).dump
docker exec "$SOURCE_POSTGRES_CONTAINER" \
  pg_dump -U "$PGUSER" -d "$PGDATABASE" -Fc > "$DUMP_PATH"
sha256sum "$DUMP_PATH"
```

Prerequisites: Docker Engine, PostgreSQL 16 client/server compatibility,
`pg_dump`/`pg_restore`, an isolated test network and volume, and credentials
provided through the environment or a secret manager. Do not place passwords,
`APP_MASTER_KEY`, session tokens or provider keys in shell history, logs or this
document.

## Database Restore

The tested restore used a fresh PostgreSQL 16 container and volume:
`task12c_restore_postgres`, `task12c_restore_data`, network
`task12c_restore_net`, database `restoredb`, user `restoreuser`. PostgreSQL and
Redis were not exposed on a host port.

```bash
docker network create --internal task12c_restore_net
docker volume create task12c_restore_data

docker run -d --name task12c_restore_postgres \
  --network task12c_restore_net \
  -e POSTGRES_DB="$RESTORE_DB" \
  -e POSTGRES_USER="$RESTORE_USER" \
  -e POSTGRES_PASSWORD="$RESTORE_PASSWORD" \
  -v task12c_restore_data:/var/lib/postgresql/data \
  postgres:16-alpine

# Wait for pg_isready before restoring.
docker exec -i task12c_restore_postgres \
  pg_restore --no-owner --exit-on-error \
  -U "$RESTORE_USER" -d "$RESTORE_DB" \
  < "$DUMP_PATH"
```

Revision and smoke checks:

```bash
docker exec task12c_restore_postgres psql -U "$RESTORE_USER" -d "$RESTORE_DB" -v ON_ERROR_STOP=1 -c \
  "SELECT version_num FROM alembic_version;"

docker exec task12c_restore_postgres psql -U "$RESTORE_USER" -d "$RESTORE_DB" -v ON_ERROR_STOP=1 -c \
  "SELECT table_name FROM information_schema.tables
    WHERE table_schema='public' AND table_name IN
    ('influencers','influencer_platform_accounts','import_jobs','import_rows',
     'influencer_metric_snapshots','refresh_queues','refresh_queue_items',
     'import_task_requests') ORDER BY table_name;"

docker exec task12c_restore_postgres psql -U "$RESTORE_USER" -d "$RESTORE_DB" -v ON_ERROR_STOP=1 -c \
  "SELECT count(*) AS queue_lineage
     FROM refresh_queue_items q JOIN influencers i ON i.id=q.influencer_id
     JOIN influencer_platform_accounts a ON a.id=q.platform_account_id;
   SELECT count(*) AS snapshot_lineage
     FROM influencer_metric_snapshots s JOIN influencers i ON i.id=s.influencer_id
     JOIN influencer_platform_accounts a ON a.id=s.platform_account_id
     JOIN import_rows r ON r.id=s.import_row_id JOIN import_jobs j ON j.id=r.import_job_id;
   SELECT count(*) AS orphan_queue_items
     FROM refresh_queue_items q LEFT JOIN influencers i ON i.id=q.influencer_id
     LEFT JOIN influencer_platform_accounts a ON a.id=q.platform_account_id
     WHERE i.id IS NULL OR a.id IS NULL;"
```

Restore result: command exited successfully; revision was
`0005_phase2_refresh_queue`; source and restored counts matched exactly:

```text
influencers=6
influencer_platform_accounts=7
import_jobs=3
import_rows=15
influencer_metric_snapshots=9
refresh_queues=4
refresh_queue_items=6
import_task_requests=18
queue_lineage=6, snapshot_lineage=9, orphan_queue_items=0, orphan_import_rows=0
```

## Deployment Environment Profiles

The code supports only `development`, `test` and `production` for `APP_ENV`.
`Settings.secure_cookies` is true only for `production`. Session cookies are
HttpOnly, `SameSite=Lax`; CSRF cookies are readable by the browser and also
`SameSite=Lax`. `APP_ENV=test` is explicitly prohibited as a Task 13
deployment configuration.

### A. Internal Loopback Release (default)

- Use a supported non-test value: `APP_ENV=development` for HTTP over an SSH tunnel.
- Bind the host-facing Nginx port only to `127.0.0.1` (for example,
  `APP_PORT=127.0.0.1:8080`). Do not publish a public HTTP port.
- Access with an SSH local tunnel, for example
  `ssh -N -L 8080:127.0.0.1:8080 user@host`, then browse locally.
- Keep PostgreSQL and Redis without host port mappings; they remain on the
  internal Compose network.
- IPv4 loopback only is accepted for this profile; IPv6 loopback is not a
  blocker.

### B. Public HTTPS Release (not authorized by Task 12C)

- `APP_ENV=production` and an injected `APP_MASTER_KEY` are required.
- HTTPS is mandatory end to end at the public edge; configure a real domain,
  certificate issuance/renewal, TLS termination and HSTS policy.
- Production session and CSRF cookies are Secure; verify proxy forwarded-proto
  handling and secure-cookie behavior over HTTPS.
- Restrict security-group/firewall ingress to the intended HTTPS endpoint and
  keep PostgreSQL/Redis private with no host exposure.
- This profile requires explicit human authorization. Task 12C makes no server,
  firewall or deployment changes.

## Real Huitun Attachment

`HUITUN_REAL_SAMPLE_PATH` was unset in the current environment. The opt-in
external-file correctness gate was therefore **NOT EXECUTED**. No sensitive
directory was scanned, no sample was copied into Git and no sample was
fabricated. Per freeze policy this is not an internal release blocker.

## Image Provenance Manifest

Task 13 deployment tooling must emit the following fields for every release:

```text
git_sha=58cc5c1997765146962be4a5cb51d5c782a24766
alembic_head=0005_phase2_refresh_queue
build_timestamp=2026-08-15T10:45:33Z (api/worker image Created; web image Created at 2026-08-15T10:07:21Z)
compose_config_sha256=0e860455e95f4c0758f2e51aef2c5f1acbde41ca6ad4ead74ed24048c7ce30b9
api_image=sha256:fb43b4a0bdd6b39ee71a5ae9f0a67e4aab2385f47b08dc84dc0d3f2eb239565a
worker_image=sha256:fd2f1e87576e098257adccd0acd0df1a400b0b255000559d9dfabcb676418950
web_image=sha256:e6249667c9033cdc8033c1d257bbe4606948f869d3c042d2b289769cf42cadf5
nginx_image=sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10
postgres_image=sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777
redis_image=sha256:e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2
```

The image digests above are the locally inspected release-candidate images.
Git-SHA image labels are a later improvement and were not added in Task 12C.

## Cleanup and Gate Result

Only resources created by this task are eligible for cleanup: the
`task12c_refresh` Compose containers/networks/volumes, the restore container,
network and volume, the temporary dump and temporary Playwright snapshots. Do
not run `docker system prune` and do not touch the existing
`influencer-outreach` project.

After cleanup, verify `git status --short` is empty and the original project
still reports healthy `/health/live` and `/health/ready` endpoints. No P0/P1
issues are open. Task 13 remains prohibited until separately authorized.

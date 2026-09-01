# Production deployment: HTTPS, loopback edge, renewal, and backups

## Boundary and prerequisites

Production traffic is `Internet -> baidu.zmcmbd.com -> host Nginx :80/:443 ->
127.0.0.1:18080 -> Docker Nginx -> Web/API`. Only the host Nginx is public;
API, Web, PostgreSQL, and Redis have no public port. DNS must point
`baidu.zmcmbd.com` to the host, the firewall must allow only public 80/443,
and public SSH port 22 remains closed. Use Tencent TAT for operations.

Use `APP_ENV=production`. This enables Secure cookies, so the application must
be reached over HTTPS. Keep the production database password and stable
`APP_MASTER_KEY` in the host's untracked runtime `.env`/secret mechanism; never
place either in Git, logs, commands copied into shell history, or documentation.

## Mandatory Operator Auth P0 `0010 -> 0011` gate

The generic Compose startup commands in the next section must not be used to
perform the Operator Auth P0 rollout. The following order is authoritative and
must complete before public traffic is restored:

1. Record the exact production Department UUID, intended first Super Admin
   Operator UUID, current Git SHA, and new API/Web image digests. Against the
   `0010_permissions_v1_persistence` schema, an exact-UUID preflight must return
   exactly one row and prove that the Department and Operator are active, the
   Operator belongs to that Department, and both the Operator role and
   Department permission ceiling are `super_admin`. Stop on any mismatch; never
   guess or substitute another Operator.
2. Create and validate a recoverable production backup. Restore that backup to
   an isolated, disposable PostgreSQL 16 rehearsal environment and use the
   recorded **new** API artifact to run `0010 -> 0011`, initialize the same exact
   Operator, authenticate it, and complete an administrator write smoke. Destroy
   the rehearsal environment afterward and do not reuse its password.
3. Enter maintenance mode. Remove every old API instance from the load balancer,
   drain and stop it, block all direct API access, and stop workers or schedulers
   that can retain database transactions. Prove the vulnerable old
   `/api/v1/auth/select-operator` is unreachable and that PostgreSQL has no
   remaining long-running application transaction.
4. Configure an approved bounded PostgreSQL `lock_timeout` for the migration
   connection, then apply `0011_operator_auth_p0` from the recorded new artifact.
   Keep all external traffic closed. Both upgrade and downgrade perform DDL that
   can contend or deadlock with live session/operator transactions; full API
   quiescence is mandatory. A lock timeout or deadlock must fail the operation
   closed while maintenance mode remains active—never retry against live traffic
   or wait without a bound.
5. Recheck the Alembic head and exact UUID/state. The intended legacy target must
   still be active and `super_admin`, with `password_hash IS NULL` and
   `credential_version = 0`. From a one-off container using the same **new** API
   image, initialize 首位管理员 with an independent password:

   ```bash
   docker compose run --rm --no-deps api setup-operator-credential \
     --department-id "<exact-department-uuid>" \
     --operator-id "<exact-operator-uuid>" \
     --require-super-admin
   ```

6. Read back and prove that 首位管理员 remains active and `super_admin`, now with
   a non-null hash and `credential_version = 1`. Initialize every other required
   legacy Operator by its own exact Department/Operator UUID and independent
   password before allowing that Operator to work; never create a shared default.
7. Start the new API only on the internal loopback path. Smoke Department login,
   truthful unbound `/auth/me`, exact Operator-password authentication, old
   Session rejection, and an authenticated administrator write. Start the Web
   artifact with the recorded matching digest only after the API smoke passes.
8. Reopen public traffic only after every preceding check passes. No old API
   process may serve against the `0011` database.

Application-only rollback to any version that permits passwordless Operator
selection is forbidden before and after credential initialization. Before any
credential exists, schema downgrade may be considered only in full maintenance
mode after its guard succeeds. After initialization, downgrade must refuse;
remain in maintenance mode and forward-fix, or use a separately approved full
backup restore followed by deployment and smoke of the patched artifacts.

## Docker and host Nginx

From the repository checkout on the host, with its untracked production `.env`
in place, start the loopback overlay:

```bash
docker compose -f docker-compose.yml -f infrastructure/production/docker-compose.loopback.yml up -d --build
docker compose -f docker-compose.yml -f infrastructure/production/docker-compose.loopback.yml ps
```

The overlay publishes only `127.0.0.1:18080`. It mounts
`infrastructure/nginx/loopback-proxy.conf`, which may preserve the forwarding
headers from the host Nginx only because Docker Nginx is loopback-only and host
Nginx is the sole public ingress. Do not use that inner config for a
directly Internet-exposed Docker Nginx.

At the public boundary, host Nginx overwrites `X-Forwarded-For` with
`$remote_addr`. The inner loopback Nginx then preserves that trusted value.

Install `infrastructure/nginx/host-baidu.zmcmbd.com.conf` as the enabled host
site, create `/var/www/certbot`, then validate and reload:

```bash
sudo install -d -m 0755 /var/www/certbot
sudo nginx -t && sudo systemctl reload nginx
```

For first issuance, after the HTTP ACME location is enabled:

```bash
sudo certbot certonly --webroot -w /var/www/certbot -d baidu.zmcmbd.com
sudo nginx -t && sudo systemctl reload nginx
```

Confirm `certbot.timer` is enabled and active. Install
`infrastructure/certbot/reload-nginx.sh` as a Certbot renewal deploy hook, then
verify renewal without changing certificates:

```bash
sudo certbot renew --dry-run
curl --fail --show-error https://baidu.zmcmbd.com/health/live
```

The hook tests Nginx before reloading it. Certificate paths may appear in the
template; generated `/etc/letsencrypt` state, certificate contents, and private
keys must never enter Git.

## Backups

Install the backup script and units, then enable the persistent daily
03:30 Asia/Shanghai timer:

```bash
sudo install -m 0755 infrastructure/backup/influencer-outreach-backup /usr/local/sbin/influencer-outreach-backup
sudo install -m 0644 infrastructure/systemd/influencer-outreach-backup.service /etc/systemd/system/
sudo install -m 0644 infrastructure/systemd/influencer-outreach-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now influencer-outreach-backup.timer
sudo systemctl start influencer-outreach-backup.service
sudo systemctl status influencer-outreach-backup.service --no-pager
```

The script creates PostgreSQL custom-format dumps in
`/home/ubuntu/backups/influencer-outreach`, mode `0600`, validates each with
container-local `pg_restore --list`, writes a SHA-256 sidecar, and retains 30
days. Confirm a restore list manually with `docker exec` and `pg_restore --list`
before relying on a backup. Keep dumps and their real checksums out of Git.

## UI, rollback, and incidents

The authenticated application and login page must display `鄂ICP备2026044999号`
linked to `https://beian.miit.gov.cn/`. Add a 公安联网备案 record only after a real
number is issued.

Rollback pointers: restore the prior Git revision only when that revision does
not remove a deployed security fix. For Operator Auth P0 (`0011`) and later,
application-only rollback to any revision with passwordless Operator selection
is forbidden before and after credential initialization. Keep maintenance mode
and forward-fix instead. Restore the prior enabled host Nginx config only after
`nginx -t`. A verified, retained custom-format database dump is the last resort,
and traffic may reopen only after the patched auth artifact and its exact-ID
admin smoke pass against the restored database. Do not treat this runbook as
authorization to deploy from a development Mac.

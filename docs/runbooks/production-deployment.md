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

Rollback pointers: restore the prior Git revision and re-run the same loopback
Compose command; restore the prior enabled host Nginx config only after
`nginx -t`; restore data only from a verified, retained custom-format dump using
the approved recovery procedure. Do not treat this runbook as authorization to
deploy from a development Mac.

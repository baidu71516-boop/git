#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$project_dir"

app_port=${APP_PORT:-8080}
if [[ -f .env ]]; then
  configured_port=$(sed -n 's/^APP_PORT=//p' .env | tail -n 1)
  app_port=${configured_port:-$app_port}
fi

expected_services=(nginx web api worker scheduler postgres redis)
if docker compose version >/dev/null 2>&1; then
  compose=(docker compose)
else
  compose=(docker-compose)
fi

running_services=$("${compose[@]}" ps --status running --services)
for service in "${expected_services[@]}"; do
  if ! printf '%s\n' "$running_services" | grep -qx "$service"; then
    echo "Service is not running: $service" >&2
    exit 1
  fi
done

curl --fail --silent --show-error "http://127.0.0.1:$app_port/" >/dev/null
curl --fail --silent --show-error "http://127.0.0.1:$app_port/health/live" >/dev/null
curl --fail --silent --show-error "http://127.0.0.1:$app_port/health/ready" >/dev/null

"${compose[@]}" exec -T worker celery \
  --workdir /workspace/apps/worker \
  -A app.celery_app:celery_app inspect ping --timeout 10 >/dev/null

echo "All Phase 0 services are healthy."

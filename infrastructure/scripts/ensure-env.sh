#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
env_file="$project_dir/.env"
example_file="$project_dir/.env.example"

if [[ -f "$env_file" ]]; then
  exit 0
fi

cp "$example_file" "$env_file"
database_password=$(openssl rand -hex 24)
master_key=$(openssl rand -hex 32)

sed -i.bak "s/POSTGRES_PASSWORD=GENERATE_ME/POSTGRES_PASSWORD=$database_password/" "$env_file"
sed -i.bak "s#DATABASE_URL=postgresql+psycopg://outreach:GENERATE_ME@postgres:5432/outreach#DATABASE_URL=postgresql+psycopg://outreach:$database_password@postgres:5432/outreach#" "$env_file"
sed -i.bak "s/APP_MASTER_KEY=GENERATE_ME/APP_MASTER_KEY=$master_key/" "$env_file"
rm -f "$env_file.bak"
chmod 600 "$env_file"

echo "Created local .env with generated development secrets."


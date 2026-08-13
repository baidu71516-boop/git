SHELL := /usr/bin/env bash
COMPOSE := $(shell if docker compose version >/dev/null 2>&1; then printf 'docker compose'; else printf 'docker-compose'; fi)

.PHONY: install dev down lint test test-freshness-postgres health migrate compose-validate build clean

install:
	uv sync --all-packages
	pnpm install --frozen-lockfile

dev:
	./infrastructure/scripts/ensure-env.sh
	$(COMPOSE) up --build --detach
	$(COMPOSE) up --detach --force-recreate --wait --wait-timeout 120 nginx

down:
	$(COMPOSE) down

lint:
	uv run ruff check apps packages tests infrastructure/migrations
	uv run black --check apps packages tests infrastructure/migrations
	uv run mypy packages/backend_core/src
	uv run mypy apps/api/app
	uv run mypy apps/worker/app
	pnpm lint
	pnpm typecheck
	pnpm --filter @influencer-outreach/web exec prettier --check "**/*.{ts,tsx,css,json,mjs}"

test:
	uv run pytest packages/backend_core/tests tests/integration tests/smoke
	PYTHONPATH=apps/api uv run pytest apps/api/tests
	PYTHONPATH=apps/worker uv run pytest apps/worker/tests
	pnpm test

test-freshness-postgres:
	uv run pytest -s tests/integration/test_influencer_freshness_postgres.py

health:
	./infrastructure/scripts/health.sh

migrate:
	$(COMPOSE) run --rm api alembic -c infrastructure/migrations/alembic.ini upgrade head

compose-validate:
	./infrastructure/scripts/ensure-env.sh
	$(COMPOSE) --env-file .env.example config --quiet

build:
	$(COMPOSE) build

clean:
	rm -rf apps/web/.next .pytest_cache .mypy_cache .ruff_cache htmlcov

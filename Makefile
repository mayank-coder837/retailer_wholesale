# SmartBiz OS developer commands. Recipes are plain commands so they run under cmd.exe (Windows) and sh (CI).
COMPOSE = docker compose -f infra/compose.yaml
API = cd apps/api &&

.PHONY: install up down migrate seed test test-api test-web test-desktop e2e lint gen-client dev-api dev-web dev-desktop

install:
	$(API) uv sync
	pnpm install

up:
	$(COMPOSE) up -d --build db api web

down:
	$(COMPOSE) down

migrate:
	$(API) uv run alembic upgrade head

seed:
	$(API) uv run python -m app.scripts.seed

test: test-api test-web test-desktop

test-api:
	$(API) uv run pytest

test-web:
	pnpm --filter @smartbiz/web test

test-desktop:
	pnpm --filter @smartbiz/desktop test

e2e:
	pnpm --filter @smartbiz/web e2e

lint:
	$(API) uv run ruff check .
	$(API) uv run ruff format --check .
	$(API) uv run mypy
	pnpm -r lint
	pnpm -r typecheck

gen-client:
	$(API) uv run python -m app.scripts.export_openapi ../../packages/api-client/openapi.json
	pnpm --filter @smartbiz/api-client generate

dev-api:
	$(API) uv run uvicorn app.main:app --reload --port 8000

dev-web:
	pnpm --filter @smartbiz/web dev

dev-desktop:
	pnpm --filter @smartbiz/desktop dev

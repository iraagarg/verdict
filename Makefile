.PHONY: help install dev up down logs test lint typecheck fmt migrate bench clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install all JS and Python dependencies
	pnpm install --frozen-lockfile || pnpm install
	cd apps/evald && python3.12 -m venv .venv && .venv/bin/pip install -q -e ".[dev]"

up: ## Start the full stack (postgres, redis, gateway, evald, dashboard)
	docker compose up --build -d
	@echo "gateway   → http://localhost:8080/health"
	@echo "evald     → http://localhost:8000/health"
	@echo "dashboard → http://localhost:3000"

down: ## Stop the stack and remove volumes
	docker compose down -v

logs: ## Tail logs from all services
	docker compose logs -f

test: ## Run every test suite (this is what CI runs)
	pnpm -r test
	cd apps/evald && .venv/bin/python -m pytest -q

typecheck: ## Type-check TypeScript and Python
	pnpm -r typecheck
	cd apps/evald && .venv/bin/python -m mypy src tests

lint: ## Lint everything
	pnpm lint
	pnpm format:check
	cd apps/evald && .venv/bin/python -m ruff check src tests
	cd apps/evald && .venv/bin/python -m ruff format --check src tests

fmt: ## Auto-format everything
	pnpm prettier --write .
	cd apps/evald && .venv/bin/python -m ruff format src tests

migrate: ## Apply database migrations to $DATABASE_URL
	@for f in db/migrations/*.sql; do echo "applying $$f"; psql "$${DATABASE_URL:?set DATABASE_URL}" -v ON_ERROR_STOP=1 -f "$$f"; done

bench: ## Run the full benchmark and write artifacts/ — NOT IMPLEMENTED UNTIL P2
	@echo "make bench is defined in P2 (replay runner)."
	@echo "It must write committed artifacts under artifacts/ and be reproducible at \$$0."
	@echo "Per non-negotiable #1: until this target produces artifacts, Verdict has no numbers."
	@exit 1

clean: ## Remove build outputs and virtualenvs
	rm -rf node_modules apps/*/node_modules packages/*/node_modules
	rm -rf apps/*/dist packages/*/dist apps/dashboard/.next
	rm -rf apps/evald/.venv apps/evald/.pytest_cache apps/evald/.ruff_cache apps/evald/.mypy_cache

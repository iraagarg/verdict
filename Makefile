.PHONY: help install dev up down logs test lint typecheck fmt migrate corpus plan pilot label calibrate bench clean

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
	# Build first: the gateway imports @verdict/shared from dist, so a source
	# change there is invisible to the tests until it is compiled. CI builds
	# explicitly, and the local loop must not differ from CI.
	pnpm --filter @verdict/shared build
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

corpus: ## Rebuild the frozen benchmark corpus from public datasets (free, no API key)
	cd apps/evald && .venv/bin/python -m evald.cli corpus build

plan: ## Show the projected cost of a full replay. Spends nothing.
	cd apps/evald && .venv/bin/python -m evald.cli replay plan --cap $(CAP)

pilot: ## Measure whether the gradable slice discriminates between rungs (needs a key)
	cd apps/evald && .venv/bin/python -m evald.cli pilot --cap $(CAP) -n $(N)

label: ## Hand-label sampled pairs for judge calibration (blind, resumable, free)
	cd apps/evald && .venv/bin/python -m evald.cli label --pairs $(PAIRS)

calibrate: ## Judge the labelled pairs and write calibration_report.json
	cd apps/evald && .venv/bin/python -m evald.cli calibrate --pairs $(PAIRS) --judge-model $(JUDGE)

bench: ## Run the full replay and write a versioned artifact. Re-runs are free.
	@test -n "$(CAP)" || (echo "refusing to run without a spend cap: make bench CAP=30" && exit 1)
	cd apps/evald && .venv/bin/python -m evald.cli replay run --cap $(CAP)

clean: ## Remove build outputs and virtualenvs
	rm -rf node_modules apps/*/node_modules packages/*/node_modules
	rm -rf apps/*/dist packages/*/dist apps/dashboard/.next
	rm -rf apps/evald/.venv apps/evald/.pytest_cache apps/evald/.ruff_cache apps/evald/.mypy_cache

# Default spend cap and pilot size. Override: make bench CAP=30
CAP ?= 5
N ?= 200
PAIRS ?= 200
JUDGE ?= claude-opus-5

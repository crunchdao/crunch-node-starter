COMPOSE := docker compose -f docker-compose.yml --env-file .local.env

.PHONY: deploy down logs fmt lint check test init-db reset-db migrate migration benchmark benchmark-compare benchmark-verify build

# ── Code quality ─────────────────────────────────────────────────────
fmt:
	uv run ruff format .
	uv run ruff check --fix . || true

lint:
	uv run ruff format --check .
	uv run ruff check .

check: lint test

deploy:
	CACHEBUST=$$(date +%s) $(COMPOSE) build
	$(COMPOSE) up -d postgres
	$(COMPOSE) --profile init run --rm init-db
	$(COMPOSE) up -d

init-db:
	$(COMPOSE) --profile init run --rm init-db

reset-db:
	$(COMPOSE) --profile reset run --rm reset-db

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

test: lint
	PYTHONPATH=scaffold/challenge:scaffold/node uv run python -m pytest tests/ -x -q

verify:
	bash scaffold/node/scripts/verify_deployment.sh

verify-ui:
	bash tests/test_e2e_ui_smoke.sh

verify-all: verify verify-ui

# ── Benchmark ─────────────────────────────────────────────────────────
AGENT_CMD ?= pi
BENCHMARK_TIMEOUT ?= 900
BENCHMARK_EVIDENCE ?= standard

benchmark:
	uv run python -m tests.benchmark.run_benchmark --agent-cmd "$(AGENT_CMD)" --timeout $(BENCHMARK_TIMEOUT) --evidence $(BENCHMARK_EVIDENCE)

benchmark-compare:
	uv run python -m tests.benchmark.run_benchmark --compare

benchmark-verify:
	uv run python -m tests.benchmark.run_benchmark --verify-only $(WORKSPACE)

# ── Build & Publish ───────────────────────────────────────────────────
# force-include in pyproject.toml bundles scaffold/ and packs/ into the wheel.
build:
	uv build

# Database migrations (Alembic)
migrate:
	$(COMPOSE) --profile init run --rm init-db

migration:
	@read -p "Migration message: " msg; \
	$(COMPOSE) --profile init run --rm init-db alembic revision --autogenerate -m "$$msg"

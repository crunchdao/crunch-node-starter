COMPOSE := docker compose -f docker-compose.yml --env-file .local.env

.PHONY: deploy down logs fmt lint check test init-db reset-db migrate migration

# ── Code quality ─────────────────────────────────────────────────────
fmt:
	uv run ruff format .
	uv run ruff check --fix . || true

lint:
	uv run ruff format --check .
	uv run ruff check .

check: lint test

deploy:
	$(COMPOSE) build
	$(COMPOSE) up -d postgres
	$(COMPOSE) run --rm init-db
	$(COMPOSE) up -d

init-db:
	$(COMPOSE) run --rm init-db

reset-db:
	$(COMPOSE) run --rm reset-db

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

test: lint
	PYTHONPATH=base/challenge:base/node uv run python -m pytest tests/ -x -q

verify:
	bash base/node/scripts/verify_deployment.sh

verify-ui:
	bash tests/test_e2e_ui_smoke.sh

verify-all: verify verify-ui

# Database migrations (Alembic)
migrate:
	$(COMPOSE) run --rm init-db

migration:
	@read -p "Migration message: " msg; \
	$(COMPOSE) run --rm init-db alembic revision --autogenerate -m "$$msg"

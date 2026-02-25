# Workspace-level convenience — proxies to node/
.PHONY: deploy down logs logs-capture verify-e2e check-models init-db reset-db starter platform backfill test test-challenge

deploy:
	cd node && make deploy

down:
	cd node && make down

logs:
	cd node && make logs

logs-capture:
	cd node && make logs-capture

verify-e2e:
	cd node && make verify-e2e

check-models:
	cd node && make check-models

init-db:
	cd node && make init-db

reset-db:
	cd node && make reset-db

starter:
	cd node && make starter

platform:
	cd node && make platform

test: test-challenge

test-challenge:
	cd challenge && uv run --extra dev python -m pytest tests/ -x -q

backfill:
	cd node && make backfill SOURCE=$(SOURCE) SUBJECT=$(SUBJECT) KIND=$(KIND) GRANULARITY=$(GRANULARITY) FROM=$(FROM) TO=$(TO) PAGE_SIZE=$(PAGE_SIZE)

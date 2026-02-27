# node

Standalone node runtime workspace for `starter-challenge`.

## What belongs here

- local deployment/runtime config (`docker-compose.yml`, `Dockerfile`, `.local.env`)
- competition config (`config/crunch_config.py`)
- node-private adapters (`plugins/`) and overrides (`extensions/`)
- custom API endpoints (`api/`)

This folder is self-contained and runnable without referencing a parent starter repo.

## Local run

From this folder:

```bash
make deploy
make verify-e2e
```

# node

Standalone node runtime workspace for `starter-challenge`.

## What belongs here

- local deployment/runtime config (`docker-compose.yml`, `Dockerfile`, `.local.env`)
- callable path configuration (`config/callables.env`)
- node-private adapters (`plugins/`) and overrides (`extensions/`)
- node-private runtime callables (`config/`)
- vendored runtime packages under `runtime/`

This folder is self-contained and runnable without referencing a parent starter repo.

## Local run

From this folder:

```bash
make deploy
make verify-e2e
```

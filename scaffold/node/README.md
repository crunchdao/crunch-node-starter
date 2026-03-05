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

## Performance target

Architecture should allow for **~50ms predict roundtrip** when optimized.
If architecture changes are expected to deviate materially from this target,
call this out explicitly with impact and rationale.

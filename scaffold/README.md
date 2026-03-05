# starter-challenge

Coordinator workspace. Run from `node/`:

```bash
cd node
make deploy
make verify-e2e
```

## Structure

- `node/` — competition infrastructure (docker-compose, config, scripts)
- `challenge/` — participant-facing package (tracker, scoring, examples)
- `webapp/` — local clone of `crunchdao/coordinator-webapp` used by `report-ui`

## Performance target

- The coordinator architecture is expected to allow for **~50ms predict roundtrip** (when optimized).
- If any architecture decision is likely to deviate materially from this target, it must be explicitly flagged and explained.

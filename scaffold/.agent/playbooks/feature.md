# Playbook: Add a Feature

## Before you start

1. Read `.agent/context.md` — understand architecture and extension points
2. Read `.agent/policy.md` — check if your change touches an approval gate
3. Identify which area: node infrastructure, challenge logic, or both

## Workflow

### 1. Plan

- List files you will create or modify
- State which extension point you are using (see context.md → Extension points)
- Check the do-not-edit zones
- If the change touches an approval gate, stop and request approval

### 2. Implement

- Prefer extending via `CrunchConfig` callables over modifying engine code
- New API endpoints go in `node/api/` (auto-discovered)
- New metrics go via `get_default_registry().register()`
- Keep participant-facing code in `challenge/`, runtime code in `node/`

### 3. Validate

```bash
cd node
make deploy
make verify-e2e
```

If working on the engine repo (not a scaffolded workspace):

```bash
uv run python -m pytest tests/ -x -q
```

### 4. Complete

Produce:
- Change summary
- Assumptions made
- Verification result (pass/fail)
- Risk list

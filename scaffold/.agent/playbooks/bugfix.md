# Playbook: Fix a Bug

## Before you start

1. Read `.agent/context.md` — understand the pipeline and status lifecycles
2. Read `.agent/policy.md` — check if the fix area requires approval

## Workflow

### 1. Reproduce

- Check logs: `cd node && make logs`
- Check captured logs: `node/runtime-services.jsonl`
- Check known failure modes: `node/RUNBOOK.md`
- Identify which worker or service is affected

### 2. Diagnose

- Trace the data flow through the pipeline (Feed → Input → Prediction → Score → Snapshot → Checkpoint)
- Check `CrunchConfig` for misconfigured callables or types
- Check env vars in `node/.local.env` and `node/config/callables.env`

### 3. Fix

- Make the minimal change that fixes the issue
- If the fix touches an approval gate (DB schema, auth, emission, infra), stop and request approval
- Prefer configuration changes over code changes where possible

### 4. Validate

```bash
cd node
make deploy
make verify-e2e
```

If working on the engine repo:

```bash
uv run python -m pytest tests/ -x -q
```

### 5. Complete

Produce:
- Root cause description
- What was changed and why
- Verification result (pass/fail)
- Risk of regression

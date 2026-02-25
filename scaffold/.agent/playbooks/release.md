# Playbook: Deploy to Production

This playbook requires approval for all steps. Do not proceed without explicit human confirmation.

## Before you start

1. Read `.agent/policy.md` — this workflow touches multiple approval gates
2. Read `node/RUNBOOK.md` for known failure modes
3. Confirm the workspace passes local validation first

## Pre-flight

### 1. Validate locally

```bash
cd node
make deploy
make verify-e2e
```

All checks must pass before proceeding.

### 2. Review production config

- `node/.production.env.example` — confirm all required vars are set
- `API_KEY` is set and strong
- `crunch_pubkey`, `compute_provider`, `data_provider` are correct mainnet addresses
- `FEED_SOURCE`, `FEED_SUBJECTS` match the intended competition

### 3. Review emission config

- Verify `build_emission` tier distribution sums to 100%
- Verify frac64 values sum to 1,000,000,000
- Cross-check with on-chain crunch configuration (`crunch-cli crunch get "<name>"`)

## Deployment

### 4. Request approval

Present to the user:
- Full list of env var changes from local to production
- Emission config summary (tiers, pubkeys)
- Any schema migrations that will run
- Risk list

### 5. Deploy

After approval:
```bash
cd node
make deploy
make verify-e2e
make logs
```

### 6. Post-deploy verification

- Confirm all workers are running: `make logs`
- Hit health endpoint: `curl http://<host>:8000/healthz`
- Verify leaderboard is populating: `curl http://<host>:8000/reports/leaderboard`
- Check feed ingestion: `curl http://<host>:8000/reports/feeds`

### 7. Complete

Produce:
- Deployment summary
- Config diff (local → production)
- Verification results
- Rollback plan (what to do if something breaks)

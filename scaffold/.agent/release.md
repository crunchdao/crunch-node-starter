# Playbook: Deploy to Production

**All steps require approval.** Do not proceed without explicit human confirmation.

## Pre-flight

### 1. Local validation passes
```bash
make preflight
```

### 2. Review production config
- `node/.production.env.example` — all required vars set
- `API_KEY` is set and strong
- `crunch_pubkey`, `compute_provider`, `data_provider` are correct mainnet addresses
- `FEED_SOURCE`, `FEED_SUBJECTS` match intended competition

### 3. Review emission config
- `build_emission` tier distribution sums to 100%
- frac64 values sum to 1,000,000,000
- Cross-check with on-chain config (`crunch-cli crunch get "<name>"`)

## Deploy

### 4. Request approval
Present: env var diff (local → production), emission summary, schema migrations, risk list.

### 5. Deploy and verify
```bash
make deploy
make verify-e2e
```

### 6. Post-deploy checks
- Health: `curl http://<host>:8000/healthz`
- Leaderboard populating: `curl http://<host>:8000/reports/leaderboard`
- Feed ingestion: `curl http://<host>:8000/reports/feeds`

### 7. Document
Produce: deployment summary, config diff, verification results, rollback plan.

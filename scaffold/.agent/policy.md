# Agent Policy — starter-challenge

## Repo contract

The agent must always:

1. **Propose a plan** before making changes — list files to modify and why
2. **State assumptions** — especially about scoring logic, emission config, and on-chain behavior
3. **Run validation** after every change:
   ```bash
   cd node
   make deploy
   make verify-e2e
   ```
4. **Do not mark work complete if verification fails**
5. **Produce a summary** of changes, risks, and what was validated

---

## Approval gates

The following changes require explicit human approval before proceeding. Do not implement these autonomously.

### On-chain / emission changes
- Modifying `build_emission`, `crunch_pubkey`, `compute_provider`, or `data_provider` in `CrunchConfig`
- Any change to frac64 reward calculations or tier distributions
- Checkpoint status transitions (`SUBMITTED → CLAIMABLE → PAID`)

### Database schema changes
- Alembic migration files (`alembic/versions/`)
- Changes to DB table definitions

### Auth / security changes
- `API_KEY`, `API_READ_AUTH`, or `API_PUBLIC_PREFIXES` configuration
- Middleware or auth logic

### Infrastructure changes
- `docker-compose.yml` service definitions, networking, or volumes
- `Dockerfile` changes
- `.production.env.example` or any production config
- GitHub Actions / CI workflows

### Cryptography / wallet operations
- Merkle tree logic (`coordinator_node/merkle/`)

See `approvals.yml` for machine-readable gate definitions.

---

## Allowed operations

### Commands the agent may run freely

```bash
make deploy
make verify-e2e
make logs
make logs-capture
make down
make backfill SOURCE=<source> SUBJECT=<subject> FROM=<date> TO=<date>
uv run python -m pytest tests/ -x -q
```

### File operations

- **Create/edit freely:** `node/runtime_definitions/`, `node/config/`, `node/api/`, `node/extensions/`, `node/plugins/`, `challenge/starter_challenge/` (except `config.py`)
- **Edit with caution (state assumptions):** `node/.local.env`, `node/.env`
- **Approval required:** see approval gates above
- **Never delete:** production env files, wallet files, migration files, `node/data/`

---

## Output contracts

When completing a task, the agent must produce:

1. **Change summary** — what was modified and why
2. **Assumptions** — any inferences made about competition behavior
3. **Verification result** — output of `make verify-e2e` (pass/fail)
4. **Risk list** — anything that could break, especially around scoring, emission, or data integrity

# Agent Policy

## Rules

1. **Propose a plan** before making changes — list files to modify and why
2. **State assumptions** — especially about scoring, emission, and on-chain behavior
3. **Satisfy the Definition of Done** in AGENTS.md before completing any task

## Approval Gates

These changes require explicit human approval. Do not implement autonomously.

| Area | What requires approval |
|---|---|
| Emission / on-chain | `build_emission`, `crunch_pubkey`, `compute_provider`, `data_provider`, frac64 calculations, checkpoint transitions |
| Database schema | Alembic migrations, table definition changes |
| Auth / security | `API_KEY`, `API_READ_AUTH`, middleware, auth logic |
| Infrastructure | `docker-compose.yml`, `Dockerfile`, production config, CI workflows |
| Cryptography | Merkle tree logic |

## Defaults

- **Database tables** — all new tables must be PostgreSQL via SQLModel/SQLAlchemy with Alembic migrations. No SQLite, no raw SQL CREATE TABLE, no in-memory stores for persistent data.

## Allowed Operations

**Commands (run freely):**
`make deploy`, `make down`, `make logs`, `make verify-e2e`, `make test`, `make backfill`

**Files:**
- **Edit freely:** `node/config/`, `node/api/`, `node/extensions/`, `node/plugins/`, `challenge/starter_challenge/`
- **Edit with caution:** `node/.local.env`, `node/.env` — state assumptions
- **Never delete:** production env files, wallet files, migration files, `node/data/`

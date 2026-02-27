# Coordinator Workspace

Crunch coordinator node. `node/` runs the infrastructure, `challenge/` is the participant-facing package.

## Workflow

### 1. Verify baseline
Deploy the scaffold as-is. Confirm it works before changing anything.
```bash
make deploy
make verify-e2e
```
If this fails, fix it first — you need a working baseline.

### 2. Agree on the spec
Before writing code, confirm with the user:
- What is the Crunch about?
- What do participants predict? (inference input and output format)
- Whats the interface of the base model participants use?
- How are predictions scored?
- What data feeds the competition? (source, subjects, granularity)
- How often do models predict? What's the resolution horizon?
- How is ground truth derived from feed data?

Do not carry over [starter placeholder values](.agent/guide.md#starter-placeholders). Confirm every one and help the user to understand what each value is used for if necessary.

### 3. Implement
Follow the [Implementation Guide](.agent/guide.md) — types/tracker, examples, feeds, ground truth, then scoring. Order matters.
Validate proposals you have with the user and help them to make good decisions here by giving context and guidance. 

### 4. Wire in CrunchConfig
Connect everything in `node/config/crunch_config.py` — the single source of truth for types, scoring, schedules, and callables.

### 5. Test
```bash
make test
```
All unit tests green. Scoring `xfail` markers removed. Examples updated to match new types.

### 6. Deploy & verify
```bash
make deploy
make verify-e2e
```
Then observe the running system over time. How long depends on the prediction interval and resolve horizon — the system needs enough time to ingest feed data, generate predictions, and score them.

Re-run `make report` periodically to check progress. It generates `node/report.md` — containers, all API responses, per-model score analysis, docker log errors, and UI status. Read it and verify:
- Scores are non-zero and differentiated across models
- Predictions are flowing and being scored
- No errors in docker logs
- UI is reachable and shows data consistent with the API

Keep monitoring until scores are stable and the leaderboard makes sense. Log anything that doesn't look right and give this information to the user.

### 7. Fix loop
If anything is wrong:
1. Read `make logs` for the failing worker
2. Check [Gotchas](.agent/context.md#gotchas) for known issues
3. Fix → go back to step 5 (if code change) or step 6 (if config change)

**Keep looping until step 6 passes completely.**

## Commands

| Command | Purpose |
|---------|---------|
| `make test` | Unit tests (no Docker) |
| `make deploy` | Validate → build → start all services |
| `make verify-e2e` | Containers + API + scored predictions + leaderboard |
| `make preflight` | deploy → check-models → verify-e2e |
| `make logs` | Stream all service logs |
| `make down` | Tear down containers |
| `make reset-db` | Reset database (destructive) |
| `make backfill` | Backfill historical feed data |
| `make report` | Generate `node/report.md` — full system snapshot for review |

## Where to Edit

| What to change | Where |
|---|---|
| Types, scoring, schedules | `node/config/crunch_config.py` |
| Feed source, subjects, timing | `node/.local.env` |
| Custom API endpoints | `node/api/` (auto-discovered) |
| Node-side extensions | `node/extensions/` |
| Model interface | `challenge/***/tracker.py` |
| Scoring function | `challenge/***/scoring.py` |
| Example models | `challenge/***/examples/` |
| Docker / deployment | `node/deployment/` |

## Done Criteria

Do not declare done until:
- [ ] `make test` passes
- [ ] `make verify-e2e` passes — models registered, scores non-zero, leaderboard populated
- [ ] System observed over time — `make report` re-run periodically, scores stable and differentiated, no log errors, UI consistent with API
- [ ] Documentation written (below)

### Documentation Output
Produce before declaring done:
- **What was built** — components implemented and how they connect
- **Design decisions** — scoring logic, type choices, schedule parameters
- **Assumptions** — anything inferred rather than explicitly confirmed
- **Verification result** — pass/fail of each check above
- **Risks** — what could break, especially around scoring or emission

## Reference

- [Implementation Guide](.agent/guide.md) — how to build each component
- [Architecture](.agent/context.md) — pipeline, workers, CrunchConfig, API, gotchas
- [Policy](.agent/policy.md) — approval gates, allowed operations

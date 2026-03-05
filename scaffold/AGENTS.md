# Coordinator Workspace

Crunch coordinator node. `node/` runs the infrastructure, `challenge/` is the participant-facing package, and `webapp/` is a local clone of `crunchdao/coordinator-webapp`.

## Git Discipline

**Every new scaffold must be a git repo from the start.** Before doing anything else:

```bash
git init
git add -A
git commit -m "Initial scaffold (unmodified starter template)"
```

After each implementation step, make a clear commit that describes:
1. **What was done** — the change itself
2. **What was achieved** — the goal or milestone reached
3. **What was tested** — which checks passed (e.g. `make test`, `make verify-e2e`)

Example commits through a typical workflow:
```
Initial scaffold (unmodified starter template)
Define types and tracker interface — make test passes
Implement scoring function — unit tests green, xfail removed
Wire CrunchConfig and feed — make deploy + make verify-e2e pass
Fix scoring edge case — all tests green, logs clean
```

Never batch unrelated changes into one commit. If a step involves both code and config, that's fine in one commit — but separate steps get separate commits.

## UI Source

- `node/.local.env` should use `REPORT_UI_BUILD_CONTEXT=../webapp`.
- `make starter` and `make platform` switch `REPORT_UI_DOCKERFILE` between
  `apps/starter/Dockerfile` and `apps/platform/Dockerfile` in the local
  `webapp/` clone.

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

### Predict latency budget (mandatory)

- Keep architecture decisions aligned with a **~50ms predict roundtrip** target (when optimized).
- Predict roundtrip means the predict-worker path from data availability/wakeup to persisted predictions.
- If a decision is likely to deviate materially from this target, explicitly notify the user before/while implementing.
- Include the reason, estimated impact, and lower-latency alternatives.

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
`verify-e2e` prints a summary on completion: leaderboard rankings, per-model score stats, latest feed timestamps, and any failed predictions. Read it carefully.

Then observe over time:
- **Logs:** `make logs` — no errors or tracebacks in any worker
- **DB:** query postgres directly for pipeline health — see [Querying the Database](.agent/context.md#querying-the-database-directly)
- **UI:** open `localhost:3000` — pages render (Leaderboard, Models, Logs, Metrics), data matches what the API and DB show

Let the system run long enough for predictions to be scored (depends on `prediction_interval_seconds` and `resolve_horizon_seconds`). Re-run `make verify-e2e` periodically to check that scores are accumulating and stable.

Log anything that doesn't look right and give this information to the user.

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

## Where to Edit

| What to change | Where |
|---|---|
| Types, scoring, schedules | `node/config/crunch_config.py` |
| Feed source, subjects, timing | `node/.local.env` |
| Custom API endpoints | `node/api/` (auto-discovered) |
| Node-side extensions | `node/extensions/` |
| Model interface | `challenge/starter_challenge/tracker.py` |
| Scoring function | `challenge/starter_challenge/scoring.py` |
| Example models | `challenge/starter_challenge/examples/` |
| Docker / deployment | `node/deployment/` |
| UI app code | `webapp/apps/starter/`, `webapp/apps/platform/` |

## Done Criteria

Do not declare done until:
- [ ] `make test` passes
- [ ] `make verify-e2e` passes — models registered, scores non-zero, leaderboard populated
- [ ] `make logs` shows no errors in any worker
- [ ] API returns correct, fresh data
- [ ] UI loads and shows data consistent with API
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
- [Production Deploy](.agent/release.md) — when going to mainnet

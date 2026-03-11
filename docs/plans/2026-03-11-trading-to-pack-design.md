# Move Trading Services to Pack via CrunchConfig Callables

## Problem

Trading-specific code (simulator, costs, state persistence, DB tables) lives in the core `crunch_node/` PyPI package but is only used by the trading pack. This bloats the engine for all competition types and prevents organizers from modifying trading behavior.

## Solution

Move trading service code from `crunch_node/services/trading/` into `packs/trading/node/extensions/trading/`. The engine resolves these services at startup via CrunchConfig callable fields, using the existing `callable_resolver` pattern.

## What moves out of the engine

| Current location | New location |
|---|---|
| `crunch_node/services/trading/simulator.py` | `packs/trading/node/extensions/trading/simulator.py` |
| `crunch_node/services/trading/sink.py` | `packs/trading/node/extensions/trading/sink.py` |
| `crunch_node/services/trading/config.py` | `packs/trading/node/extensions/trading/config.py` |
| `crunch_node/services/trading/costs.py` | `packs/trading/node/extensions/trading/costs.py` |
| `crunch_node/services/trading/models.py` | `packs/trading/node/extensions/trading/models.py` |
| `crunch_node/db/tables/trading.py` | `packs/trading/node/extensions/trading/tables.py` |
| `crunch_node/db/trading_state_repository.py` | `packs/trading/node/extensions/trading/state_repository.py` |

## What stays in the engine

- Generic worker lifecycle (predict, score, checkpoint, report)
- Feed system (Binance, Pyth, ingestion, normalization)
- Scoring pipeline, metrics, ensembles
- DB infrastructure, base tables
- CrunchConfig base class + callable resolver

## CrunchConfig changes

New optional fields on CrunchConfig, defaulting to `None`:

- `predict_sink` — callable path to a sink class that receives feed ticks and predictions (used by predict_worker)
- `score_state_loader` — callable path to load external state for scoring (used by score_worker)
- `extra_db_tables` — list of callable paths to SQLModel table classes to register at startup

The trading pack's `CrunchConfig` sets these to point at its extensions.

## Worker changes

The existing `if config.trading:` conditionals in `predict_worker.py`, `score_worker.py`, and `report_worker.py` become `if config.predict_sink:` (or similar) — resolving the callable via `callable_resolver` instead of importing trading code directly.

## What organizers get

The trading code lands in their workspace under `node/extensions/trading/`. They can read, modify, and extend it — change cost models, adjust position limits, add custom logic. It's just Python files in their project.

## Context

- Addresses reviewer feedback from PR #33 (philippWassibauer): ship pack-specific features in the pack, not the PyPI package
- Chosen over explicit plugin system (over-engineered for one pack) and hybrid pack_services approach (premature abstraction)
- Aligns with existing `callable_resolver` pattern already used for scoring functions

# Move Trading Services to Pack — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move trading-specific services from `crunch_node/` (PyPI engine) into `packs/trading/node/extensions/trading/`, resolved via CrunchConfig callable fields.

**Architecture:** The engine gets three new optional CrunchConfig fields: `build_simulator_sink` (factory for predict-worker sink), `build_score_snapshots` (factory for score-worker snapshot builder), and `build_trading_widgets` (factory for report-worker UI widgets). When `None` (default), workers skip that capability. The trading pack sets these to callables in `node/extensions/trading/`. DB table registration piggybacks on SQLModel's `metadata.create_all()` — importing the table class before engine start is sufficient.

**Tech Stack:** Python, Pydantic, SQLModel, FastAPI

---

### Task 1: Create trading extensions directory in the trading pack

**Files:**
- Create: `packs/trading/node/extensions/__init__.py`
- Create: `packs/trading/node/extensions/trading/__init__.py`

**Step 1: Create the directories and init files**

```python
# packs/trading/node/extensions/__init__.py
# (empty)
```

```python
# packs/trading/node/extensions/trading/__init__.py
# (empty)
```

**Step 2: Commit**

```bash
git add packs/trading/node/extensions/
git commit -m "feat: create trading extensions directory in trading pack"
```

---

### Task 2: Copy trading service modules into the pack extensions

Copy the trading service code from the engine into the pack. These files move as-is, with import paths updated to be relative within the extensions package.

**Files:**
- Create: `packs/trading/node/extensions/trading/models.py` (from `crunch_node/services/trading/models.py`)
- Create: `packs/trading/node/extensions/trading/costs.py` (from `crunch_node/services/trading/costs.py`)
- Create: `packs/trading/node/extensions/trading/config.py` (from `crunch_node/services/trading/config.py`)
- Create: `packs/trading/node/extensions/trading/simulator.py` (from `crunch_node/services/trading/simulator.py`)
- Create: `packs/trading/node/extensions/trading/tables.py` (from `crunch_node/db/tables/trading.py`)
- Create: `packs/trading/node/extensions/trading/state_repository.py` (from `crunch_node/db/trading_state_repository.py`)
- Create: `packs/trading/node/extensions/trading/sink.py` (from `crunch_node/services/trading/sink.py`)

**Step 1: Copy each file and update imports**

Key import changes in each file:
- `from crunch_node.services.trading.costs import CostModel` → `from extensions.trading.costs import CostModel`
- `from crunch_node.services.trading.models import ...` → `from extensions.trading.models import ...`
- `from crunch_node.services.trading.config import TradingConfig` → `from extensions.trading.config import TradingConfig`
- `from crunch_node.services.trading.simulator import TradingEngine` → `from extensions.trading.simulator import TradingEngine`
- `from crunch_node.db.tables.trading import TradingStateRow` → `from extensions.trading.tables import TradingStateRow`
- `from crunch_node.db.trading_state_repository import TradingStateRepository` → `from extensions.trading.state_repository import TradingStateRepository`

The `sink.py` still imports from `crunch_node` for engine interfaces it hooks into:
- `from crunch_node.entities.prediction import InputRecord, PredictionRecord` (stays)
- `from crunch_node.feeds.contracts import FeedDataRecord` (stays)

The `state_repository.py` still imports:
- `from sqlmodel import Session, select` (stays)

The `tables.py` still imports:
- `from sqlmodel import Field, SQLModel` (stays)
- `from sqlalchemy import Column` (stays)
- `from sqlalchemy.dialects.postgresql import JSONB` (stays)

**Step 2: Commit**

```bash
git add packs/trading/node/extensions/trading/
git commit -m "feat: copy trading services into pack extensions with updated imports"
```

---

### Task 3: Update the trading pack's CrunchConfig to import from extensions

The trading pack's `crunch_config.py` currently imports `CostModel` and `TradingConfig` from `crunch_node.services.trading`. Update these to import from `extensions.trading` instead.

**Files:**
- Modify: `packs/trading/node/config/crunch_config.py`

**Step 1: Update imports**

Change:
```python
# Remove these if present - CostModel and TradingConfig are defined inline currently
# but the classes should now come from extensions
from crunch_node.services.trading.costs import CostModel  # REMOVE if present
from crunch_node.services.trading.config import TradingConfig  # REMOVE if present
```

Actually, the trading pack's `crunch_config.py` currently defines `CostModel` and `TradingConfig` inline (duplicating the engine versions). After this task, it should import them from `extensions.trading` instead:

```python
from extensions.trading.costs import CostModel
from extensions.trading.config import TradingConfig
```

Remove the inline `CostModel` and `TradingConfig` class definitions from this file.

**Step 2: Commit**

```bash
git add packs/trading/node/config/crunch_config.py
git commit -m "refactor: trading pack config imports from extensions instead of inline"
```

---

### Task 4: Add CrunchConfig callable fields for trading hooks

Add three new optional fields to the base `CrunchConfig` that packs can set to wire pack-specific behavior into workers.

**Files:**
- Modify: `crunch_node/crunch_config.py`
- Test: `tests/test_crunch_config_hooks.py`

**Step 1: Write the failing test**

```python
# tests/test_crunch_config_hooks.py
from crunch_node.crunch_config import CrunchConfig


def test_hook_fields_default_to_none():
    config = CrunchConfig()
    assert config.build_simulator_sink is None
    assert config.build_score_snapshots is None
    assert config.build_trading_widgets is None


def test_hook_fields_accept_callables():
    def my_sink_factory(**kwargs):
        return "sink"

    def my_snapshot_factory(**kwargs):
        return []

    def my_widgets_factory():
        return []

    config = CrunchConfig(
        build_simulator_sink=my_sink_factory,
        build_score_snapshots=my_snapshot_factory,
        build_trading_widgets=my_widgets_factory,
    )
    assert config.build_simulator_sink is my_sink_factory
    assert config.build_score_snapshots is my_snapshot_factory
    assert config.build_trading_widgets is my_widgets_factory
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_crunch_config_hooks.py -v`
Expected: FAIL — fields don't exist yet

**Step 3: Add the fields to CrunchConfig**

Add to `crunch_node/crunch_config.py` in the `CrunchConfig` class, after the existing callable fields:

```python
    build_simulator_sink: Callable[..., Any] | None = Field(
        default=None,
        description=(
            "Factory callable that builds a simulator sink for the predict worker. "
            "Signature: (session, config) → sink object with on_record() and on_predictions() methods. "
            "When None, no simulator sink is wired."
        ),
    )
    build_score_snapshots: Callable[..., Any] | None = Field(
        default=None,
        description=(
            "Factory callable that builds trading snapshots for the score worker. "
            "Signature: (session, config, snapshot_repository) → callable(now) → list[SnapshotRecord]. "
            "When None, the standard prediction-based scoring path is used."
        ),
    )
    build_trading_widgets: Callable[..., Any] | None = Field(
        default=None,
        description=(
            "Factory callable that returns metrics widget config for the report UI. "
            "Signature: () → list[dict]. When None, standard widgets are built."
        ),
    )
```

Add `from collections.abc import Callable` to the imports at the top.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_crunch_config_hooks.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add crunch_node/crunch_config.py tests/test_crunch_config_hooks.py
git commit -m "feat: add optional hook fields to CrunchConfig for pack-specific services"
```

---

### Task 5: Create factory functions in the trading pack extensions

Create the factory functions that the trading pack's CrunchConfig will point to. These encapsulate all the wiring currently hardcoded in the workers.

**Files:**
- Create: `packs/trading/node/extensions/trading/factories.py`
- Test: `tests/test_trading_factories.py`

**Step 1: Write the failing test**

```python
# tests/test_trading_factories.py
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add the trading pack's node directory to sys.path so extensions.trading is importable
_pack_node = str(Path(__file__).resolve().parent.parent / "packs" / "trading" / "node")
if _pack_node not in sys.path:
    sys.path.insert(0, _pack_node)

from extensions.trading.factories import build_simulator_sink, build_score_snapshots


def test_build_simulator_sink_returns_sink():
    session = MagicMock()
    # Mock the state repo to return no existing model IDs
    config = MagicMock()
    config.trading.cost_model = MagicMock()
    config.trading.max_position_size = 10.0
    config.trading.max_portfolio_size = 20.0
    config.trading.signal_mode = "order"
    config.trading.asset_price_mapping = {"BTC": "BTCUSDT"}

    sink = build_simulator_sink(session=session, config=config)
    assert sink is not None
    assert hasattr(sink, "on_record")
    assert hasattr(sink, "on_predictions")


def test_build_score_snapshots_returns_callable():
    session = MagicMock()
    config = MagicMock()
    snapshot_repo = MagicMock()

    builder = build_score_snapshots(
        session=session, config=config, snapshot_repository=snapshot_repo
    )
    assert callable(builder)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_trading_factories.py -v`
Expected: FAIL — module doesn't exist

**Step 3: Implement the factory functions**

```python
# packs/trading/node/extensions/trading/factories.py
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from extensions.trading.config import TradingConfig
from extensions.trading.simulator import TradingEngine
from extensions.trading.sink import SimulatorSink
from extensions.trading.state_repository import TradingStateRepository

logger = logging.getLogger(__name__)


def build_simulator_sink(*, session: Any, config: Any) -> SimulatorSink:
    """Factory for predict_worker: build a SimulatorSink with crash recovery."""
    trading: TradingConfig = config.trading
    simulator = TradingEngine(
        cost_model=trading.cost_model,
        max_position_size=trading.max_position_size,
        max_portfolio_size=trading.max_portfolio_size,
    )
    state_repo = TradingStateRepository(session)

    model_ids = state_repo.get_all_model_ids()
    for model_id in model_ids:
        state = state_repo.load_state(model_id)
        if state is not None:
            simulator.load_state(model_id, state)
            logger.info("Restored trading state for model %s", model_id)

    return SimulatorSink(
        simulator=simulator,
        state_repository=state_repo,
        trading_config=trading,
        model_ids=model_ids,
        signal_mode=trading.signal_mode,
    )


def build_score_snapshots(*, session: Any, config: Any, snapshot_repository: Any):
    """Factory for score_worker: returns a callable(now) → list[SnapshotRecord]."""
    from extensions.trading.state_repository import TradingStateRepository

    state_repo = TradingStateRepository(session)

    def build_snapshots(now: datetime) -> list:
        from crunch_node.entities.prediction import SnapshotRecord

        model_ids = state_repo.get_all_model_ids()
        if not model_ids:
            return []

        snapshots = []
        for model_id in model_ids:
            state = state_repo.load_state(model_id)
            if state is None:
                continue

            positions_data = state.get("positions", [])
            trades_data = state.get("trades", [])
            portfolio_fees = state.get("portfolio_fees", 0.0)
            closed_carry = state.get("closed_carry", 0.0)

            total_unrealized = 0.0
            for p in positions_data:
                entry = p["entry_price"]
                current = p.get("current_price", entry)
                size = p["size"]
                if entry > 0:
                    price_return = (current - entry) / entry
                    if p["direction"] == "short":
                        price_return = -price_return
                    total_unrealized += size * price_return

            total_realized = sum(
                t.get("realized_pnl", 0.0) or 0.0 for t in trades_data
            )
            total_carry = (
                sum(p.get("accrued_carry", 0.0) for p in positions_data) + closed_carry
            )
            net_pnl = total_unrealized + total_realized - portfolio_fees - total_carry

            result_summary: dict[str, Any] = {
                "net_pnl": net_pnl,
                "unrealized_pnl": total_unrealized,
                "realized_pnl": total_realized,
                "total_fees": portfolio_fees,
                "total_carry_costs": total_carry,
                "open_position_count": len(positions_data),
            }

            result_summary.update(
                _compute_trading_metrics(
                    snapshot_repository, model_id, net_pnl, trades_data
                )
            )

            snapshots.append(
                SnapshotRecord(
                    id=f"SNAP_{model_id}_{now.strftime('%Y%m%d_%H%M%S')}",
                    model_id=model_id,
                    period_start=now,
                    period_end=now,
                    prediction_count=len(positions_data),
                    result_summary=result_summary,
                )
            )

        return snapshots

    return build_snapshots


def _compute_trading_metrics(
    snapshot_repository: Any,
    model_id: str,
    current_net_pnl: float,
    trades: list[dict[str, Any]],
) -> dict[str, float]:
    metrics: dict[str, float] = {}

    historical = snapshot_repository.find(model_id=model_id)
    pnl_series = [float(s.result_summary.get("net_pnl", 0.0)) for s in historical]
    pnl_series.append(current_net_pnl)

    metrics["max_drawdown"] = _max_drawdown(pnl_series)

    profitable = sum(1 for t in trades if (t.get("realized_pnl") or 0.0) > 0)
    metrics["hit_rate"] = profitable / len(trades) if trades else 0.0

    metrics["sortino_ratio"] = _sortino_ratio(pnl_series)

    return metrics


def _max_drawdown(pnl_series: list[float]) -> float:
    if len(pnl_series) < 2:
        return 0.0
    peak = pnl_series[0]
    max_dd = 0.0
    for pnl in pnl_series[1:]:
        if pnl > peak:
            peak = pnl
        dd = peak - pnl
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _sortino_ratio(pnl_series: list[float]) -> float:
    if len(pnl_series) < 2:
        return 0.0
    returns = [pnl_series[i] - pnl_series[i - 1] for i in range(1, len(pnl_series))]
    mean_return = sum(returns) / len(returns)
    downside = [r for r in returns if r < 0]
    if not downside:
        return 0.0
    downside_var = sum(r * r for r in downside) / len(downside)
    downside_std = downside_var**0.5
    if downside_std < 1e-12:
        return 0.0
    return mean_return / downside_std


def build_trading_widgets() -> list[dict[str, Any]]:
    """Build metrics widgets for trading competition UI."""
    return [
        {
            "id": 1,
            "name": "PnL",
            "type": "chart",
            "metrics": [
                {
                    "type": "line",
                    "xAxis": {"name": "performed_at"},
                    "yAxis": {
                        "series": [{"name": "net_pnl", "label": "Net PnL"}],
                        "format": "decimal-4",
                    },
                    "displayEvolution": False,
                }
            ],
        }
    ]
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_trading_factories.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add packs/trading/node/extensions/trading/factories.py tests/test_trading_factories.py
git commit -m "feat: add factory functions for trading pack hooks"
```

---

### Task 6: Wire trading pack CrunchConfig to use the factory hooks

Update the trading pack's CrunchConfig to set the three hook fields, pointing at the factory functions. Also ensure the `TradingStateRow` table class is imported so SQLModel discovers it.

**Files:**
- Modify: `packs/trading/node/config/crunch_config.py`

**Step 1: Update the CrunchConfig**

Add to the imports:
```python
from extensions.trading.factories import (
    build_simulator_sink,
    build_score_snapshots,
    build_trading_widgets,
)
import extensions.trading.tables  # ensure table registered with SQLModel metadata
```

Add to the `CrunchConfig` class fields:
```python
    build_simulator_sink: Callable[..., Any] | None = build_simulator_sink
    build_score_snapshots: Callable[..., Any] | None = build_score_snapshots
    build_trading_widgets: Callable[..., Any] | None = build_trading_widgets
```

Remove the `trading` field — it's no longer needed as a CrunchConfig field because the factories handle everything. The `TradingConfig` is used internally by the factories. However, the factories need access to the config, so keep `trading` as a field but move its type import to extensions.

**Step 2: Commit**

```bash
git add packs/trading/node/config/crunch_config.py
git commit -m "feat: wire trading pack CrunchConfig to factory hooks"
```

---

### Task 7: Update predict_worker to use the hook

Replace the hardcoded `_maybe_build_simulator_sink` function with a call to `config.build_simulator_sink`.

**Files:**
- Modify: `crunch_node/workers/predict_worker.py`
- Test: `tests/test_predict_worker_simulator.py` (update existing)

**Step 1: Write/update the test**

Update the existing test to verify the worker uses `config.build_simulator_sink` instead of checking `config.trading`:

```python
def test_predict_worker_uses_build_simulator_sink_hook():
    mock_sink = MagicMock()
    mock_sink.on_record = AsyncMock()
    mock_sink.on_predictions = MagicMock()

    config = MagicMock()
    config.build_simulator_sink = MagicMock(return_value=mock_sink)
    config.trading = None  # should not matter anymore

    # The worker should call config.build_simulator_sink(session=..., config=...)
    # and wire the result as a feed sink
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_predict_worker_simulator.py -v`

**Step 3: Update predict_worker.py**

Remove the `_maybe_build_simulator_sink` function entirely. In `main()`, replace:

```python
    simulator_sink = _maybe_build_simulator_sink(config, session)
```

With:

```python
    simulator_sink = None
    if config.build_simulator_sink is not None:
        simulator_sink = config.build_simulator_sink(session=session, config=config)
        logger.info("Simulator sink enabled via build_simulator_sink hook")
```

Remove the trading-specific imports at the top:
- Remove: `from crunch_node.db.trading_state_repository import TradingStateRepository`
- Remove: `from crunch_node.services.trading.simulator import TradingEngine`
- Remove: `from crunch_node.services.trading.sink import SimulatorSink`

Also update the `pair_to_asset` setup. Currently it reads `config.trading.asset_price_mapping`. This should move into the factory — the `SimulatorSink` already handles pair-to-asset mapping internally. Remove the pair_to_asset block from predict_worker.py and pass an empty dict to FeedWindow:

```python
    feed_window = FeedWindow(max_size=120)
```

Wait — `FeedWindow` uses `pair_to_asset` so that `get_input()` returns asset names instead of pair names. This is needed for the predict service to pass the right subject to models. The trading pack needs this mapping but it's a FeedWindow concern, not a trading-services concern.

Better approach: let `build_simulator_sink` return both the sink and any feed_window config it needs. Or add a `pair_to_asset` field to CrunchConfig. Actually the simplest: keep reading `pair_to_asset` from `config.trading` if it exists, but make it a generic CrunchConfig field:

Add to CrunchConfig:
```python
    feed_subject_mapping: dict[str, str] = Field(
        default_factory=dict,
        description="Map feed subjects to model-facing names (e.g. BTCUSDT → BTC)",
    )
```

The trading pack sets this from its asset_price_mapping. Then predict_worker reads `config.feed_subject_mapping`.

**Step 4: Run tests**

Run: `pytest tests/test_predict_worker_simulator.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add crunch_node/workers/predict_worker.py crunch_node/crunch_config.py tests/test_predict_worker_simulator.py
git commit -m "refactor: predict_worker uses build_simulator_sink hook instead of hardcoded trading imports"
```

---

### Task 8: Update score_worker to use the hook

Replace the hardcoded trading state repository wiring with the `build_score_snapshots` hook.

**Files:**
- Modify: `crunch_node/workers/score_worker.py`
- Modify: `crunch_node/services/score.py`
- Test: `tests/test_score_worker_trading.py` (update existing)
- Test: `tests/test_score_trading.py` (update existing)

**Step 1: Update score_worker.py**

In `build_service()`, replace:
```python
    trading_state_repo = None
    if getattr(config, "trading", None) is not None:
        from crunch_node.db.trading_state_repository import TradingStateRepository
        trading_state_repo = TradingStateRepository(session)
```

With:
```python
    build_snapshots_fn = None
    if config.build_score_snapshots is not None:
        build_snapshots_fn = config.build_score_snapshots(
            session=session, config=config, snapshot_repository=snapshot_repo
        )
```

Pass `build_snapshots_fn` to `ScoreService` instead of `trading_state_repository`.

**Step 2: Update score.py**

Replace `self.trading_state_repository` with `self._build_snapshots_fn` (a callable or None).

In `score_and_snapshot()`, replace:
```python
        if self.trading_state_repository is not None:
            snapshots = self._build_trading_snapshots(now)
```

With:
```python
        if self._build_snapshots_fn is not None:
            snapshots = self._build_snapshots_fn(now)
```

Remove the `_build_trading_snapshots`, `_compute_trading_metrics`, `_get_pnl_history`, `_max_drawdown`, and `_sortino_ratio` methods from `ScoreService` — this logic now lives in `packs/trading/node/extensions/trading/factories.py`.

In `main()`, update the IO validation check:
```python
    if service._build_snapshots_fn is None:
        service.validate_scoring_io()
```

**Step 3: Run tests**

Run: `pytest tests/test_score_worker_trading.py tests/test_score_trading.py -v`
Expected: Tests need updating to use the new hook pattern

**Step 4: Update tests to use hook pattern**

Update tests to mock `config.build_score_snapshots` instead of `config.trading`.

**Step 5: Commit**

```bash
git add crunch_node/workers/score_worker.py crunch_node/services/score.py tests/test_score_worker_trading.py tests/test_score_trading.py
git commit -m "refactor: score_worker uses build_score_snapshots hook instead of hardcoded trading imports"
```

---

### Task 9: Update report_worker to use the hook

Replace the hardcoded `_build_trading_widgets` function and `is_trading` check with the `build_trading_widgets` hook.

**Files:**
- Modify: `crunch_node/workers/report_worker.py`

**Step 1: Update report_worker.py**

Remove the `_build_trading_widgets()` function.

Replace:
```python
    is_trading = getattr(contract, "trading", None) is not None
    if is_trading:
        widgets = _build_trading_widgets()
    else:
        widgets = _build_standard_widgets(series, metric_series, contract)
```

With:
```python
    if contract.build_trading_widgets is not None:
        widgets = contract.build_trading_widgets()
    else:
        widgets = _build_standard_widgets(series, metric_series, contract)
```

**Step 2: Commit**

```bash
git add crunch_node/workers/report_worker.py
git commit -m "refactor: report_worker uses build_trading_widgets hook instead of hardcoded check"
```

---

### Task 10: Remove trading code from the engine

Now that all references go through hooks, remove the trading-specific code from the engine.

**Files:**
- Delete: `crunch_node/services/trading/` (entire directory)
- Delete: `crunch_node/db/tables/trading.py`
- Delete: `crunch_node/db/trading_state_repository.py`
- Modify: `crunch_node/db/tables/__init__.py` (remove TradingStateRow import)
- Modify: `crunch_node/crunch_config.py` (remove `trading` field if it was there — check first)

**Step 1: Remove the files**

```bash
rm -rf crunch_node/services/trading/
rm crunch_node/db/tables/trading.py
rm crunch_node/db/trading_state_repository.py
```

**Step 2: Update `crunch_node/db/tables/__init__.py`**

Remove:
```python
from crunch_node.db.tables.trading import TradingStateRow
```

Remove `"TradingStateRow"` from `__all__`.

**Step 3: Run full test suite**

Run: `pytest tests/ -v --ignore=tests/benchmark_trading`
Expected: All non-trading tests pass. Trading tests will fail because they import from the old paths — that's expected and fixed in Task 11.

**Step 4: Commit**

```bash
git add -A
git commit -m "refactor: remove trading services from core engine"
```

---

### Task 11: Update trading tests to import from pack extensions

All 13 trading test files import from `crunch_node.services.trading.*`. Update them to import from `extensions.trading.*` and add the pack's `node/` directory to `sys.path`.

**Files:**
- Modify: All `tests/test_trading_*.py` files
- Modify: `tests/test_simulator_sink.py`
- Modify: `tests/test_simulator_hook.py`
- Modify: `tests/test_score_trading.py`
- Modify: `tests/test_predict_worker_simulator.py`

**Step 1: Add a conftest.py helper or update each test file**

Add a `conftest.py` (or update existing) that puts the trading pack's node dir on `sys.path`:

```python
# tests/conftest.py (add to existing or create)
import sys
from pathlib import Path

_pack_node = str(Path(__file__).resolve().parent.parent / "packs" / "trading" / "node")
if _pack_node not in sys.path:
    sys.path.insert(0, _pack_node)
```

Then update imports in each test file:
- `from crunch_node.services.trading.simulator import TradingEngine` → `from extensions.trading.simulator import TradingEngine`
- `from crunch_node.services.trading.sink import SimulatorSink` → `from extensions.trading.sink import SimulatorSink`
- `from crunch_node.services.trading.config import TradingConfig` → `from extensions.trading.config import TradingConfig`
- `from crunch_node.services.trading.costs import CostModel` → `from extensions.trading.costs import CostModel`
- `from crunch_node.services.trading.models import Position, Trade, Direction` → `from extensions.trading.models import Position, Trade, Direction`
- `from crunch_node.db.trading_state_repository import TradingStateRepository` → `from extensions.trading.state_repository import TradingStateRepository`
- `from crunch_node.db.tables.trading import TradingStateRow` → `from extensions.trading.tables import TradingStateRow`

**Step 2: Run all trading tests**

Run: `pytest tests/test_trading_*.py tests/test_simulator_sink.py tests/test_simulator_hook.py -v`
Expected: ALL PASS

**Step 3: Run full test suite**

Run: `pytest tests/ -v --ignore=tests/benchmark_trading`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add tests/
git commit -m "refactor: update trading tests to import from pack extensions"
```

---

### Task 12: Clean up feed_window.py

The `pair_to_asset` parameter in `FeedWindow` is a generic concern (subject remapping), not trading-specific. It stays in the engine but rename it for clarity and wire it from the new `feed_subject_mapping` CrunchConfig field.

**Files:**
- Modify: `crunch_node/services/feed_window.py` (rename param if desired — optional)
- Verify: predict_worker uses `config.feed_subject_mapping`

**Step 1: Verify no stale trading references remain**

Run:
```bash
grep -r "crunch_node.services.trading" crunch_node/
grep -r "crunch_node.db.trading" crunch_node/
grep -r "crunch_node.db.tables.trading" crunch_node/
```

Expected: No results.

**Step 2: Commit** (if any changes)

```bash
git add crunch_node/
git commit -m "chore: verify no trading references remain in engine"
```

---

### Task 13: Final verification

**Step 1: Run the full test suite**

```bash
pytest tests/ -v --ignore=tests/benchmark_trading
```

Expected: ALL PASS

**Step 2: Verify the engine has no trading imports**

```bash
grep -r "trading" crunch_node/ --include="*.py" | grep -v "__pycache__" | grep -v "build_trading_widgets\|build_simulator_sink\|build_score_snapshots\|feed_subject_mapping"
```

Expected: Only references to the hook field names and their documentation strings.

**Step 3: Verify the pack has all trading code**

```bash
ls packs/trading/node/extensions/trading/
```

Expected: `__init__.py`, `models.py`, `costs.py`, `config.py`, `simulator.py`, `tables.py`, `state_repository.py`, `sink.py`, `factories.py`

**Step 4: Commit and summarize**

```bash
git log --oneline HEAD~12..HEAD
```

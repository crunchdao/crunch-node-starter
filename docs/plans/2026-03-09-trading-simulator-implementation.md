# Trading Simulator Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a TradingSimulator that converts model signals into positions, marks-to-market on every tick, and writes portfolio snapshots for leaderboard scoring.

**Architecture:** The simulator hooks into the existing feed-predict worker via two extension points: a feed sink (for ticks) and `post_predict_hook` (for orders). It writes to the existing `SnapshotRecord` table. No new DB tables needed for the scoring pipeline — position/trade state is in-memory.

**Tech Stack:** Python, Pydantic, existing FeedDataService sink interface, existing SnapshotRecord/SnapshotRepository

**Design doc:** `docs/plans/2026-03-09-trading-simulator-design.md`

---

### Task 1: Position and Trade data models

**Files:**
- Create: `crunch_node/services/trading/models.py`
- Test: `tests/test_trading_models.py`

**Step 1: Write the failing test**

```python
from datetime import datetime, UTC

def test_position_unrealized_pnl_long():
    from crunch_node.services.trading.models import Position
    pos = Position(
        model_id="model_1",
        subject="BTCUSDT",
        direction="long",
        leverage=0.5,
        entry_price=50000.0,
        opened_at=datetime.now(UTC),
    )
    pos.current_price = 51000.0
    assert pos.unrealized_pnl == 0.5 * (51000.0 - 50000.0) / 50000.0

def test_position_unrealized_pnl_short():
    from crunch_node.services.trading.models import Position
    pos = Position(
        model_id="model_1",
        subject="BTCUSDT",
        direction="short",
        leverage=0.3,
        entry_price=50000.0,
        opened_at=datetime.now(UTC),
    )
    pos.current_price = 49000.0
    assert pos.unrealized_pnl == 0.3 * (50000.0 - 49000.0) / 50000.0

def test_trade_record_creation():
    from crunch_node.services.trading.models import Trade
    trade = Trade(
        model_id="model_1",
        subject="BTCUSDT",
        direction="long",
        leverage=0.5,
        entry_price=50000.0,
        opened_at=datetime.now(UTC),
    )
    assert trade.exit_price is None
    assert trade.realized_pnl is None
    assert trade.closed_at is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_trading_models.py -v`
Expected: FAIL with ImportError

**Step 3: Write minimal implementation**

`crunch_node/services/trading/__init__.py` — empty

`crunch_node/services/trading/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Position:
    model_id: str
    subject: str
    direction: str  # "long" or "short"
    leverage: float
    entry_price: float
    opened_at: datetime
    current_price: float = 0.0
    accrued_carry: float = 0.0

    @property
    def unrealized_pnl(self) -> float:
        if self.entry_price == 0:
            return 0.0
        price_return = (self.current_price - self.entry_price) / self.entry_price
        if self.direction == "short":
            price_return = -price_return
        return self.leverage * price_return


@dataclass
class Trade:
    model_id: str
    subject: str
    direction: str
    leverage: float
    entry_price: float
    opened_at: datetime
    exit_price: float | None = None
    closed_at: datetime | None = None
    realized_pnl: float | None = None
    fees_paid: float = 0.0
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_trading_models.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add crunch_node/services/trading/ tests/test_trading_models.py
git commit -m "feat(trading): add Position and Trade data models"
```

---

### Task 2: Cost model

**Files:**
- Create: `crunch_node/services/trading/costs.py`
- Test: `tests/test_trading_costs.py`

**Step 1: Write the failing test**

```python
def test_trading_fee():
    from crunch_node.services.trading.costs import CostModel
    costs = CostModel(trading_fee_pct=0.001, spread_pct=0.0, carry_annual_pct=0.0)
    fee = costs.order_cost(leverage=0.5)
    assert fee == 0.001 * 0.5  # fee scales with leverage

def test_spread_cost():
    from crunch_node.services.trading.costs import CostModel
    costs = CostModel(trading_fee_pct=0.0, spread_pct=0.001, carry_annual_pct=0.0)
    fee = costs.order_cost(leverage=1.0)
    assert fee == 0.001

def test_carry_cost():
    from crunch_node.services.trading.costs import CostModel
    costs = CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.1095)
    daily = costs.carry_cost(leverage=1.0, seconds=86400)
    assert abs(daily - 0.0003) < 0.0001  # 10.95% / 365 ≈ 0.0003 per day
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_trading_costs.py -v`
Expected: FAIL with ImportError

**Step 3: Write minimal implementation**

```python
from __future__ import annotations

from pydantic import BaseModel, Field


class CostModel(BaseModel):
    trading_fee_pct: float = Field(default=0.001, description="Per-trade fee as fraction, scaled by leverage")
    spread_pct: float = Field(default=0.0001, description="Spread cost as fraction, scaled by leverage")
    carry_annual_pct: float = Field(default=0.1095, description="Annual carry cost as fraction, scaled by leverage")

    def order_cost(self, leverage: float) -> float:
        return (self.trading_fee_pct + self.spread_pct) * abs(leverage)

    def carry_cost(self, leverage: float, seconds: float) -> float:
        return self.carry_annual_pct * abs(leverage) * seconds / (365 * 86400)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_trading_costs.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add crunch_node/services/trading/costs.py tests/test_trading_costs.py
git commit -m "feat(trading): add CostModel with fee, spread, and carry"
```

---

### Task 3: TradingSimulator core — apply_order and mark_to_market

**Files:**
- Create: `crunch_node/services/trading/simulator.py`
- Test: `tests/test_trading_simulator.py`

**Step 1: Write the failing test**

```python
from datetime import datetime, UTC
from crunch_node.services.trading.costs import CostModel

def test_open_long_position():
    from crunch_node.services.trading.simulator import TradingSimulator
    sim = TradingSimulator(cost_model=CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0))
    sim.apply_order("model_1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=datetime.now(UTC))
    pos = sim.get_position("model_1", "BTCUSDT")
    assert pos is not None
    assert pos.direction == "long"
    assert pos.leverage == 0.5
    assert pos.entry_price == 50000.0

def test_add_to_existing_position():
    from crunch_node.services.trading.simulator import TradingSimulator
    sim = TradingSimulator(cost_model=CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0))
    now = datetime.now(UTC)
    sim.apply_order("model_1", "BTCUSDT", "long", 0.3, price=50000.0, timestamp=now)
    sim.apply_order("model_1", "BTCUSDT", "long", 0.2, price=51000.0, timestamp=now)
    pos = sim.get_position("model_1", "BTCUSDT")
    assert pos.leverage == 0.5

def test_reduce_position():
    from crunch_node.services.trading.simulator import TradingSimulator
    sim = TradingSimulator(cost_model=CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0))
    now = datetime.now(UTC)
    sim.apply_order("model_1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
    sim.apply_order("model_1", "BTCUSDT", "short", 0.3, price=51000.0, timestamp=now)
    pos = sim.get_position("model_1", "BTCUSDT")
    assert pos.direction == "long"
    assert abs(pos.leverage - 0.2) < 1e-9

def test_close_position_by_opposite_order():
    from crunch_node.services.trading.simulator import TradingSimulator
    sim = TradingSimulator(cost_model=CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0))
    now = datetime.now(UTC)
    sim.apply_order("model_1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
    sim.apply_order("model_1", "BTCUSDT", "short", 0.5, price=51000.0, timestamp=now)
    pos = sim.get_position("model_1", "BTCUSDT")
    assert pos is None
    assert len(sim.get_trades("model_1")) == 1
    assert sim.get_trades("model_1")[0].realized_pnl is not None

def test_overshoot_opens_new_position():
    from crunch_node.services.trading.simulator import TradingSimulator
    sim = TradingSimulator(cost_model=CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0))
    now = datetime.now(UTC)
    sim.apply_order("model_1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
    sim.apply_order("model_1", "BTCUSDT", "short", 0.8, price=51000.0, timestamp=now)
    pos = sim.get_position("model_1", "BTCUSDT")
    assert pos is not None
    assert pos.direction == "short"
    assert abs(pos.leverage - 0.3) < 1e-9

def test_mark_to_market():
    from crunch_node.services.trading.simulator import TradingSimulator
    sim = TradingSimulator(cost_model=CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0))
    now = datetime.now(UTC)
    sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=now)
    sim.mark_to_market("BTCUSDT", 51000.0, now)
    pos = sim.get_position("model_1", "BTCUSDT")
    assert pos.current_price == 51000.0
    assert pos.unrealized_pnl == 1.0 * (51000.0 - 50000.0) / 50000.0

def test_fees_deducted_on_order():
    from crunch_node.services.trading.simulator import TradingSimulator
    sim = TradingSimulator(cost_model=CostModel(trading_fee_pct=0.001, spread_pct=0.0, carry_annual_pct=0.0))
    now = datetime.now(UTC)
    sim.apply_order("model_1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
    snapshot = sim.get_portfolio_snapshot("model_1", now)
    assert snapshot["total_fees"] == 0.001 * 0.5
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_trading_simulator.py -v`
Expected: FAIL with ImportError

**Step 3: Implement TradingSimulator**

This is the largest implementation step. The simulator needs:
- `positions: dict[tuple[str, str], Position]` keyed by (model_id, subject)
- `trades: dict[str, list[Trade]]` keyed by model_id
- `portfolio_fees: dict[str, float]` keyed by model_id
- `portfolio_carry: dict[str, float]` keyed by model_id
- `peak_values: dict[str, float]` keyed by model_id
- `apply_order(model_id, subject, direction, leverage, price, timestamp)`
- `mark_to_market(subject, price, timestamp)`
- `get_position(model_id, subject) -> Position | None`
- `get_trades(model_id) -> list[Trade]`
- `get_portfolio_snapshot(model_id, timestamp) -> dict`

Key logic in `apply_order`:
- If no position exists: open new position
- If same direction: increase leverage (average entry price)
- If opposite direction and leverage < existing: reduce position
- If opposite direction and leverage == existing: close position, record trade
- If opposite direction and leverage > existing: close position, record trade, open new position with remainder

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_trading_simulator.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add crunch_node/services/trading/simulator.py tests/test_trading_simulator.py
git commit -m "feat(trading): TradingSimulator with position tracking and mark-to-market"
```

---

### Task 4: SimulatorSink — feed sink + snapshot writer

**Files:**
- Create: `crunch_node/services/trading/sink.py`
- Test: `tests/test_simulator_sink.py`

**Step 1: Write the failing test**

```python
from datetime import datetime, UTC
from unittest.mock import MagicMock
from crunch_node.feeds import FeedDataRecord

def test_on_record_marks_to_market():
    from crunch_node.services.trading.sink import SimulatorSink
    from crunch_node.services.trading.simulator import TradingSimulator
    from crunch_node.services.trading.costs import CostModel

    sim = TradingSimulator(cost_model=CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0))
    snapshot_repo = MagicMock()
    sink = SimulatorSink(simulator=sim, snapshot_repository=snapshot_repo)

    now = datetime.now(UTC)
    sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=now)

    record = FeedDataRecord(
        source="binance",
        subject="BTCUSDT",
        kind="candle",
        granularity="1m",
        ts_event=int(now.timestamp() * 1000),
        values={"close": 51000.0},
        metadata={},
    )

    import asyncio
    asyncio.run(sink.on_record(record))

    pos = sim.get_position("model_1", "BTCUSDT")
    assert pos.current_price == 51000.0

def test_on_record_writes_snapshot():
    from crunch_node.services.trading.sink import SimulatorSink
    from crunch_node.services.trading.simulator import TradingSimulator
    from crunch_node.services.trading.costs import CostModel

    sim = TradingSimulator(cost_model=CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0))
    snapshot_repo = MagicMock()
    sink = SimulatorSink(simulator=sim, snapshot_repository=snapshot_repo)

    now = datetime.now(UTC)
    sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=now)

    record = FeedDataRecord(
        source="binance",
        subject="BTCUSDT",
        kind="candle",
        granularity="1m",
        ts_event=int(now.timestamp() * 1000),
        values={"close": 51000.0},
        metadata={},
    )

    import asyncio
    asyncio.run(sink.on_record(record))

    snapshot_repo.save.assert_called_once()
    saved = snapshot_repo.save.call_args[0][0]
    assert saved.model_id == "model_1"
    assert saved.result_summary["net_pnl"] > 0

def test_extract_price_from_tick():
    from crunch_node.services.trading.sink import SimulatorSink
    record = FeedDataRecord(
        source="pyth", subject="BTC", kind="tick", granularity="1s",
        ts_event=1000, values={"price": 50000.0}, metadata={},
    )
    assert SimulatorSink.extract_price(record) == 50000.0

def test_extract_price_from_candle():
    from crunch_node.services.trading.sink import SimulatorSink
    record = FeedDataRecord(
        source="binance", subject="BTCUSDT", kind="candle", granularity="1m",
        ts_event=1000, values={"open": 49900, "high": 50100, "low": 49800, "close": 50000.0, "volume": 100}, metadata={},
    )
    assert SimulatorSink.extract_price(record) == 50000.0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_simulator_sink.py -v`
Expected: FAIL with ImportError

**Step 3: Implement SimulatorSink**

The sink needs:
- `on_record(FeedDataRecord)` — extract price, call `simulator.mark_to_market()`, write snapshots
- `extract_price(record)` — get price from `values["close"]` or `values["price"]`
- Write one `SnapshotRecord` per model that has positions, using `snapshot_repository.save()`

Reference existing SnapshotRecord: `crunch_node/entities/prediction.py:97`

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_simulator_sink.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add crunch_node/services/trading/sink.py tests/test_simulator_sink.py
git commit -m "feat(trading): SimulatorSink as feed sink with snapshot writing"
```

---

### Task 5: post_predict_hook wiring

**Files:**
- Test: `tests/test_simulator_hook.py`

**Step 1: Write the failing test**

```python
from datetime import datetime, UTC
from crunch_node.entities.prediction import PredictionRecord, InputRecord, PredictionStatus

def test_hook_forwards_signal_to_simulator():
    from crunch_node.services.trading.sink import SimulatorSink
    from crunch_node.services.trading.simulator import TradingSimulator
    from crunch_node.services.trading.costs import CostModel
    from unittest.mock import MagicMock

    sim = TradingSimulator(cost_model=CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0))
    sink = SimulatorSink(simulator=sim, snapshot_repository=MagicMock())

    now = datetime.now(UTC)
    inp = InputRecord(id="INP_1", raw_data={"close": 50000.0}, received_at=now)

    predictions = [
        PredictionRecord(
            id="PRED_1",
            model_id="model_1",
            input_id="INP_1",
            scope_key="trading-btcusdt",
            scope={"subject": "BTCUSDT"},
            inference_output={"direction": "long", "leverage": 0.5},
            status=PredictionStatus.PENDING,
            performed_at=now,
            resolvable_at=now,
        ),
    ]

    # Call the hook
    result = sink.on_predictions(predictions, inp, now)

    pos = sim.get_position("model_1", "BTCUSDT")
    assert pos is not None
    assert pos.direction == "long"
    assert pos.leverage == 0.5
    assert result == predictions  # hook must return predictions unchanged

def test_hook_extracts_price_from_input():
    from crunch_node.services.trading.sink import SimulatorSink
    from crunch_node.services.trading.simulator import TradingSimulator
    from crunch_node.services.trading.costs import CostModel
    from unittest.mock import MagicMock

    sim = TradingSimulator(cost_model=CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0))
    sink = SimulatorSink(simulator=sim, snapshot_repository=MagicMock())

    now = datetime.now(UTC)
    inp = InputRecord(id="INP_1", raw_data={"close": 50000.0}, received_at=now)

    predictions = [
        PredictionRecord(
            id="PRED_1",
            model_id="model_1",
            input_id="INP_1",
            scope_key="trading-btcusdt",
            scope={"subject": "BTCUSDT"},
            inference_output={"direction": "long", "leverage": 0.5},
            status=PredictionStatus.PENDING,
            performed_at=now,
            resolvable_at=now,
        ),
    ]

    sink.on_predictions(predictions, inp, now)
    pos = sim.get_position("model_1", "BTCUSDT")
    assert pos.entry_price == 50000.0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_simulator_hook.py -v`
Expected: FAIL — `on_predictions` method not found

**Step 3: Add `on_predictions` to SimulatorSink**

`on_predictions` is the method wired as `post_predict_hook`. It receives `(predictions, input_record, now)`, extracts direction/leverage from each prediction's `inference_output`, extracts price from `input_record.raw_data`, and calls `simulator.apply_order()`. Returns predictions unchanged.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_simulator_hook.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_simulator_hook.py crunch_node/services/trading/sink.py
git commit -m "feat(trading): post_predict_hook wiring for order forwarding"
```

---

### Task 6: Trading pack CrunchConfig wiring

**Files:**
- Modify: `packs/trading/node/config/crunch_config.py`
- Test: `tests/test_trading_config_wiring.py`

**Step 1: Write the failing test**

```python
def test_trading_config_has_cost_model():
    from packs.trading.node.config.crunch_config import CrunchConfig
    config = CrunchConfig()
    assert hasattr(config, "cost_model")
    assert config.cost_model.trading_fee_pct > 0

def test_trading_config_aggregation_uses_net_pnl():
    from packs.trading.node.config.crunch_config import CrunchConfig
    config = CrunchConfig()
    assert config.aggregation.value_field == "net_pnl"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_trading_config_wiring.py -v`
Expected: FAIL — no cost_model attribute

**Step 3: Update trading pack CrunchConfig**

Add `cost_model` field. Update `aggregation.value_field` to `"net_pnl"`. The `post_predict_hook` wiring and `SimulatorSink` addition to feed sinks happens in the worker bootstrap (Task 7), not in the config — since the config is declarative and the sink needs runtime dependencies.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_trading_config_wiring.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add packs/trading/node/config/crunch_config.py tests/test_trading_config_wiring.py
git commit -m "feat(trading): add CostModel to trading pack config"
```

---

### Task 7: Worker bootstrap — wire SimulatorSink into predict_worker

**Files:**
- Modify: `crunch_node/workers/predict_worker.py:109-155`
- Test: `tests/test_predict_worker_simulator.py`

**Step 1: Write the failing test**

```python
def test_simulator_sink_added_when_cost_model_present():
    """When CrunchConfig has a cost_model, predict_worker should add SimulatorSink."""
    from unittest.mock import MagicMock, patch

    # Verify that the worker bootstrap creates a SimulatorSink
    # when the config has a cost_model attribute
    from crunch_node.services.trading.costs import CostModel
    config = MagicMock()
    config.cost_model = CostModel()

    from crunch_node.services.trading.sink import SimulatorSink
    # Test that SimulatorSink can be constructed with config's cost_model
    sim_sink = SimulatorSink.from_config(config)
    assert sim_sink is not None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_predict_worker_simulator.py -v`
Expected: FAIL

**Step 3: Implement worker wiring**

In `predict_worker.py:main()`, after building `predict_sink`, check if `config` has a `cost_model`. If so:
1. Create `TradingSimulator` with the cost model
2. Create `SimulatorSink` wrapping the simulator + snapshot repository
3. Add `SimulatorSink` to the sinks list
4. Wire `simulator_sink.on_predictions` as `post_predict_hook` on the predict service

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_predict_worker_simulator.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add crunch_node/workers/predict_worker.py tests/test_predict_worker_simulator.py
git commit -m "feat(trading): wire SimulatorSink into predict worker when cost_model present"
```

---

### Task 8: Integration test — full tick-to-snapshot flow

**Files:**
- Test: `tests/test_trading_integration.py`

**Step 1: Write the integration test**

End-to-end test: create a simulator with a sink, apply an order, send a tick, verify a SnapshotRecord is written with correct P&L values. Then send an opposite order to close, verify realized P&L in the next snapshot.

```python
def test_full_flow_order_tick_snapshot_close():
    """Open position → tick → snapshot with unrealized P&L → close → snapshot with realized P&L."""
    from crunch_node.services.trading.simulator import TradingSimulator
    from crunch_node.services.trading.sink import SimulatorSink
    from crunch_node.services.trading.costs import CostModel
    from unittest.mock import MagicMock
    from datetime import datetime, UTC

    sim = TradingSimulator(cost_model=CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0))
    snapshot_repo = MagicMock()
    sink = SimulatorSink(simulator=sim, snapshot_repository=snapshot_repo)

    now = datetime.now(UTC)

    # 1. Open long
    sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=now)

    # 2. Price moves up — tick
    from crunch_node.feeds import FeedDataRecord
    record = FeedDataRecord(
        source="binance", subject="BTCUSDT", kind="candle", granularity="1m",
        ts_event=int(now.timestamp() * 1000), values={"close": 51000.0}, metadata={},
    )
    import asyncio
    asyncio.run(sink.on_record(record))

    # 3. Verify snapshot has unrealized P&L
    snapshot_repo.save.assert_called()
    snap = snapshot_repo.save.call_args[0][0]
    assert snap.result_summary["unrealized_pnl"] > 0
    assert snap.result_summary["realized_pnl"] == 0

    snapshot_repo.reset_mock()

    # 4. Close position
    sim.apply_order("model_1", "BTCUSDT", "short", 1.0, price=51000.0, timestamp=now)

    # 5. Another tick
    record2 = FeedDataRecord(
        source="binance", subject="BTCUSDT", kind="candle", granularity="1m",
        ts_event=int(now.timestamp() * 1000), values={"close": 51000.0}, metadata={},
    )
    asyncio.run(sink.on_record(record2))

    # 6. Verify snapshot has realized P&L, no unrealized
    snap2 = snapshot_repo.save.call_args[0][0]
    assert snap2.result_summary["realized_pnl"] > 0
    assert snap2.result_summary["unrealized_pnl"] == 0
    assert snap2.result_summary["open_position_count"] == 0
```

**Step 2: Run test**

Run: `uv run pytest tests/test_trading_integration.py -v`
Expected: PASS (if all prior tasks are complete)

**Step 3: Commit**

```bash
git add tests/test_trading_integration.py
git commit -m "test(trading): integration test for full tick-to-snapshot flow"
```

---

### Task 9: Carry cost accrual on tick

**Files:**
- Modify: `crunch_node/services/trading/simulator.py`
- Test: `tests/test_trading_carry.py`

**Step 1: Write the failing test**

```python
from datetime import datetime, timedelta, UTC

def test_carry_cost_accrues_on_mark_to_market():
    from crunch_node.services.trading.simulator import TradingSimulator
    from crunch_node.services.trading.costs import CostModel

    sim = TradingSimulator(cost_model=CostModel(
        trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.1095,
    ))
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = t0 + timedelta(days=1)

    sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=t0)
    sim.mark_to_market("BTCUSDT", 50000.0, t1)

    pos = sim.get_position("model_1", "BTCUSDT")
    assert abs(pos.accrued_carry - 0.0003) < 0.0001

    snapshot = sim.get_portfolio_snapshot("model_1", t1)
    assert snapshot["total_carry_costs"] > 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_trading_carry.py -v`
Expected: FAIL — carry not accrued

**Step 3: Add carry accrual to `mark_to_market`**

Track `last_mark_at` per position. On each mark, compute elapsed seconds and call `cost_model.carry_cost(leverage, elapsed_seconds)`. Add to `position.accrued_carry`.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_trading_carry.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add crunch_node/services/trading/simulator.py tests/test_trading_carry.py
git commit -m "feat(trading): accrue carry costs on mark-to-market"
```

---

### Task 10: Leverage limits

**Files:**
- Modify: `crunch_node/services/trading/simulator.py`
- Modify: `crunch_node/services/trading/costs.py` (add limits to CostModel or separate config)
- Test: `tests/test_trading_leverage_limits.py`

**Step 1: Write the failing test**

```python
def test_position_leverage_clamped():
    from crunch_node.services.trading.simulator import TradingSimulator
    from crunch_node.services.trading.costs import CostModel
    from datetime import datetime, UTC

    sim = TradingSimulator(
        cost_model=CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0),
        max_position_leverage=2.5,
    )
    now = datetime.now(UTC)
    sim.apply_order("model_1", "BTCUSDT", "long", 3.0, price=50000.0, timestamp=now)
    pos = sim.get_position("model_1", "BTCUSDT")
    assert pos.leverage == 2.5

def test_portfolio_leverage_clamped():
    from crunch_node.services.trading.simulator import TradingSimulator
    from crunch_node.services.trading.costs import CostModel
    from datetime import datetime, UTC

    sim = TradingSimulator(
        cost_model=CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0),
        max_portfolio_leverage=5.0,
    )
    now = datetime.now(UTC)
    sim.apply_order("model_1", "BTCUSDT", "long", 2.5, price=50000.0, timestamp=now)
    sim.apply_order("model_1", "ETHUSDT", "long", 2.5, price=3000.0, timestamp=now)
    sim.apply_order("model_1", "SOLUSD", "long", 1.0, price=100.0, timestamp=now)
    # Total would be 6.0, but clamped to 5.0 — last order reduced
    total = sum(
        p.leverage for p in sim.get_all_positions("model_1")
    )
    assert total <= 5.0
```

**Step 2-5: Implement, test, commit**

Run: `uv run pytest tests/test_trading_leverage_limits.py -v`

```bash
git add crunch_node/services/trading/simulator.py tests/test_trading_leverage_limits.py
git commit -m "feat(trading): position and portfolio leverage limits"
```

---

### Task 11: Target mode signal interpretation

**Files:**
- Modify: `crunch_node/services/trading/sink.py`
- Test: `tests/test_trading_target_mode.py`

**Step 1: Write the failing test**

```python
def test_target_mode_opens_position():
    from crunch_node.services.trading.sink import SimulatorSink
    from crunch_node.services.trading.simulator import TradingSimulator
    from crunch_node.services.trading.costs import CostModel
    from unittest.mock import MagicMock
    from datetime import datetime, UTC

    sim = TradingSimulator(cost_model=CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0))
    sink = SimulatorSink(simulator=sim, snapshot_repository=MagicMock(), signal_mode="target")

    # signal=0.7 → target 70% long from flat
    sink.apply_signal("model_1", "BTCUSDT", {"signal": 0.7}, price=50000.0, timestamp=datetime.now(UTC))
    pos = sim.get_position("model_1", "BTCUSDT")
    assert pos.direction == "long"
    assert abs(pos.leverage - 0.7) < 1e-9

def test_target_mode_adjusts_position():
    from crunch_node.services.trading.sink import SimulatorSink
    from crunch_node.services.trading.simulator import TradingSimulator
    from crunch_node.services.trading.costs import CostModel
    from unittest.mock import MagicMock
    from datetime import datetime, UTC

    sim = TradingSimulator(cost_model=CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0))
    sink = SimulatorSink(simulator=sim, snapshot_repository=MagicMock(), signal_mode="target")

    now = datetime.now(UTC)
    sink.apply_signal("model_1", "BTCUSDT", {"signal": 0.7}, price=50000.0, timestamp=now)
    sink.apply_signal("model_1", "BTCUSDT", {"signal": 0.3}, price=51000.0, timestamp=now)
    pos = sim.get_position("model_1", "BTCUSDT")
    assert pos.direction == "long"
    assert abs(pos.leverage - 0.3) < 1e-9

def test_target_mode_zero_closes():
    from crunch_node.services.trading.sink import SimulatorSink
    from crunch_node.services.trading.simulator import TradingSimulator
    from crunch_node.services.trading.costs import CostModel
    from unittest.mock import MagicMock
    from datetime import datetime, UTC

    sim = TradingSimulator(cost_model=CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0))
    sink = SimulatorSink(simulator=sim, snapshot_repository=MagicMock(), signal_mode="target")

    now = datetime.now(UTC)
    sink.apply_signal("model_1", "BTCUSDT", {"signal": 0.7}, price=50000.0, timestamp=now)
    sink.apply_signal("model_1", "BTCUSDT", {"signal": 0.0}, price=51000.0, timestamp=now)
    pos = sim.get_position("model_1", "BTCUSDT")
    assert pos is None
```

**Step 2-5: Implement, test, commit**

Target mode translates the signal into the order(s) needed to reach the target position. The `apply_signal` method computes the delta between current position and target, then calls `simulator.apply_order()`.

Run: `uv run pytest tests/test_trading_target_mode.py -v`

```bash
git add crunch_node/services/trading/sink.py tests/test_trading_target_mode.py
git commit -m "feat(trading): target mode signal interpretation"
```

---

### Task 12: Run full test suite

**Step 1: Run all tests**

```bash
uv run pytest tests/ -x -q
```

Verify no regressions. Fix any failures.

**Step 2: Run trading-specific tests**

```bash
uv run pytest tests/test_trading*.py tests/test_simulator*.py -v
```

**Step 3: Commit any fixes**

```bash
git commit -m "fix: resolve test issues from trading simulator integration"
```

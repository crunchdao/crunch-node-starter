# Trading State Persistence & Score Service Integration

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Persist trading simulator state to DB so the Score service (separate process) can pull portfolio metrics and create snapshots, and the simulator can recover from crashes.

**Architecture:** A new `trading_portfolio_state` DB table stores per-model portfolio state (positions, trades, accumulators) as JSONB. The SimulatorSink persists state after each tick/order instead of writing snapshots. The Score service reads persisted state and creates SnapshotRecords — same pattern as non-trading competitions. On startup, TradingSimulator loads from DB for crash recovery.

**Tech Stack:** SQLModel, PostgreSQL JSONB, existing repository pattern

---

### Task 1: TradingStateRow table definition

**Files:**
- Create: `crunch_node/db/tables/trading.py`
- Modify: `crunch_node/db/tables/__init__.py`
- Test: `tests/test_trading_state_table.py`

**Step 1: Write the failing test**

```python
# tests/test_trading_state_table.py
from __future__ import annotations

from datetime import UTC, datetime


class TestTradingStateRow:
    def test_row_creation(self):
        from crunch_node.db.tables.trading import TradingStateRow

        row = TradingStateRow(
            model_id="model_1",
            positions_jsonb=[
                {
                    "subject": "BTCUSDT",
                    "direction": "long",
                    "leverage": 0.5,
                    "entry_price": 50000.0,
                    "opened_at": "2026-01-01T00:00:00+00:00",
                    "current_price": 51000.0,
                    "accrued_carry": 0.001,
                }
            ],
            trades_jsonb=[],
            portfolio_fees=0.0005,
            closed_carry=0.0,
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert row.model_id == "model_1"
        assert len(row.positions_jsonb) == 1
        assert row.portfolio_fees == 0.0005

    def test_row_has_table_name(self):
        from crunch_node.db.tables.trading import TradingStateRow

        assert TradingStateRow.__tablename__ == "trading_portfolio_state"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_trading_state_table.py -v`
Expected: FAIL with ImportError

**Step 3: Write minimal implementation**

`crunch_node/db/tables/trading.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class TradingStateRow(SQLModel, table=True):
    __tablename__ = "trading_portfolio_state"

    model_id: str = Field(primary_key=True)

    positions_jsonb: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB),
    )
    trades_jsonb: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB),
    )
    portfolio_fees: float = Field(default=0.0)
    closed_carry: float = Field(default=0.0)

    updated_at: datetime = Field(default_factory=utc_now, index=True)
```

Add to `crunch_node/db/tables/__init__.py`:

```python
from crunch_node.db.tables.trading import TradingStateRow
```

And add `"TradingStateRow"` to `__all__`.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_trading_state_table.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add crunch_node/db/tables/trading.py crunch_node/db/tables/__init__.py tests/test_trading_state_table.py
git commit -m "feat(trading): add TradingStateRow table for portfolio state persistence"
```

---

### Task 2: TradingStateRepository — save and load

**Files:**
- Create: `crunch_node/db/trading_state_repository.py`
- Test: `tests/test_trading_state_repository.py`

**Step 1: Write the failing test**

```python
# tests/test_trading_state_repository.py
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from crunch_node.services.trading.models import Position, Trade


class TestTradingStateRepository:
    def test_save_state(self):
        from crunch_node.db.trading_state_repository import TradingStateRepository

        session = MagicMock()
        session.get.return_value = None
        repo = TradingStateRepository(session)

        positions = [
            Position(
                model_id="m1", subject="BTCUSDT", direction="long",
                leverage=0.5, entry_price=50000.0,
                opened_at=datetime(2026, 1, 1, tzinfo=UTC),
                current_price=51000.0, accrued_carry=0.001,
            )
        ]
        trades = [
            Trade(
                model_id="m1", subject="BTCUSDT", direction="long",
                leverage=0.3, entry_price=49000.0,
                opened_at=datetime(2026, 1, 1, tzinfo=UTC),
                exit_price=50000.0, closed_at=datetime(2026, 1, 2, tzinfo=UTC),
                realized_pnl=0.006, fees_paid=0.0003,
            )
        ]

        repo.save_state("m1", positions, trades, portfolio_fees=0.001, closed_carry=0.0002)

        session.add.assert_called_once()
        session.commit.assert_called_once()

    def test_load_state_returns_none_when_missing(self):
        from crunch_node.db.trading_state_repository import TradingStateRepository

        session = MagicMock()
        session.get.return_value = None
        repo = TradingStateRepository(session)

        result = repo.load_state("m1")
        assert result is None

    def test_load_state_returns_dict(self):
        from crunch_node.db.trading_state_repository import TradingStateRepository
        from crunch_node.db.tables.trading import TradingStateRow

        row = TradingStateRow(
            model_id="m1",
            positions_jsonb=[{
                "subject": "BTCUSDT", "direction": "long", "leverage": 0.5,
                "entry_price": 50000.0, "opened_at": "2026-01-01T00:00:00+00:00",
                "current_price": 51000.0, "accrued_carry": 0.001,
            }],
            trades_jsonb=[],
            portfolio_fees=0.001,
            closed_carry=0.0,
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session = MagicMock()
        session.get.return_value = row
        repo = TradingStateRepository(session)

        result = repo.load_state("m1")
        assert result is not None
        assert result["model_id"] == "m1"
        assert len(result["positions"]) == 1
        assert result["portfolio_fees"] == 0.001

    def test_load_all_model_ids(self):
        from crunch_node.db.trading_state_repository import TradingStateRepository

        session = MagicMock()
        repo = TradingStateRepository(session)

        mock_result = MagicMock()
        mock_result.all.return_value = [("m1",), ("m2",)]
        session.exec.return_value = mock_result

        ids = repo.get_all_model_ids()
        assert ids == ["m1", "m2"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_trading_state_repository.py -v`
Expected: FAIL with ImportError

**Step 3: Write minimal implementation**

```python
# crunch_node/db/trading_state_repository.py
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from crunch_node.db.tables.trading import TradingStateRow
from crunch_node.services.trading.models import Position, Trade


class TradingStateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save_state(
        self,
        model_id: str,
        positions: list[Position],
        trades: list[Trade],
        portfolio_fees: float,
        closed_carry: float,
    ) -> None:
        positions_data = [
            {
                "subject": p.subject,
                "direction": p.direction,
                "leverage": p.leverage,
                "entry_price": p.entry_price,
                "opened_at": p.opened_at.isoformat(),
                "current_price": p.current_price,
                "accrued_carry": p.accrued_carry,
            }
            for p in positions
        ]
        trades_data = [
            {
                "subject": t.subject,
                "direction": t.direction,
                "leverage": t.leverage,
                "entry_price": t.entry_price,
                "opened_at": t.opened_at.isoformat(),
                "exit_price": t.exit_price,
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                "realized_pnl": t.realized_pnl,
                "fees_paid": t.fees_paid,
            }
            for t in trades
        ]

        existing = self._session.get(TradingStateRow, model_id)
        if existing is None:
            row = TradingStateRow(
                model_id=model_id,
                positions_jsonb=positions_data,
                trades_jsonb=trades_data,
                portfolio_fees=portfolio_fees,
                closed_carry=closed_carry,
                updated_at=datetime.now(UTC),
            )
            self._session.add(row)
        else:
            existing.positions_jsonb = positions_data
            existing.trades_jsonb = trades_data
            existing.portfolio_fees = portfolio_fees
            existing.closed_carry = closed_carry
            existing.updated_at = datetime.now(UTC)

        self._session.commit()

    def load_state(self, model_id: str) -> dict[str, Any] | None:
        row = self._session.get(TradingStateRow, model_id)
        if row is None:
            return None
        return {
            "model_id": row.model_id,
            "positions": row.positions_jsonb,
            "trades": row.trades_jsonb,
            "portfolio_fees": row.portfolio_fees,
            "closed_carry": row.closed_carry,
            "updated_at": row.updated_at,
        }

    def get_all_model_ids(self) -> list[str]:
        stmt = select(TradingStateRow.model_id)
        result = self._session.exec(stmt).all()
        return [r[0] if isinstance(r, tuple) else r for r in result]
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_trading_state_repository.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add crunch_node/db/trading_state_repository.py tests/test_trading_state_repository.py
git commit -m "feat(trading): add TradingStateRepository for state persistence"
```

---

### Task 3: TradingSimulator — serialize/deserialize state

**Files:**
- Modify: `crunch_node/services/trading/simulator.py`
- Test: `tests/test_trading_serialization.py`

**Step 1: Write the failing test**

```python
# tests/test_trading_serialization.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from crunch_node.services.trading.costs import CostModel
from crunch_node.services.trading.simulator import TradingSimulator

ZERO_COST = CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0)


class TestSimulatorSerialization:
    def test_get_full_state(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        sim.apply_order("m1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        sim.apply_order("m1", "ETHUSDT", "short", 0.3, price=3000.0, timestamp=now)

        state = sim.get_full_state("m1")
        assert len(state["positions"]) == 2
        assert state["portfolio_fees"] >= 0
        assert state["closed_carry"] >= 0

    def test_load_state_restores_positions(self):
        sim1 = TradingSimulator(cost_model=ZERO_COST)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        sim1.apply_order("m1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        state = sim1.get_full_state("m1")

        sim2 = TradingSimulator(cost_model=ZERO_COST)
        sim2.load_state("m1", state)

        pos = sim2.get_position("m1", "BTCUSDT")
        assert pos is not None
        assert pos.direction == "long"
        assert pos.leverage == pytest.approx(0.5)
        assert pos.entry_price == pytest.approx(50000.0)

    def test_load_state_restores_trades(self):
        sim1 = TradingSimulator(cost_model=ZERO_COST)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        sim1.apply_order("m1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        sim1.apply_order("m1", "BTCUSDT", "short", 0.5, price=51000.0, timestamp=now)
        state = sim1.get_full_state("m1")

        sim2 = TradingSimulator(cost_model=ZERO_COST)
        sim2.load_state("m1", state)

        trades = sim2.get_trades("m1")
        assert len(trades) == 1
        assert trades[0].realized_pnl is not None

    def test_load_state_restores_accumulators(self):
        sim1 = TradingSimulator(cost_model=CostModel(
            trading_fee_pct=0.001, spread_pct=0.0, carry_annual_pct=0.0,
        ))
        now = datetime(2026, 1, 1, tzinfo=UTC)
        sim1.apply_order("m1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        state = sim1.get_full_state("m1")

        sim2 = TradingSimulator(cost_model=ZERO_COST)
        sim2.load_state("m1", state)

        snapshot = sim2.get_portfolio_snapshot("m1", now)
        assert snapshot["total_fees"] == pytest.approx(0.001 * 0.5)

    def test_roundtrip_preserves_snapshot(self):
        sim1 = TradingSimulator(cost_model=ZERO_COST)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        sim1.apply_order("m1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        sim1.mark_to_market("BTCUSDT", 51000.0, now)
        snap1 = sim1.get_portfolio_snapshot("m1", now)

        state = sim1.get_full_state("m1")
        sim2 = TradingSimulator(cost_model=ZERO_COST)
        sim2.load_state("m1", state)
        snap2 = sim2.get_portfolio_snapshot("m1", now)

        assert snap2["net_pnl"] == pytest.approx(snap1["net_pnl"])
        assert snap2["total_unrealized_pnl"] == pytest.approx(snap1["total_unrealized_pnl"])
        assert snap2["open_position_count"] == snap1["open_position_count"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_trading_serialization.py -v`
Expected: FAIL — `get_full_state` not found

**Step 3: Add `get_full_state` and `load_state` to TradingSimulator**

Add these methods to `crunch_node/services/trading/simulator.py`:

```python
def get_full_state(self, model_id: str) -> dict[str, Any]:
    """Return full serializable state for persistence."""
    positions = self.get_all_positions(model_id)
    trades = self.get_trades(model_id)
    return {
        "positions": positions,
        "trades": trades,
        "portfolio_fees": self._portfolio_fees.get(model_id, 0.0),
        "closed_carry": self._closed_carry.get(model_id, 0.0),
    }

def load_state(self, model_id: str, state: dict[str, Any]) -> None:
    """Restore simulator state from persistence."""
    for pos_data in state.get("positions", []):
        if isinstance(pos_data, Position):
            pos = pos_data
        else:
            pos = Position(
                model_id=model_id,
                subject=pos_data["subject"],
                direction=pos_data["direction"],
                leverage=pos_data["leverage"],
                entry_price=pos_data["entry_price"],
                opened_at=(
                    datetime.fromisoformat(pos_data["opened_at"])
                    if isinstance(pos_data["opened_at"], str)
                    else pos_data["opened_at"]
                ),
                current_price=pos_data.get("current_price", 0.0),
                accrued_carry=pos_data.get("accrued_carry", 0.0),
            )
        key = (model_id, pos.subject)
        self._positions[key] = pos
        self._last_mark_at[key] = pos.opened_at

    for trade_data in state.get("trades", []):
        if isinstance(trade_data, Trade):
            trade = trade_data
        else:
            opened_at = trade_data["opened_at"]
            if isinstance(opened_at, str):
                opened_at = datetime.fromisoformat(opened_at)
            closed_at = trade_data.get("closed_at")
            if isinstance(closed_at, str):
                closed_at = datetime.fromisoformat(closed_at)

            trade = Trade(
                model_id=model_id,
                subject=trade_data["subject"],
                direction=trade_data["direction"],
                leverage=trade_data["leverage"],
                entry_price=trade_data["entry_price"],
                opened_at=opened_at,
                exit_price=trade_data.get("exit_price"),
                closed_at=closed_at,
                realized_pnl=trade_data.get("realized_pnl"),
                fees_paid=trade_data.get("fees_paid", 0.0),
            )
        self._trades.setdefault(model_id, []).append(trade)

    self._portfolio_fees[model_id] = state.get("portfolio_fees", 0.0)
    self._closed_carry[model_id] = state.get("closed_carry", 0.0)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_trading_serialization.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add crunch_node/services/trading/simulator.py tests/test_trading_serialization.py
git commit -m "feat(trading): add get_full_state/load_state for simulator persistence"
```

---

### Task 4: SimulatorSink — replace snapshot writing with state persistence

**Files:**
- Modify: `crunch_node/services/trading/sink.py`
- Test: `tests/test_simulator_sink.py` (update existing tests)
- Test: `tests/test_trading_integration.py` (update existing tests)

**Step 1: Write the failing test**

```python
# Add to tests/test_simulator_sink.py — new test class

class TestStatePersistence:
    def test_on_record_persists_state(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        state_repo = MagicMock()
        sink = SimulatorSink(
            simulator=sim,
            state_repository=state_repo,
            model_ids=["model_1"],
        )

        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=now)

        record = FeedDataRecord(
            source="binance", subject="BTCUSDT", kind="candle", granularity="1m",
            ts_event=int(now.timestamp() * 1000), values={"close": 51000.0},
        )
        asyncio.run(sink.on_record(record))

        state_repo.save_state.assert_called_once()

    def test_on_record_does_not_write_snapshots(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        state_repo = MagicMock()
        sink = SimulatorSink(
            simulator=sim,
            state_repository=state_repo,
            model_ids=["model_1"],
        )

        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=now)

        record = FeedDataRecord(
            source="binance", subject="BTCUSDT", kind="candle", granularity="1m",
            ts_event=int(now.timestamp() * 1000), values={"close": 51000.0},
        )
        asyncio.run(sink.on_record(record))

        # No snapshot_repository, no save call on it
        assert not hasattr(sink, '_snapshot_repository')
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_simulator_sink.py::TestStatePersistence -v`
Expected: FAIL

**Step 3: Update SimulatorSink**

Replace `snapshot_repository` parameter with `state_repository`. Replace `_write_snapshots()` with `_persist_state()`. The `_persist_state` method calls `state_repository.save_state()` for each tracked model_id.

Key changes to `crunch_node/services/trading/sink.py`:

```python
class SimulatorSink:
    def __init__(
        self,
        simulator: TradingSimulator,
        state_repository: Any,
        model_ids: list[str] | None = None,
        signal_mode: Literal["delta", "target"] = "delta",
    ) -> None:
        self._simulator = simulator
        self._state_repository = state_repository
        self._model_ids = model_ids or []
        self._signal_mode = signal_mode

    async def on_record(self, record: FeedDataRecord) -> None:
        price = self.extract_price(record)
        if price is None:
            return
        ts = datetime.fromtimestamp(record.ts_event / 1000, tz=UTC)
        self._simulator.mark_to_market(record.subject, price, ts)
        self._persist_state()

    # ... extract_price, on_predictions, apply_signal stay the same ...
    # ... but on_predictions should also call _persist_state() after processing orders ...

    def _persist_state(self) -> None:
        for model_id in self._model_ids:
            state = self._simulator.get_full_state(model_id)
            self._state_repository.save_state(
                model_id,
                state["positions"],
                state["trades"],
                portfolio_fees=state["portfolio_fees"],
                closed_carry=state["closed_carry"],
            )
```

Remove `_write_snapshots()` entirely. Remove `uuid` import.

**Step 4: Update existing tests**

Update all tests in `tests/test_simulator_sink.py`, `tests/test_simulator_hook.py`, and `tests/test_trading_integration.py` to use `state_repository=MagicMock()` instead of `snapshot_repository=MagicMock()`. The integration tests that asserted `snapshot_repo.save.call_args[0][0].result_summary` need to be changed to assert on `state_repo.save_state` calls instead.

**Step 5: Run all trading tests to verify**

Run: `uv run pytest tests/test_simulator_sink.py tests/test_simulator_hook.py tests/test_trading_integration.py tests/test_trading_target_mode.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add crunch_node/services/trading/sink.py tests/test_simulator_sink.py tests/test_simulator_hook.py tests/test_trading_integration.py tests/test_trading_target_mode.py
git commit -m "refactor(trading): replace snapshot writing with state persistence in SimulatorSink"
```

---

### Task 5: Score service — trading-aware scoring path

**Files:**
- Modify: `crunch_node/services/score.py`
- Test: `tests/test_score_trading.py`

**Step 1: Write the failing test**

```python
# tests/test_score_trading.py
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from crunch_node.services.score import ScoreService


class TestTradingScoring:
    def _make_service(self, trading_state_repo=None):
        return ScoreService(
            checkpoint_interval_seconds=300,
            scoring_function=lambda p, g: MagicMock(model_dump=lambda: {"value": 0}),
            snapshot_repository=MagicMock(),
            model_repository=MagicMock(fetch_all=MagicMock(return_value={})),
            leaderboard_repository=MagicMock(),
            prediction_repository=MagicMock(find=MagicMock(return_value=[])),
            trading_state_repository=trading_state_repo,
        )

    def test_trading_score_reads_portfolio_state(self):
        state_repo = MagicMock()
        state_repo.get_all_model_ids.return_value = ["m1"]
        state_repo.load_state.return_value = {
            "model_id": "m1",
            "positions": [
                {
                    "subject": "BTCUSDT", "direction": "long", "leverage": 0.5,
                    "entry_price": 50000.0, "opened_at": "2026-01-01T00:00:00+00:00",
                    "current_price": 51000.0, "accrued_carry": 0.0,
                }
            ],
            "trades": [],
            "portfolio_fees": 0.0005,
            "closed_carry": 0.0,
            "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
        }

        service = self._make_service(trading_state_repo=state_repo)
        result = service.score_and_snapshot()

        assert result is True
        service.snapshot_repository.save.assert_called_once()
        snap = service.snapshot_repository.save.call_args[0][0]
        assert snap.model_id == "m1"
        assert "net_pnl" in snap.result_summary

    def test_no_trading_state_falls_through_to_prediction_scoring(self):
        service = self._make_service(trading_state_repo=None)
        result = service.score_and_snapshot()
        assert result is False  # no predictions to score

    def test_trading_snapshot_contains_portfolio_metrics(self):
        state_repo = MagicMock()
        state_repo.get_all_model_ids.return_value = ["m1"]
        state_repo.load_state.return_value = {
            "model_id": "m1",
            "positions": [
                {
                    "subject": "BTCUSDT", "direction": "long", "leverage": 1.0,
                    "entry_price": 50000.0, "opened_at": "2026-01-01T00:00:00+00:00",
                    "current_price": 51000.0, "accrued_carry": 0.001,
                }
            ],
            "trades": [
                {
                    "subject": "ETHUSDT", "direction": "short", "leverage": 0.3,
                    "entry_price": 3000.0, "opened_at": "2026-01-01T00:00:00+00:00",
                    "exit_price": 2900.0, "closed_at": "2026-01-02T00:00:00+00:00",
                    "realized_pnl": 0.01, "fees_paid": 0.0003,
                }
            ],
            "portfolio_fees": 0.001,
            "closed_carry": 0.0002,
            "updated_at": datetime(2026, 1, 2, tzinfo=UTC),
        }

        service = self._make_service(trading_state_repo=state_repo)
        service.score_and_snapshot()

        snap = service.snapshot_repository.save.call_args[0][0]
        summary = snap.result_summary
        assert "unrealized_pnl" in summary
        assert "realized_pnl" in summary
        assert "total_fees" in summary
        assert "total_carry_costs" in summary
        assert "open_position_count" in summary
        assert "net_pnl" in summary
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_score_trading.py -v`
Expected: FAIL — `trading_state_repository` not accepted

**Step 3: Add trading-aware path to ScoreService**

Add `trading_state_repository` parameter to `ScoreService.__init__()`. Modify `score_and_snapshot()` to check for trading state first — if `trading_state_repository` is present and has data, create snapshots from portfolio state instead of scoring predictions.

Add to `ScoreService.__init__`:
```python
self.trading_state_repository = kwargs.pop("trading_state_repository", None)
```

Add new method `_score_trading` and modify `score_and_snapshot`:

```python
def score_and_snapshot(self) -> bool:
    now = datetime.now(UTC)

    # Trading-aware path: read portfolio state, write snapshots
    if self.trading_state_repository is not None:
        return self._score_trading(now)

    # Standard path: score predictions
    scored = self._score_predictions(now)
    # ... rest unchanged ...

def _score_trading(self, now: datetime) -> bool:
    """Create snapshots from persisted trading simulator state."""
    model_ids = self.trading_state_repository.get_all_model_ids()
    if not model_ids:
        return False

    from crunch_node.services.trading.models import Position

    written_snapshots = []
    for model_id in model_ids:
        state = self.trading_state_repository.load_state(model_id)
        if state is None:
            continue

        positions_data = state.get("positions", [])
        trades_data = state.get("trades", [])
        portfolio_fees = state.get("portfolio_fees", 0.0)
        closed_carry = state.get("closed_carry", 0.0)

        # Reconstruct portfolio metrics from persisted state
        total_unrealized = 0.0
        for p in positions_data:
            entry = p["entry_price"]
            current = p.get("current_price", entry)
            leverage = p["leverage"]
            if entry > 0:
                price_return = (current - entry) / entry
                if p["direction"] == "short":
                    price_return = -price_return
                total_unrealized += leverage * price_return

        total_realized = sum(
            t.get("realized_pnl", 0.0) or 0.0 for t in trades_data
        )
        total_carry = (
            sum(p.get("accrued_carry", 0.0) for p in positions_data)
            + closed_carry
        )
        net_pnl = total_unrealized + total_realized - portfolio_fees - total_carry

        snapshot = SnapshotRecord(
            id=f"SNAP_{model_id}_{now.strftime('%Y%m%d_%H%M%S')}",
            model_id=model_id,
            period_start=now,
            period_end=now,
            prediction_count=len(positions_data),
            result_summary={
                "net_pnl": net_pnl,
                "unrealized_pnl": total_unrealized,
                "realized_pnl": total_realized,
                "total_fees": portfolio_fees,
                "total_carry_costs": total_carry,
                "open_position_count": len(positions_data),
            },
            created_at=now,
        )
        self.snapshot_repository.save(snapshot)
        written_snapshots.append(snapshot)

    if not written_snapshots:
        return False

    self.logger.info("Wrote %d trading snapshots", len(written_snapshots))
    self._rebuild_leaderboard()
    self._maybe_checkpoint(now)
    return True
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_score_trading.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add crunch_node/services/score.py tests/test_score_trading.py
git commit -m "feat(trading): add trading-aware scoring path to ScoreService"
```

---

### Task 6: Score worker — wire TradingStateRepository

**Files:**
- Modify: `crunch_node/workers/score_worker.py`
- Test: `tests/test_score_worker_trading.py`

**Step 1: Write the failing test**

```python
# tests/test_score_worker_trading.py
from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestScoreWorkerTrading:
    @patch("crunch_node.workers.score_worker.load_config")
    @patch("crunch_node.workers.score_worker.create_session")
    def test_trading_state_repo_wired_when_cost_model_present(self, mock_session, mock_config):
        from crunch_node.services.trading.costs import CostModel

        config = MagicMock()
        config.cost_model = CostModel()
        config.scoring_function = lambda p, g: MagicMock()
        config.performance.timing_enabled = False
        mock_config.return_value = config
        mock_session.return_value = MagicMock()

        from crunch_node.workers.score_worker import build_service
        service = build_service()

        assert service.trading_state_repository is not None

    @patch("crunch_node.workers.score_worker.load_config")
    @patch("crunch_node.workers.score_worker.create_session")
    def test_no_trading_state_repo_when_no_cost_model(self, mock_session, mock_config):
        config = MagicMock(spec=[
            "scoring_function", "performance",
        ])
        config.scoring_function = lambda p, g: MagicMock()
        config.performance.timing_enabled = False
        mock_config.return_value = config
        mock_session.return_value = MagicMock()

        from crunch_node.workers.score_worker import build_service
        service = build_service()

        assert service.trading_state_repository is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_score_worker_trading.py -v`
Expected: FAIL

**Step 3: Update score_worker.py**

In `build_service()`, after creating the session, check if config has `cost_model`. If so, create a `TradingStateRepository` and pass it to `ScoreService`.

```python
# Add after session = create_session(), before return ScoreService(...)
trading_state_repo = None
if getattr(config, "cost_model", None) is not None:
    from crunch_node.db.trading_state_repository import TradingStateRepository
    trading_state_repo = TradingStateRepository(session)

return ScoreService(
    # ... existing args ...
    trading_state_repository=trading_state_repo,
)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_score_worker_trading.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add crunch_node/workers/score_worker.py tests/test_score_worker_trading.py
git commit -m "feat(trading): wire TradingStateRepository into score worker"
```

---

### Task 7: Predict worker — use TradingStateRepository instead of SnapshotRepository

**Files:**
- Modify: `crunch_node/workers/predict_worker.py`
- Test: `tests/test_predict_worker_simulator.py` (update existing tests)

**Step 1: Write the failing test**

```python
# Update tests/test_predict_worker_simulator.py

class TestMaybeBuildSimulatorSink:
    def test_returns_sink_with_state_repository(self):
        from crunch_node.services.trading.costs import CostModel
        from crunch_node.services.trading.sink import SimulatorSink
        from crunch_node.workers.predict_worker import _maybe_build_simulator_sink

        config = MagicMock()
        config.cost_model = CostModel()
        session = MagicMock()
        sink = _maybe_build_simulator_sink(config, session)
        assert sink is not None
        assert isinstance(sink, SimulatorSink)
        assert hasattr(sink, '_state_repository')
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_predict_worker_simulator.py -v`
Expected: FAIL

**Step 3: Update `_maybe_build_simulator_sink`**

Change from using `DBSnapshotRepository` to `TradingStateRepository`. Also add crash recovery: call `simulator.load_state()` for each model found in the state repo.

```python
def _maybe_build_simulator_sink(config, session):
    cost_model = getattr(config, "cost_model", None)
    if cost_model is None:
        return None

    from crunch_node.db.trading_state_repository import TradingStateRepository
    from crunch_node.services.trading.simulator import TradingSimulator
    from crunch_node.services.trading.sink import SimulatorSink

    simulator = TradingSimulator(cost_model=cost_model)
    state_repo = TradingStateRepository(session)

    # Crash recovery: reload persisted state
    model_ids = state_repo.get_all_model_ids()
    for model_id in model_ids:
        state = state_repo.load_state(model_id)
        if state is not None:
            simulator.load_state(model_id, state)
            logger.info("Restored trading state for model %s", model_id)

    sink = SimulatorSink(
        simulator=simulator,
        state_repository=state_repo,
        model_ids=model_ids,
    )
    logger.info("Trading simulator enabled with cost_model: %s", cost_model)
    return sink
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_predict_worker_simulator.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add crunch_node/workers/predict_worker.py tests/test_predict_worker_simulator.py
git commit -m "feat(trading): predict worker uses TradingStateRepository with crash recovery"
```

---

### Task 8: Integration test — full persist-to-score flow

**Files:**
- Test: `tests/test_trading_persist_score_integration.py`

**Step 1: Write the integration test**

```python
# tests/test_trading_persist_score_integration.py
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from crunch_node.feeds.contracts import FeedDataRecord
from crunch_node.services.trading.costs import CostModel
from crunch_node.services.trading.simulator import TradingSimulator
from crunch_node.services.trading.sink import SimulatorSink

ZERO_COST = CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0)


class InMemoryTradingStateRepository:
    """In-memory implementation for integration testing."""

    def __init__(self):
        self._states = {}

    def save_state(self, model_id, positions, trades, portfolio_fees, closed_carry):
        self._states[model_id] = {
            "model_id": model_id,
            "positions": [
                {
                    "subject": p.subject, "direction": p.direction,
                    "leverage": p.leverage, "entry_price": p.entry_price,
                    "opened_at": p.opened_at.isoformat(),
                    "current_price": p.current_price, "accrued_carry": p.accrued_carry,
                }
                for p in positions
            ],
            "trades": [
                {
                    "subject": t.subject, "direction": t.direction,
                    "leverage": t.leverage, "entry_price": t.entry_price,
                    "opened_at": t.opened_at.isoformat(),
                    "exit_price": t.exit_price,
                    "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                    "realized_pnl": t.realized_pnl, "fees_paid": t.fees_paid,
                }
                for t in trades
            ],
            "portfolio_fees": portfolio_fees,
            "closed_carry": closed_carry,
            "updated_at": datetime.now(UTC),
        }

    def load_state(self, model_id):
        return self._states.get(model_id)

    def get_all_model_ids(self):
        return list(self._states.keys())


class TestPersistToScoreFlow:
    def test_predict_persists_score_reads(self):
        """SimulatorSink persists state → ScoreService reads it → creates snapshot."""
        from crunch_node.entities.prediction import SnapshotRecord
        from crunch_node.services.score import ScoreService

        state_repo = InMemoryTradingStateRepository()

        # Predict side: simulator + sink persist state
        sim = TradingSimulator(cost_model=ZERO_COST)
        sink = SimulatorSink(
            simulator=sim,
            state_repository=state_repo,
            model_ids=["model_1"],
        )

        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=now)

        record = FeedDataRecord(
            source="binance", subject="BTCUSDT", kind="candle", granularity="1m",
            ts_event=int(now.timestamp() * 1000), values={"close": 51000.0},
        )
        asyncio.run(sink.on_record(record))

        # Verify state was persisted
        assert state_repo.load_state("model_1") is not None

        # Score side: reads persisted state, writes snapshot
        snapshot_repo = MagicMock()
        score_service = ScoreService(
            checkpoint_interval_seconds=300,
            scoring_function=lambda p, g: MagicMock(),
            snapshot_repository=snapshot_repo,
            model_repository=MagicMock(fetch_all=MagicMock(return_value={})),
            leaderboard_repository=MagicMock(),
            prediction_repository=MagicMock(find=MagicMock(return_value=[])),
            trading_state_repository=state_repo,
        )

        result = score_service.score_and_snapshot()
        assert result is True

        snapshot_repo.save.assert_called_once()
        snap = snapshot_repo.save.call_args[0][0]
        assert snap.model_id == "model_1"
        expected_pnl = 1.0 * (51000.0 - 50000.0) / 50000.0
        assert snap.result_summary["unrealized_pnl"] == pytest.approx(expected_pnl)
        assert snap.result_summary["net_pnl"] == pytest.approx(expected_pnl)

    def test_crash_recovery_then_score(self):
        """Simulator crashes, reloads from DB, then Score reads consistent state."""
        state_repo = InMemoryTradingStateRepository()

        # First run: open position, persist
        sim1 = TradingSimulator(cost_model=ZERO_COST)
        sink1 = SimulatorSink(
            simulator=sim1, state_repository=state_repo, model_ids=["model_1"],
        )
        now = datetime.now(UTC)
        sim1.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=now)

        record = FeedDataRecord(
            source="binance", subject="BTCUSDT", kind="candle", granularity="1m",
            ts_event=int(now.timestamp() * 1000), values={"close": 51000.0},
        )
        asyncio.run(sink1.on_record(record))

        # "Crash" — create new simulator, reload from state_repo
        sim2 = TradingSimulator(cost_model=ZERO_COST)
        state = state_repo.load_state("model_1")
        sim2.load_state("model_1", state)

        pos = sim2.get_position("model_1", "BTCUSDT")
        assert pos is not None
        assert pos.direction == "long"
        assert pos.current_price == pytest.approx(51000.0)
```

**Step 2: Run test**

Run: `uv run pytest tests/test_trading_persist_score_integration.py -v`
Expected: PASS (if all prior tasks complete)

**Step 3: Commit**

```bash
git add tests/test_trading_persist_score_integration.py
git commit -m "test(trading): integration test for persist-to-score and crash recovery"
```

---

### Task 9: Run full test suite

**Step 1: Run all trading tests**

Run: `uv run pytest tests/test_trading*.py tests/test_simulator*.py tests/test_predict_worker*.py tests/test_score_trading.py tests/test_score_worker_trading.py -v`
Expected: ALL PASS

**Step 2: Run full test suite**

Run: `uv run pytest --ignore=packs --ignore=tests/test_backtest_harness.py --ignore=tests/test_parquet_sink.py -q`
Expected: 0 regressions from our changes (pre-existing failures in backfill/timing are expected)

**Step 3: Commit any fixes if needed**

---

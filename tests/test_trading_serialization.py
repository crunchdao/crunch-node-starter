from __future__ import annotations

from datetime import UTC, datetime

import pytest

from crunch_node.services.trading.costs import CostModel
from crunch_node.services.trading.simulator import TradingEngine

ZERO_COST = CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0)


class TestSimulatorSerialization:
    def test_get_full_state(self):
        sim = TradingEngine(cost_model=ZERO_COST)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        sim.apply_order("m1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        sim.apply_order("m1", "ETHUSDT", "short", 0.3, price=3000.0, timestamp=now)

        state = sim.get_full_state("m1")
        assert len(state["positions"]) == 2
        assert state["portfolio_fees"] >= 0
        assert state["closed_carry"] >= 0

    def test_load_state_restores_positions(self):
        sim1 = TradingEngine(cost_model=ZERO_COST)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        sim1.apply_order("m1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        state = sim1.get_full_state("m1")

        sim2 = TradingEngine(cost_model=ZERO_COST)
        sim2.load_state("m1", state)

        pos = sim2.get_position("m1", "BTCUSDT")
        assert pos is not None
        assert pos.direction == "long"
        assert pos.leverage == pytest.approx(0.5)
        assert pos.entry_price == pytest.approx(50000.0)

    def test_load_state_restores_trades(self):
        sim1 = TradingEngine(cost_model=ZERO_COST)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        sim1.apply_order("m1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        sim1.apply_order("m1", "BTCUSDT", "short", 0.5, price=51000.0, timestamp=now)
        state = sim1.get_full_state("m1")

        sim2 = TradingEngine(cost_model=ZERO_COST)
        sim2.load_state("m1", state)

        trades = sim2.get_trades("m1")
        assert len(trades) == 1
        assert trades[0].realized_pnl is not None

    def test_load_state_restores_accumulators(self):
        sim1 = TradingEngine(
            cost_model=CostModel(
                trading_fee_pct=0.001,
                spread_pct=0.0,
                carry_annual_pct=0.0,
            )
        )
        now = datetime(2026, 1, 1, tzinfo=UTC)
        sim1.apply_order("m1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        state = sim1.get_full_state("m1")

        sim2 = TradingEngine(cost_model=ZERO_COST)
        sim2.load_state("m1", state)

        snapshot = sim2.get_portfolio_snapshot("m1", now)
        assert snapshot["total_fees"] == pytest.approx(0.001 * 0.5)

    def test_roundtrip_preserves_snapshot(self):
        sim1 = TradingEngine(cost_model=ZERO_COST)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        sim1.apply_order("m1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        sim1.mark_to_market("BTCUSDT", 51000.0, now)
        snap1 = sim1.get_portfolio_snapshot("m1", now)

        state = sim1.get_full_state("m1")
        sim2 = TradingEngine(cost_model=ZERO_COST)
        sim2.load_state("m1", state)
        snap2 = sim2.get_portfolio_snapshot("m1", now)

        assert snap2["net_pnl"] == pytest.approx(snap1["net_pnl"])
        assert snap2["total_unrealized_pnl"] == pytest.approx(
            snap1["total_unrealized_pnl"]
        )
        assert snap2["open_position_count"] == snap1["open_position_count"]

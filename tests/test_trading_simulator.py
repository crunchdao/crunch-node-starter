from __future__ import annotations

from datetime import UTC, datetime

from crunch_node.services.trading.costs import CostModel
from crunch_node.services.trading.simulator import TradingSimulator

ZERO_COST = CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0)


class TestApplyOrder:
    def test_open_long_position(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        sim.apply_order("model_1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=datetime.now(UTC))
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos is not None
        assert pos.direction == "long"
        assert pos.leverage == 0.5
        assert pos.entry_price == 50000.0

    def test_add_to_existing_position(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 0.3, price=50000.0, timestamp=now)
        sim.apply_order("model_1", "BTCUSDT", "long", 0.2, price=51000.0, timestamp=now)
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.leverage == 0.5

    def test_reduce_position(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        sim.apply_order("model_1", "BTCUSDT", "short", 0.3, price=51000.0, timestamp=now)
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.direction == "long"
        assert abs(pos.leverage - 0.2) < 1e-9

    def test_close_position_by_opposite_order(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        sim.apply_order("model_1", "BTCUSDT", "short", 0.5, price=51000.0, timestamp=now)
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos is None
        assert len(sim.get_trades("model_1")) == 1
        assert sim.get_trades("model_1")[0].realized_pnl is not None

    def test_overshoot_opens_new_position(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        sim.apply_order("model_1", "BTCUSDT", "short", 0.8, price=51000.0, timestamp=now)
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos is not None
        assert pos.direction == "short"
        assert abs(pos.leverage - 0.3) < 1e-9


class TestMarkToMarket:
    def test_mark_to_market(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=now)
        sim.mark_to_market("BTCUSDT", 51000.0, now)
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.current_price == 51000.0
        assert pos.unrealized_pnl == 1.0 * (51000.0 - 50000.0) / 50000.0


class TestFees:
    def test_fees_deducted_on_order(self):
        sim = TradingSimulator(
            cost_model=CostModel(trading_fee_pct=0.001, spread_pct=0.0, carry_annual_pct=0.0)
        )
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        snapshot = sim.get_portfolio_snapshot("model_1", now)
        assert snapshot["total_fees"] == 0.001 * 0.5

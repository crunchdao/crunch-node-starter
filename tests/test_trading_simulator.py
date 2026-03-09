from __future__ import annotations

from datetime import UTC, datetime

import pytest

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
        assert pos.leverage == pytest.approx(0.5)
        assert pos.entry_price == pytest.approx((50000.0 * 0.3 + 51000.0 * 0.2) / 0.5)

    def test_reduce_position(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        sim.apply_order("model_1", "BTCUSDT", "short", 0.3, price=51000.0, timestamp=now)
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.direction == "long"
        assert pos.leverage == pytest.approx(0.2)

    def test_reduce_records_partial_trade(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        sim.apply_order("model_1", "BTCUSDT", "short", 0.3, price=51000.0, timestamp=now)
        trades = sim.get_trades("model_1")
        assert len(trades) == 1
        expected_pnl = 0.3 * (51000.0 - 50000.0) / 50000.0
        assert trades[0].realized_pnl == pytest.approx(expected_pnl)
        assert trades[0].leverage == 0.3

    def test_close_position_by_opposite_order(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        sim.apply_order("model_1", "BTCUSDT", "short", 0.5, price=51000.0, timestamp=now)
        assert sim.get_position("model_1", "BTCUSDT") is None
        trades = sim.get_trades("model_1")
        assert len(trades) == 1
        expected_pnl = 0.5 * (51000.0 - 50000.0) / 50000.0
        assert trades[0].realized_pnl == pytest.approx(expected_pnl)

    def test_overshoot_opens_new_position(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        sim.apply_order("model_1", "BTCUSDT", "short", 0.8, price=51000.0, timestamp=now)
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos is not None
        assert pos.direction == "short"
        assert pos.leverage == pytest.approx(0.3)
        assert len(sim.get_trades("model_1")) == 1

    def test_negative_leverage_raises(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        with pytest.raises(ValueError):
            sim.apply_order("m1", "X", "long", -1.0, price=100.0, timestamp=datetime.now(UTC))

    def test_invalid_direction_raises(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        with pytest.raises(ValueError):
            sim.apply_order("m1", "X", "up", 1.0, price=100.0, timestamp=datetime.now(UTC))

    def test_close_short_position(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "short", 0.5, price=50000.0, timestamp=now)
        sim.apply_order("model_1", "BTCUSDT", "long", 0.5, price=49000.0, timestamp=now)
        assert sim.get_position("model_1", "BTCUSDT") is None
        expected_pnl = 0.5 * (50000.0 - 49000.0) / 50000.0
        assert sim.get_trades("model_1")[0].realized_pnl == pytest.approx(expected_pnl)


class TestMarkToMarket:
    def test_mark_to_market(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=now)
        sim.mark_to_market("BTCUSDT", 51000.0, now)
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.current_price == 51000.0
        assert pos.unrealized_pnl == pytest.approx(1.0 * (51000.0 - 50000.0) / 50000.0)


class TestFees:
    def test_fees_deducted_on_order(self):
        sim = TradingSimulator(
            cost_model=CostModel(trading_fee_pct=0.001, spread_pct=0.0, carry_annual_pct=0.0)
        )
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 0.5, price=50000.0, timestamp=now)
        snapshot = sim.get_portfolio_snapshot("model_1", now)
        assert snapshot["total_fees"] == pytest.approx(0.001 * 0.5)

    def test_snapshot_net_pnl(self):
        sim = TradingSimulator(
            cost_model=CostModel(trading_fee_pct=0.001, spread_pct=0.0, carry_annual_pct=0.0)
        )
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=now)
        sim.mark_to_market("BTCUSDT", 51000.0, now)
        snapshot = sim.get_portfolio_snapshot("model_1", now)
        expected_unrealized = 1.0 * (51000.0 - 50000.0) / 50000.0
        expected_fees = 0.001 * 1.0
        assert snapshot["total_unrealized_pnl"] == pytest.approx(expected_unrealized)
        assert snapshot["net_pnl"] == pytest.approx(expected_unrealized - expected_fees)

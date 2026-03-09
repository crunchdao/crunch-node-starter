from __future__ import annotations

from datetime import UTC, datetime

import pytest

from crunch_node.services.trading.costs import CostModel
from crunch_node.services.trading.simulator import TradingSimulator

ZERO_COST = CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0)


class TestLeverageLimits:
    def test_position_leverage_clamped(self):
        sim = TradingSimulator(cost_model=ZERO_COST, max_position_leverage=2.5)
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 3.0, price=50000.0, timestamp=now)
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.leverage == pytest.approx(2.5)

    def test_portfolio_leverage_clamped(self):
        sim = TradingSimulator(cost_model=ZERO_COST, max_portfolio_leverage=5.0)
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 2.5, price=50000.0, timestamp=now)
        sim.apply_order("model_1", "ETHUSDT", "long", 2.5, price=3000.0, timestamp=now)
        sim.apply_order("model_1", "SOLUSD", "long", 1.0, price=100.0, timestamp=now)
        total = sum(p.leverage for p in sim.get_all_positions("model_1"))
        assert total <= 5.0

    def test_no_clamping_within_limits(self):
        sim = TradingSimulator(cost_model=ZERO_COST, max_position_leverage=5.0, max_portfolio_leverage=10.0)
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 3.0, price=50000.0, timestamp=now)
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.leverage == pytest.approx(3.0)

    def test_add_to_position_respects_limit(self):
        sim = TradingSimulator(cost_model=ZERO_COST, max_position_leverage=2.0)
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 1.5, price=50000.0, timestamp=now)
        sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=51000.0, timestamp=now)
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.leverage <= 2.0

    def test_zero_leverage_after_clamping_skips_order(self):
        sim = TradingSimulator(cost_model=ZERO_COST, max_portfolio_leverage=2.0)
        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 2.0, price=50000.0, timestamp=now)
        sim.apply_order("model_1", "ETHUSDT", "long", 1.0, price=3000.0, timestamp=now)
        pos = sim.get_position("model_1", "ETHUSDT")
        assert pos is None

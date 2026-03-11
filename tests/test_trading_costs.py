from __future__ import annotations

import pytest

from crunch_node.services.trading.costs import CostModel


class TestCostModel:
    def test_trading_fee(self):
        costs = CostModel(trading_fee_pct=0.001, spread_pct=0.0, carry_annual_pct=0.0)
        fee = costs.order_cost(size=0.5)
        assert fee == 0.001 * 0.5

    def test_spread_cost(self):
        costs = CostModel(trading_fee_pct=0.0, spread_pct=0.001, carry_annual_pct=0.0)
        fee = costs.order_cost(size=1.0)
        assert fee == 0.001

    def test_carry_cost(self):
        costs = CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.1095)
        daily = costs.carry_cost(size=1.0, seconds=86400)
        assert daily == pytest.approx(0.0003, abs=1e-6)

    def test_zero_size(self):
        costs = CostModel()
        assert costs.order_cost(size=0.0) == 0.0
        assert costs.carry_cost(size=0.0, seconds=86400) == 0.0

    def test_negative_fee_rejected(self):
        with pytest.raises(Exception):
            CostModel(trading_fee_pct=-0.001)

from __future__ import annotations

from crunch_node.services.trading.costs import CostModel


def test_trading_fee():
    costs = CostModel(trading_fee_pct=0.001, spread_pct=0.0, carry_annual_pct=0.0)
    fee = costs.order_cost(leverage=0.5)
    assert fee == 0.001 * 0.5


def test_spread_cost():
    costs = CostModel(trading_fee_pct=0.0, spread_pct=0.001, carry_annual_pct=0.0)
    fee = costs.order_cost(leverage=1.0)
    assert fee == 0.001


def test_carry_cost():
    costs = CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.1095)
    daily = costs.carry_cost(leverage=1.0, seconds=86400)
    assert abs(daily - 0.0003) < 0.0001

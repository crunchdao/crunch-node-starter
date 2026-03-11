from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from extensions.costs import CostModel
from extensions.simulator import TradingEngine

CARRY_ONLY = CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.1095)


class TestCarryCost:
    def test_carry_accrues_on_mark_to_market(self):
        sim = TradingEngine(cost_model=CARRY_ONLY)
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        t1 = t0 + timedelta(days=1)

        sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=t0)
        sim.mark_to_market("BTCUSDT", 50000.0, t1)

        pos = sim.get_position("model_1", "BTCUSDT")
        expected_carry = 0.1095 * 1.0 * 86400 / (365 * 86400)
        assert pos.accrued_carry == pytest.approx(expected_carry, abs=1e-6)

    def test_carry_in_portfolio_snapshot(self):
        sim = TradingEngine(cost_model=CARRY_ONLY)
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        t1 = t0 + timedelta(days=1)

        sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=t0)
        sim.mark_to_market("BTCUSDT", 50000.0, t1)

        snapshot = sim.get_portfolio_snapshot("model_1", t1)
        assert snapshot["total_carry_costs"] > 0
        assert snapshot["net_pnl"] < 0  # carry cost reduces net P&L

    def test_carry_accumulates_across_ticks(self):
        sim = TradingEngine(cost_model=CARRY_ONLY)
        t0 = datetime(2026, 1, 1, tzinfo=UTC)

        sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=t0)
        sim.mark_to_market("BTCUSDT", 50000.0, t0 + timedelta(days=1))
        sim.mark_to_market("BTCUSDT", 50000.0, t0 + timedelta(days=2))

        pos = sim.get_position("model_1", "BTCUSDT")
        expected_carry = 2 * 0.1095 * 1.0 * 86400 / (365 * 86400)
        assert pos.accrued_carry == pytest.approx(expected_carry, abs=1e-6)

    def test_no_carry_when_zero_rate(self):
        zero_carry = CostModel(
            trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0
        )
        sim = TradingEngine(cost_model=zero_carry)
        t0 = datetime(2026, 1, 1, tzinfo=UTC)

        sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=t0)
        sim.mark_to_market("BTCUSDT", 50000.0, t0 + timedelta(days=1))

        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.accrued_carry == 0.0

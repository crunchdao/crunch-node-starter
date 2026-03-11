from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from crunch_node.services.trading.config import TradingConfig
from crunch_node.services.trading.costs import CostModel
from crunch_node.services.trading.simulator import TradingEngine
from crunch_node.services.trading.sink import SimulatorSink

ZERO_COST = CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0)
DEFAULT_TRADING_CONFIG = TradingConfig(cost_model=ZERO_COST)


class TestOrderMode:
    def test_buy_opens_long(self):
        sim = TradingEngine(
            cost_model=ZERO_COST,
            max_position_size=1_000_000,
            max_portfolio_size=1_000_000,
        )
        sink = SimulatorSink(
            simulator=sim,
            state_repository=MagicMock(),
            trading_config=DEFAULT_TRADING_CONFIG,
            signal_mode="order",
        )
        now = datetime.now(UTC)
        sink.apply_signal(
            "model_1",
            "BTCUSDT",
            {"action": "buy", "amount": 100},
            price=50000.0,
            timestamp=now,
        )
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.direction == "long"
        assert pos.size == pytest.approx(100.0)

    def test_sell_opens_short(self):
        sim = TradingEngine(
            cost_model=ZERO_COST,
            max_position_size=1_000_000,
            max_portfolio_size=1_000_000,
        )
        sink = SimulatorSink(
            simulator=sim,
            state_repository=MagicMock(),
            trading_config=DEFAULT_TRADING_CONFIG,
            signal_mode="order",
        )
        now = datetime.now(UTC)
        sink.apply_signal(
            "model_1",
            "BTCUSDT",
            {"action": "sell", "amount": 50},
            price=50000.0,
            timestamp=now,
        )
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.direction == "short"
        assert pos.size == pytest.approx(50.0)

    def test_zero_amount_is_noop(self):
        sim = TradingEngine(
            cost_model=ZERO_COST,
            max_position_size=1_000_000,
            max_portfolio_size=1_000_000,
        )
        sink = SimulatorSink(
            simulator=sim,
            state_repository=MagicMock(),
            trading_config=DEFAULT_TRADING_CONFIG,
            signal_mode="order",
        )
        now = datetime.now(UTC)
        sink.apply_signal(
            "model_1",
            "BTCUSDT",
            {"action": "buy", "amount": 0},
            price=50000.0,
            timestamp=now,
        )
        assert sim.get_position("model_1", "BTCUSDT") is None

    def test_missing_fields_raises(self):
        sim = TradingEngine(
            cost_model=ZERO_COST,
            max_position_size=1_000_000,
            max_portfolio_size=1_000_000,
        )
        sink = SimulatorSink(
            simulator=sim,
            state_repository=MagicMock(),
            trading_config=DEFAULT_TRADING_CONFIG,
            signal_mode="order",
        )
        now = datetime.now(UTC)
        with pytest.raises(ValueError, match="Order mode requires"):
            sink.apply_signal(
                "model_1",
                "BTCUSDT",
                {"action": "buy"},
                price=50000.0,
                timestamp=now,
            )

    def test_buy_then_sell_closes_position(self):
        sim = TradingEngine(
            cost_model=ZERO_COST,
            max_position_size=1_000_000,
            max_portfolio_size=1_000_000,
        )
        sink = SimulatorSink(
            simulator=sim,
            state_repository=MagicMock(),
            trading_config=DEFAULT_TRADING_CONFIG,
            signal_mode="order",
        )
        now = datetime.now(UTC)
        sink.apply_signal(
            "model_1",
            "BTCUSDT",
            {"action": "buy", "amount": 100},
            price=50000.0,
            timestamp=now,
        )
        sink.apply_signal(
            "model_1",
            "BTCUSDT",
            {"action": "sell", "amount": 100},
            price=51000.0,
            timestamp=now,
        )
        assert sim.get_position("model_1", "BTCUSDT") is None

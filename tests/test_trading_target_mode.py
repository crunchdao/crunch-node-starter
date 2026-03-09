from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from crunch_node.services.trading.costs import CostModel
from crunch_node.services.trading.simulator import TradingEngine
from crunch_node.services.trading.sink import SimulatorSink

ZERO_COST = CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0)


class TestTargetMode:
    def test_opens_long_from_flat(self):
        sim = TradingEngine(cost_model=ZERO_COST)
        sink = SimulatorSink(
            simulator=sim, state_repository=MagicMock(), signal_mode="target"
        )
        now = datetime.now(UTC)
        sink.apply_signal(
            "model_1", "BTCUSDT", {"signal": 0.7}, price=50000.0, timestamp=now
        )
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.direction == "long"
        assert pos.leverage == pytest.approx(0.7)

    def test_opens_short_from_flat(self):
        sim = TradingEngine(cost_model=ZERO_COST)
        sink = SimulatorSink(
            simulator=sim, state_repository=MagicMock(), signal_mode="target"
        )
        now = datetime.now(UTC)
        sink.apply_signal(
            "model_1", "BTCUSDT", {"signal": -0.5}, price=50000.0, timestamp=now
        )
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.direction == "short"
        assert pos.leverage == pytest.approx(0.5)

    def test_reduces_long(self):
        sim = TradingEngine(cost_model=ZERO_COST)
        sink = SimulatorSink(
            simulator=sim, state_repository=MagicMock(), signal_mode="target"
        )
        now = datetime.now(UTC)
        sink.apply_signal(
            "model_1", "BTCUSDT", {"signal": 0.7}, price=50000.0, timestamp=now
        )
        sink.apply_signal(
            "model_1", "BTCUSDT", {"signal": 0.3}, price=51000.0, timestamp=now
        )
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.direction == "long"
        assert pos.leverage == pytest.approx(0.3)

    def test_zero_signal_closes(self):
        sim = TradingEngine(cost_model=ZERO_COST)
        sink = SimulatorSink(
            simulator=sim, state_repository=MagicMock(), signal_mode="target"
        )
        now = datetime.now(UTC)
        sink.apply_signal(
            "model_1", "BTCUSDT", {"signal": 0.7}, price=50000.0, timestamp=now
        )
        sink.apply_signal(
            "model_1", "BTCUSDT", {"signal": 0.0}, price=51000.0, timestamp=now
        )
        assert sim.get_position("model_1", "BTCUSDT") is None

    def test_flips_long_to_short(self):
        sim = TradingEngine(cost_model=ZERO_COST)
        sink = SimulatorSink(
            simulator=sim, state_repository=MagicMock(), signal_mode="target"
        )
        now = datetime.now(UTC)
        sink.apply_signal(
            "model_1", "BTCUSDT", {"signal": 0.5}, price=50000.0, timestamp=now
        )
        sink.apply_signal(
            "model_1", "BTCUSDT", {"signal": -0.3}, price=51000.0, timestamp=now
        )
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.direction == "short"
        assert pos.leverage == pytest.approx(0.3)

    def test_increases_long(self):
        sim = TradingEngine(cost_model=ZERO_COST)
        sink = SimulatorSink(
            simulator=sim, state_repository=MagicMock(), signal_mode="target"
        )
        now = datetime.now(UTC)
        sink.apply_signal(
            "model_1", "BTCUSDT", {"signal": 0.3}, price=50000.0, timestamp=now
        )
        sink.apply_signal(
            "model_1", "BTCUSDT", {"signal": 0.8}, price=51000.0, timestamp=now
        )
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.direction == "long"
        assert pos.leverage == pytest.approx(0.8)

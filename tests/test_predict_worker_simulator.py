from __future__ import annotations

from unittest.mock import MagicMock

from crunch_node.services.trading.config import TradingConfig
from crunch_node.services.trading.sink import SimulatorSink
from crunch_node.workers.predict_worker import _maybe_build_simulator_sink


class TestMaybeBuildSimulatorSink:
    def test_returns_sink_when_trading_config_present(self):
        config = MagicMock()
        config.trading = TradingConfig()
        session = MagicMock()
        sink = _maybe_build_simulator_sink(config, session)
        assert sink is not None
        assert isinstance(sink, SimulatorSink)
        assert hasattr(sink, "_state_repository")

    def test_returns_none_when_no_trading_config(self):
        config = MagicMock(spec=[])
        session = MagicMock()
        sink = _maybe_build_simulator_sink(config, session)
        assert sink is None

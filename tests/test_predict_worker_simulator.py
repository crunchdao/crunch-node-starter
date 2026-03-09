from __future__ import annotations

from unittest.mock import MagicMock, patch

from crunch_node.services.trading.costs import CostModel
from crunch_node.services.trading.sink import SimulatorSink
from crunch_node.workers.predict_worker import _maybe_build_simulator_sink


class TestMaybeBuildSimulatorSink:
    def test_returns_sink_when_cost_model_present(self):
        config = MagicMock()
        config.cost_model = CostModel()
        session = MagicMock()
        sink = _maybe_build_simulator_sink(config, session)
        assert sink is not None
        assert isinstance(sink, SimulatorSink)
        assert hasattr(sink, "_state_repository")

    def test_returns_none_when_no_cost_model(self):
        config = MagicMock(spec=[])
        session = MagicMock()
        sink = _maybe_build_simulator_sink(config, session)
        assert sink is None

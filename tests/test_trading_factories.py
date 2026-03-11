from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

_pack_node = str(Path(__file__).resolve().parent.parent / "packs" / "trading" / "node")
if _pack_node not in sys.path:
    sys.path.insert(0, _pack_node)

from extensions.trading.costs import CostModel
from extensions.trading.factories import (
    build_score_snapshots,
    build_simulator_sink,
    build_trading_widgets,
)
from extensions.trading.sink import SimulatorSink


def _mock_config():
    config = MagicMock()
    config.trading.cost_model = CostModel()
    config.trading.max_position_size = 10.0
    config.trading.max_portfolio_size = 20.0
    config.trading.signal_mode = "delta"
    config.trading.asset_price_mapping = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
    return config


def test_build_simulator_sink_returns_simulator_sink():
    session = MagicMock()
    config = _mock_config()

    session.exec.return_value.all.return_value = []

    sink = build_simulator_sink(session=session, config=config)

    assert isinstance(sink, SimulatorSink)
    assert hasattr(sink, "on_record")
    assert hasattr(sink, "on_predictions")


def test_build_score_snapshots_returns_callable():
    session = MagicMock()
    config = _mock_config()
    snapshot_repository = MagicMock()

    result = build_score_snapshots(
        session=session, config=config, snapshot_repository=snapshot_repository
    )

    assert callable(result)


def test_build_trading_widgets_returns_non_empty_list_of_dicts():
    widgets = build_trading_widgets()

    assert isinstance(widgets, list)
    assert len(widgets) > 0
    for w in widgets:
        assert isinstance(w, dict)

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from crunch_node.entities.prediction import InputRecord, PredictionRecord, PredictionStatus
from crunch_node.services.trading.costs import CostModel
from crunch_node.services.trading.simulator import TradingSimulator
from crunch_node.services.trading.sink import SimulatorSink

ZERO_COST = CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0)


class TestOnPredictions:
    def _make_prediction(self, model_id, subject, direction, leverage, now):
        return PredictionRecord(
            id="PRED_1",
            model_id=model_id,
            input_id="INP_1",
            prediction_config_id=None,
            scope_key=f"trading-{subject.lower()}",
            scope={"subject": subject},
            inference_output={"direction": direction, "leverage": leverage},
            status=PredictionStatus.PENDING,
            exec_time_ms=1.0,
            performed_at=now,
            resolvable_at=now,
        )

    def test_hook_forwards_signal_to_simulator(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        sink = SimulatorSink(simulator=sim, snapshot_repository=MagicMock())
        now = datetime.now(UTC)
        inp = InputRecord(id="INP_1", raw_data={"close": 50000.0}, received_at=now)
        predictions = [self._make_prediction("model_1", "BTCUSDT", "long", 0.5, now)]

        result = sink.on_predictions(predictions, inp, now)

        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos is not None
        assert pos.direction == "long"
        assert pos.leverage == 0.5
        assert result == predictions

    def test_hook_extracts_price_from_input(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        sink = SimulatorSink(simulator=sim, snapshot_repository=MagicMock())
        now = datetime.now(UTC)
        inp = InputRecord(id="INP_1", raw_data={"close": 50000.0}, received_at=now)
        predictions = [self._make_prediction("model_1", "BTCUSDT", "long", 0.5, now)]

        sink.on_predictions(predictions, inp, now)
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.entry_price == 50000.0

    def test_hook_auto_registers_model_id(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        sink = SimulatorSink(simulator=sim, snapshot_repository=MagicMock())
        now = datetime.now(UTC)
        inp = InputRecord(id="INP_1", raw_data={"close": 50000.0}, received_at=now)
        predictions = [self._make_prediction("model_1", "BTCUSDT", "long", 0.5, now)]

        sink.on_predictions(predictions, inp, now)
        assert "model_1" in sink._model_ids

    def test_hook_skips_when_no_price(self):
        sim = TradingSimulator(cost_model=ZERO_COST)
        sink = SimulatorSink(simulator=sim, snapshot_repository=MagicMock())
        now = datetime.now(UTC)
        inp = InputRecord(id="INP_1", raw_data={}, received_at=now)
        predictions = [self._make_prediction("model_1", "BTCUSDT", "long", 0.5, now)]

        result = sink.on_predictions(predictions, inp, now)
        assert sim.get_position("model_1", "BTCUSDT") is None
        assert result == predictions

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

from crunch_node.entities.prediction import (
    InputRecord,
    PredictionRecord,
    PredictionStatus,
)
from crunch_node.feeds.contracts import FeedDataRecord
from crunch_node.services.trading.costs import CostModel
from crunch_node.services.trading.simulator import TradingEngine
from crunch_node.services.trading.sink import SimulatorSink

ZERO_COST = CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0)


def _make_feed_record(
    subject: str, close: float, ts_ms: int = 1_000_000
) -> FeedDataRecord:
    return FeedDataRecord(
        source="test",
        subject=subject,
        kind="candle",
        granularity="1m",
        ts_event=ts_ms,
        values={"close": close},
    )


def _prime_price(sink: SimulatorSink, subject: str, price: float) -> None:
    asyncio.run(sink.on_record(_make_feed_record(subject, price)))


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
        sim = TradingEngine(cost_model=ZERO_COST)
        sink = SimulatorSink(simulator=sim, state_repository=MagicMock())
        now = datetime.now(UTC)
        _prime_price(sink, "BTCUSDT", 50000.0)
        inp = InputRecord(id="INP_1", raw_data={}, received_at=now)
        predictions = [self._make_prediction("model_1", "BTCUSDT", "long", 0.5, now)]

        result = sink.on_predictions(predictions, inp, now)

        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos is not None
        assert pos.direction == "long"
        assert pos.size == 0.5
        assert result == predictions

    def test_hook_uses_price_from_feed(self):
        sim = TradingEngine(cost_model=ZERO_COST)
        sink = SimulatorSink(simulator=sim, state_repository=MagicMock())
        now = datetime.now(UTC)
        _prime_price(sink, "BTCUSDT", 50000.0)
        inp = InputRecord(id="INP_1", raw_data={}, received_at=now)
        predictions = [self._make_prediction("model_1", "BTCUSDT", "long", 0.5, now)]

        sink.on_predictions(predictions, inp, now)
        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.entry_price == 50000.0

    def test_hook_auto_registers_model_id(self):
        sim = TradingEngine(cost_model=ZERO_COST)
        sink = SimulatorSink(simulator=sim, state_repository=MagicMock())
        now = datetime.now(UTC)
        _prime_price(sink, "BTCUSDT", 50000.0)
        inp = InputRecord(id="INP_1", raw_data={}, received_at=now)
        predictions = [self._make_prediction("model_1", "BTCUSDT", "long", 0.5, now)]

        sink.on_predictions(predictions, inp, now)
        assert "model_1" in sink._model_ids

    def test_hook_skips_when_no_price(self):
        sim = TradingEngine(cost_model=ZERO_COST)
        sink = SimulatorSink(simulator=sim, state_repository=MagicMock())
        now = datetime.now(UTC)
        inp = InputRecord(id="INP_1", raw_data={}, received_at=now)
        predictions = [self._make_prediction("model_1", "BTCUSDT", "long", 0.5, now)]

        result = sink.on_predictions(predictions, inp, now)
        assert sim.get_position("model_1", "BTCUSDT") is None
        assert result == predictions

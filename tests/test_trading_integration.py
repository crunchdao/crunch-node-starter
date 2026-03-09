from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from crunch_node.feeds.contracts import FeedDataRecord
from crunch_node.services.trading.costs import CostModel
from crunch_node.services.trading.simulator import TradingSimulator
from crunch_node.services.trading.sink import SimulatorSink

ZERO_COST = CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0)


class TestFullFlow:
    def test_order_tick_snapshot_close(self):
        """Open position -> tick -> snapshot with unrealized P&L -> close -> snapshot with realized P&L."""
        sim = TradingSimulator(cost_model=ZERO_COST)
        snapshot_repo = MagicMock()
        sink = SimulatorSink(simulator=sim, snapshot_repository=snapshot_repo, model_ids=["model_1"])

        now = datetime.now(UTC)
        ts_ms = int(now.timestamp() * 1000)

        sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=now)

        record = FeedDataRecord(
            source="binance", subject="BTCUSDT", kind="candle", granularity="1m",
            ts_event=ts_ms, values={"close": 51000.0},
        )
        asyncio.run(sink.on_record(record))

        snapshot_repo.save.assert_called()
        snap = snapshot_repo.save.call_args[0][0]
        assert snap.result_summary["unrealized_pnl"] > 0
        assert snap.result_summary["realized_pnl"] == 0
        assert snap.result_summary["open_position_count"] == 1

        snapshot_repo.reset_mock()

        sim.apply_order("model_1", "BTCUSDT", "short", 1.0, price=51000.0, timestamp=now)

        record2 = FeedDataRecord(
            source="binance", subject="BTCUSDT", kind="candle", granularity="1m",
            ts_event=ts_ms, values={"close": 51000.0},
        )
        asyncio.run(sink.on_record(record2))

        snap2 = snapshot_repo.save.call_args[0][0]
        expected_realized = 1.0 * (51000.0 - 50000.0) / 50000.0
        assert snap2.result_summary["realized_pnl"] == pytest.approx(expected_realized)
        assert snap2.result_summary["unrealized_pnl"] == 0
        assert snap2.result_summary["open_position_count"] == 0

    def test_hook_to_tick_flow(self):
        """Prediction hook opens position, then tick writes snapshot."""
        from crunch_node.entities.prediction import InputRecord, PredictionRecord, PredictionStatus

        sim = TradingSimulator(cost_model=ZERO_COST)
        snapshot_repo = MagicMock()
        sink = SimulatorSink(simulator=sim, snapshot_repository=snapshot_repo)

        now = datetime.now(UTC)
        inp = InputRecord(id="INP_1", raw_data={"close": 50000.0}, received_at=now)
        predictions = [
            PredictionRecord(
                id="PRED_1",
                model_id="model_1",
                input_id="INP_1",
                prediction_config_id=None,
                scope_key="trading-btcusdt",
                scope={"subject": "BTCUSDT"},
                inference_output={"direction": "long", "leverage": 1.0},
                status=PredictionStatus.PENDING,
                exec_time_ms=1.0,
                performed_at=now,
                resolvable_at=now,
            ),
        ]

        sink.on_predictions(predictions, inp, now)
        assert "model_1" in sink._model_ids

        record = FeedDataRecord(
            source="binance", subject="BTCUSDT", kind="candle", granularity="1m",
            ts_event=int(now.timestamp() * 1000), values={"close": 52000.0},
        )
        asyncio.run(sink.on_record(record))

        snapshot_repo.save.assert_called_once()
        snap = snapshot_repo.save.call_args[0][0]
        assert snap.model_id == "model_1"
        expected_pnl = 1.0 * (52000.0 - 50000.0) / 50000.0
        assert snap.result_summary["unrealized_pnl"] == pytest.approx(expected_pnl)

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from crunch_node.feeds.contracts import FeedDataRecord
from crunch_node.services.trading.config import TradingConfig
from crunch_node.services.trading.costs import CostModel
from crunch_node.services.trading.simulator import TradingEngine
from crunch_node.services.trading.sink import SimulatorSink

ZERO_COST = CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0)
DEFAULT_TRADING_CONFIG = TradingConfig(cost_model=ZERO_COST)


class TestFullFlow:
    def test_order_tick_snapshot_close(self):
        sim = TradingEngine(cost_model=ZERO_COST)
        state_repo = MagicMock()
        sink = SimulatorSink(
            simulator=sim,
            state_repository=state_repo,
            trading_config=DEFAULT_TRADING_CONFIG,
            model_ids=["model_1"],
        )

        now = datetime.now(UTC)
        ts_ms = int(now.timestamp() * 1000)

        sim.apply_order("model_1", "BTC", "long", 1.0, price=50000.0, timestamp=now)

        record = FeedDataRecord(
            source="binance",
            subject="BTCUSDT",
            kind="candle",
            granularity="1m",
            ts_event=ts_ms,
            values={"close": 51000.0},
        )
        asyncio.run(sink.on_record(record))

        state_repo.save_state.assert_not_called()
        snapshot = sim.get_portfolio_snapshot("model_1", now)
        assert snapshot["total_unrealized_pnl"] > 0
        assert snapshot["total_realized_pnl"] == 0
        assert snapshot["open_position_count"] == 1

        sim.apply_order(
            "model_1", "BTC", "short", 1.0, price=51000.0, timestamp=now
        )

        record2 = FeedDataRecord(
            source="binance",
            subject="BTCUSDT",
            kind="candle",
            granularity="1m",
            ts_event=ts_ms,
            values={"close": 51000.0},
        )
        asyncio.run(sink.on_record(record2))

        snapshot2 = sim.get_portfolio_snapshot("model_1", now)
        expected_realized = 1.0 * (51000.0 - 50000.0) / 50000.0
        assert snapshot2["total_realized_pnl"] == pytest.approx(expected_realized)
        assert snapshot2["total_unrealized_pnl"] == 0
        assert snapshot2["open_position_count"] == 0

    def test_hook_to_tick_flow(self):
        from crunch_node.entities.prediction import (
            InputRecord,
            PredictionRecord,
            PredictionStatus,
        )

        sim = TradingEngine(cost_model=ZERO_COST)
        state_repo = MagicMock()
        sink = SimulatorSink(
            simulator=sim,
            state_repository=state_repo,
            trading_config=DEFAULT_TRADING_CONFIG,
        )

        now = datetime.now(UTC)
        ts_ms = int(now.timestamp() * 1000)

        feed_record = FeedDataRecord(
            source="binance",
            subject="BTCUSDT",
            kind="candle",
            granularity="1m",
            ts_event=ts_ms,
            values={"close": 50000.0},
        )
        asyncio.run(sink.on_record(feed_record))

        inp = InputRecord(id="INP_1", raw_data={}, received_at=now)
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
        state_repo.save_state.assert_called_once()

        tick_record = FeedDataRecord(
            source="binance",
            subject="BTCUSDT",
            kind="candle",
            granularity="1m",
            ts_event=ts_ms,
            values={"close": 52000.0},
        )
        asyncio.run(sink.on_record(tick_record))

        snapshot = sim.get_portfolio_snapshot("model_1", now)
        expected_pnl = 1.0 * (52000.0 - 50000.0) / 50000.0
        assert snapshot["total_unrealized_pnl"] == pytest.approx(expected_pnl)

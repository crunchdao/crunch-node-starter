from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from crunch_node.feeds.contracts import FeedDataRecord
from crunch_node.services.trading.costs import CostModel
from crunch_node.services.trading.simulator import TradingEngine
from crunch_node.services.trading.sink import SimulatorSink

ZERO_COST = CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0)


class TestExtractPrice:
    def test_extract_price_from_candle(self):
        record = FeedDataRecord(
            source="binance",
            subject="BTCUSDT",
            kind="candle",
            granularity="1m",
            ts_event=1000,
            values={
                "open": 49900,
                "high": 50100,
                "low": 49800,
                "close": 50000.0,
                "volume": 100,
            },
        )
        assert SimulatorSink.extract_price(record) == 50000.0

    def test_extract_price_from_tick(self):
        record = FeedDataRecord(
            source="pyth",
            subject="BTC",
            kind="tick",
            granularity="1s",
            ts_event=1000,
            values={"price": 50000.0},
        )
        assert SimulatorSink.extract_price(record) == 50000.0

    def test_extract_price_returns_none_when_missing(self):
        record = FeedDataRecord(
            source="custom",
            subject="X",
            kind="depth",
            granularity="1s",
            ts_event=1000,
            values={"bid": 100, "ask": 101},
        )
        assert SimulatorSink.extract_price(record) is None


class TestOnRecord:
    def test_on_record_marks_to_market(self):
        sim = TradingEngine(cost_model=ZERO_COST)
        state_repo = MagicMock()
        sink = SimulatorSink(
            simulator=sim, state_repository=state_repo, model_ids=["model_1"]
        )

        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=now)

        record = FeedDataRecord(
            source="binance",
            subject="BTCUSDT",
            kind="candle",
            granularity="1m",
            ts_event=int(now.timestamp() * 1000),
            values={"close": 51000.0},
        )
        asyncio.run(sink.on_record(record))

        pos = sim.get_position("model_1", "BTCUSDT")
        assert pos.current_price == 51000.0

    def test_on_record_persists_state(self):
        sim = TradingEngine(cost_model=ZERO_COST)
        state_repo = MagicMock()
        sink = SimulatorSink(
            simulator=sim, state_repository=state_repo, model_ids=["model_1"]
        )

        now = datetime.now(UTC)
        sim.apply_order("model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=now)

        record = FeedDataRecord(
            source="binance",
            subject="BTCUSDT",
            kind="candle",
            granularity="1m",
            ts_event=int(now.timestamp() * 1000),
            values={"close": 51000.0},
        )
        asyncio.run(sink.on_record(record))

        state_repo.save_state.assert_called_once()

    def test_on_record_skips_when_no_price(self):
        sim = TradingEngine(cost_model=ZERO_COST)
        state_repo = MagicMock()
        sink = SimulatorSink(
            simulator=sim, state_repository=state_repo, model_ids=["model_1"]
        )

        record = FeedDataRecord(
            source="custom",
            subject="X",
            kind="depth",
            granularity="1s",
            ts_event=1000,
            values={"bid": 100, "ask": 101},
        )
        asyncio.run(sink.on_record(record))

        state_repo.save_state.assert_not_called()

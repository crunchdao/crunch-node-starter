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


class InMemoryTradingStateRepository:
    def __init__(self):
        self._states = {}

    def save_state(self, model_id, positions, trades, portfolio_fees, closed_carry):
        self._states[model_id] = {
            "model_id": model_id,
            "positions": [
                {
                    "subject": p.subject,
                    "direction": p.direction,
                    "leverage": p.leverage,
                    "entry_price": p.entry_price,
                    "opened_at": p.opened_at.isoformat(),
                    "current_price": p.current_price,
                    "accrued_carry": p.accrued_carry,
                }
                for p in positions
            ],
            "trades": [
                {
                    "subject": t.subject,
                    "direction": t.direction,
                    "leverage": t.leverage,
                    "entry_price": t.entry_price,
                    "opened_at": t.opened_at.isoformat(),
                    "exit_price": t.exit_price,
                    "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                    "realized_pnl": t.realized_pnl,
                    "fees_paid": t.fees_paid,
                }
                for t in trades
            ],
            "portfolio_fees": portfolio_fees,
            "closed_carry": closed_carry,
            "updated_at": datetime.now(UTC),
        }

    def load_state(self, model_id):
        return self._states.get(model_id)

    def get_all_model_ids(self):
        return list(self._states.keys())


class TestPersistToScoreFlow:
    def test_predict_persists_score_reads(self):
        from crunch_node.services.score import ScoreService

        state_repo = InMemoryTradingStateRepository()

        sim = TradingEngine(cost_model=ZERO_COST)
        sink = SimulatorSink(
            simulator=sim,
            state_repository=state_repo,
            model_ids=["model_1"],
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

        assert state_repo.load_state("model_1") is not None

        snapshot_repo = MagicMock()
        snapshot_repo.find = MagicMock(return_value=[])
        leaderboard_repo = MagicMock()

        score_service = ScoreService(
            checkpoint_interval_seconds=300,
            scoring_function=lambda p, g: MagicMock(),
            snapshot_repository=snapshot_repo,
            model_repository=MagicMock(fetch_all=MagicMock(return_value={})),
            leaderboard_repository=leaderboard_repo,
            prediction_repository=MagicMock(find=MagicMock(return_value=[])),
            trading_state_repository=state_repo,
        )

        result = score_service.score_and_snapshot()
        assert result is True

        snapshot_repo.save.assert_called_once()
        snap = snapshot_repo.save.call_args[0][0]
        assert snap.model_id == "model_1"
        expected_pnl = 1.0 * (51000.0 - 50000.0) / 50000.0
        assert snap.result_summary["unrealized_pnl"] == pytest.approx(expected_pnl)
        assert snap.result_summary["net_pnl"] == pytest.approx(expected_pnl)

    def test_crash_recovery_then_score(self):
        state_repo = InMemoryTradingStateRepository()

        sim1 = TradingEngine(cost_model=ZERO_COST)
        sink1 = SimulatorSink(
            simulator=sim1,
            state_repository=state_repo,
            model_ids=["model_1"],
        )
        now = datetime.now(UTC)
        sim1.apply_order(
            "model_1", "BTCUSDT", "long", 1.0, price=50000.0, timestamp=now
        )

        record = FeedDataRecord(
            source="binance",
            subject="BTCUSDT",
            kind="candle",
            granularity="1m",
            ts_event=int(now.timestamp() * 1000),
            values={"close": 51000.0},
        )
        asyncio.run(sink1.on_record(record))

        sim2 = TradingEngine(cost_model=ZERO_COST)
        state = state_repo.load_state("model_1")
        sim2.load_state("model_1", state)

        pos = sim2.get_position("model_1", "BTCUSDT")
        assert pos is not None
        assert pos.direction == "long"
        assert pos.current_price == pytest.approx(51000.0)

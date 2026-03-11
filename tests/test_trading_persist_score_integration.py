from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from crunch_node.entities.prediction import (
    InputRecord,
    PredictionRecord,
    PredictionStatus,
    SnapshotRecord,
)
from crunch_node.feeds.contracts import FeedDataRecord
from extensions.trading.config import TradingConfig
from extensions.trading.costs import CostModel
from extensions.trading.simulator import TradingEngine
from extensions.trading.sink import SimulatorSink

ZERO_COST = CostModel(trading_fee_pct=0.0, spread_pct=0.0, carry_annual_pct=0.0)
DEFAULT_TRADING_CONFIG = TradingConfig(cost_model=ZERO_COST)


def _feed_record(subject: str, close: float, ts_ms: int) -> FeedDataRecord:
    return FeedDataRecord(
        source="binance",
        subject=subject,
        kind="candle",
        granularity="1m",
        ts_event=ts_ms,
        values={"close": close},
    )


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
                    "size": p.size,
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
                    "size": t.size,
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
            "updated_at": datetime.now(timezone.utc),
        }

    def load_state(self, model_id):
        return self._states.get(model_id)

    def get_all_model_ids(self):
        return list(self._states.keys())


def _make_build_snapshots_fn(state_repo):
    def build_snapshots(now):
        model_ids = state_repo.get_all_model_ids()
        if not model_ids:
            return []

        snapshots = []
        for model_id in model_ids:
            state = state_repo.load_state(model_id)
            if state is None:
                continue

            positions_data = state.get("positions", [])
            total_unrealized = 0.0
            for p in positions_data:
                entry = p["entry_price"]
                current = p.get("current_price", entry)
                size = p["size"]
                if entry > 0:
                    price_return = (current - entry) / entry
                    if p["direction"] == "short":
                        price_return = -price_return
                    total_unrealized += size * price_return

            trades_data = state.get("trades", [])
            total_realized = sum(
                t.get("realized_pnl", 0.0) or 0.0 for t in trades_data
            )
            portfolio_fees = state.get("portfolio_fees", 0.0)
            closed_carry = state.get("closed_carry", 0.0)
            total_carry = (
                sum(p.get("accrued_carry", 0.0) for p in positions_data) + closed_carry
            )
            net_pnl = total_unrealized + total_realized - portfolio_fees - total_carry

            snapshots.append(
                SnapshotRecord(
                    id=f"SNAP_{model_id}_{now.strftime('%Y%m%d_%H%M%S')}",
                    model_id=model_id,
                    period_start=now,
                    period_end=now,
                    prediction_count=len(positions_data),
                    result_summary={
                        "net_pnl": net_pnl,
                        "unrealized_pnl": total_unrealized,
                        "realized_pnl": total_realized,
                        "total_fees": portfolio_fees,
                        "total_carry_costs": total_carry,
                        "open_position_count": len(positions_data),
                    },
                )
            )
        return snapshots

    return build_snapshots


class TestPersistToScoreFlow:
    def test_predict_persists_score_reads(self):
        from crunch_node.services.score import ScoreService

        state_repo = InMemoryTradingStateRepository()

        sim = TradingEngine(cost_model=ZERO_COST)
        sink = SimulatorSink(
            simulator=sim,
            state_repository=state_repo,
            trading_config=DEFAULT_TRADING_CONFIG,
        )

        now = datetime.now(timezone.utc)
        ts_ms = int(now.timestamp())

        asyncio.run(sink.on_record(_feed_record("BTCUSDT", 50000.0, ts_ms)))

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

        asyncio.run(sink.on_record(_feed_record("BTCUSDT", 51000.0, ts_ms)))

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
            build_snapshots_fn=_make_build_snapshots_fn(state_repo),
        )

        result = score_service.score_and_snapshot()
        assert result is True

        snapshot_repo.save.assert_called_once()
        snap = snapshot_repo.save.call_args[0][0]
        assert snap.model_id == "model_1"
        assert snap.result_summary["unrealized_pnl"] == pytest.approx(0.0)
        assert snap.result_summary["net_pnl"] == pytest.approx(0.0)

    def test_crash_recovery_then_score(self):
        state_repo = InMemoryTradingStateRepository()

        sim1 = TradingEngine(cost_model=ZERO_COST)
        sink1 = SimulatorSink(
            simulator=sim1,
            state_repository=state_repo,
            trading_config=DEFAULT_TRADING_CONFIG,
        )
        now = datetime.now(timezone.utc)
        ts_ms = int(now.timestamp())

        asyncio.run(sink1.on_record(_feed_record("BTCUSDT", 50000.0, ts_ms)))

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
        sink1.on_predictions(predictions, inp, now)

        asyncio.run(sink1.on_record(_feed_record("BTCUSDT", 51000.0, ts_ms)))

        sim2 = TradingEngine(cost_model=ZERO_COST)
        state = state_repo.load_state("model_1")
        sim2.load_state("model_1", state)

        pos = sim2.get_position("model_1", "BTC")
        assert pos is not None
        assert pos.direction == "long"
        assert pos.current_price == pytest.approx(50000.0)

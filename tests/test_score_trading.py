from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from crunch_node.services.score import ScoreService


class TestTradingScoring:
    def _make_service(self, trading_state_repo=None):
        return ScoreService(
            checkpoint_interval_seconds=300,
            scoring_function=lambda p, g: MagicMock(model_dump=lambda: {"value": 0}),
            snapshot_repository=MagicMock(),
            model_repository=MagicMock(fetch_all=MagicMock(return_value={})),
            leaderboard_repository=MagicMock(),
            prediction_repository=MagicMock(find=MagicMock(return_value=[])),
            trading_state_repository=trading_state_repo,
        )

    def test_trading_score_reads_portfolio_state(self):
        state_repo = MagicMock()
        state_repo.get_all_model_ids.return_value = ["m1"]
        state_repo.load_state.return_value = {
            "model_id": "m1",
            "positions": [
                {
                    "subject": "BTCUSDT",
                    "direction": "long",
                    "leverage": 0.5,
                    "entry_price": 50000.0,
                    "opened_at": "2026-01-01T00:00:00+00:00",
                    "current_price": 51000.0,
                    "accrued_carry": 0.0,
                }
            ],
            "trades": [],
            "portfolio_fees": 0.0005,
            "closed_carry": 0.0,
            "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
        }

        service = self._make_service(trading_state_repo=state_repo)
        result = service.score_and_snapshot()

        assert result is True
        service.snapshot_repository.save.assert_called_once()
        snap = service.snapshot_repository.save.call_args[0][0]
        assert snap.model_id == "m1"
        assert "net_pnl" in snap.result_summary

    def test_no_trading_state_falls_through_to_prediction_scoring(self):
        service = self._make_service(trading_state_repo=None)
        result = service.score_and_snapshot()
        assert result is False

    def test_trading_snapshot_contains_portfolio_metrics(self):
        state_repo = MagicMock()
        state_repo.get_all_model_ids.return_value = ["m1"]
        state_repo.load_state.return_value = {
            "model_id": "m1",
            "positions": [
                {
                    "subject": "BTCUSDT",
                    "direction": "long",
                    "leverage": 1.0,
                    "entry_price": 50000.0,
                    "opened_at": "2026-01-01T00:00:00+00:00",
                    "current_price": 51000.0,
                    "accrued_carry": 0.001,
                }
            ],
            "trades": [
                {
                    "subject": "ETHUSDT",
                    "direction": "short",
                    "leverage": 0.3,
                    "entry_price": 3000.0,
                    "opened_at": "2026-01-01T00:00:00+00:00",
                    "exit_price": 2900.0,
                    "closed_at": "2026-01-02T00:00:00+00:00",
                    "realized_pnl": 0.01,
                    "fees_paid": 0.0003,
                }
            ],
            "portfolio_fees": 0.001,
            "closed_carry": 0.0002,
            "updated_at": datetime(2026, 1, 2, tzinfo=UTC),
        }

        service = self._make_service(trading_state_repo=state_repo)
        service.score_and_snapshot()

        snap = service.snapshot_repository.save.call_args[0][0]
        summary = snap.result_summary
        assert "unrealized_pnl" in summary
        assert "realized_pnl" in summary
        assert "total_fees" in summary
        assert "total_carry_costs" in summary
        assert "open_position_count" in summary
        assert "net_pnl" in summary

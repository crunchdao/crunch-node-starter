from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from extensions.trading.models import Position, Trade


class TestTradingStateRepository:
    def test_save_state(self):
        from extensions.trading.state_repository import TradingStateRepository

        session = MagicMock()
        session.get.return_value = None
        repo = TradingStateRepository(session)

        positions = [
            Position(
                model_id="m1",
                subject="BTCUSDT",
                direction="long",
                size=0.5,
                entry_price=50000.0,
                opened_at=datetime(2026, 1, 1, tzinfo=UTC),
                current_price=51000.0,
                accrued_carry=0.001,
            )
        ]
        trades = [
            Trade(
                model_id="m1",
                subject="BTCUSDT",
                direction="long",
                size=0.3,
                entry_price=49000.0,
                opened_at=datetime(2026, 1, 1, tzinfo=UTC),
                exit_price=50000.0,
                closed_at=datetime(2026, 1, 2, tzinfo=UTC),
                realized_pnl=0.006,
                fees_paid=0.0003,
            )
        ]

        repo.save_state(
            "m1", positions, trades, portfolio_fees=0.001, closed_carry=0.0002
        )

        session.add.assert_called_once()
        session.commit.assert_called_once()

    def test_save_state_updates_existing(self):
        from extensions.trading.tables import TradingStateRow
        from extensions.trading.state_repository import TradingStateRepository

        existing_row = TradingStateRow(
            model_id="m1",
            positions_jsonb=[],
            trades_jsonb=[],
            portfolio_fees=0.0,
            closed_carry=0.0,
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session = MagicMock()
        session.get.return_value = existing_row
        repo = TradingStateRepository(session)

        repo.save_state("m1", [], [], portfolio_fees=0.005, closed_carry=0.001)

        session.add.assert_not_called()
        session.commit.assert_called_once()
        assert existing_row.portfolio_fees == 0.005
        assert existing_row.closed_carry == 0.001

    def test_load_state_returns_none_when_missing(self):
        from extensions.trading.state_repository import TradingStateRepository

        session = MagicMock()
        session.get.return_value = None
        repo = TradingStateRepository(session)

        result = repo.load_state("m1")
        assert result is None

    def test_load_state_returns_dict(self):
        from extensions.trading.tables import TradingStateRow
        from extensions.trading.state_repository import TradingStateRepository

        row = TradingStateRow(
            model_id="m1",
            positions_jsonb=[
                {
                    "subject": "BTCUSDT",
                    "direction": "long",
                    "size": 0.5,
                    "entry_price": 50000.0,
                    "opened_at": "2026-01-01T00:00:00+00:00",
                    "current_price": 51000.0,
                    "accrued_carry": 0.001,
                }
            ],
            trades_jsonb=[],
            portfolio_fees=0.001,
            closed_carry=0.0,
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session = MagicMock()
        session.get.return_value = row
        repo = TradingStateRepository(session)

        result = repo.load_state("m1")
        assert result is not None
        assert result["model_id"] == "m1"
        assert len(result["positions"]) == 1
        assert result["portfolio_fees"] == 0.001

    def test_load_all_model_ids(self):
        from extensions.trading.state_repository import TradingStateRepository

        session = MagicMock()
        repo = TradingStateRepository(session)

        mock_result = MagicMock()
        mock_result.all.return_value = [("m1",), ("m2",)]
        session.exec.return_value = mock_result

        ids = repo.get_all_model_ids()
        assert ids == ["m1", "m2"]

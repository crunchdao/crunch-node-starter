from __future__ import annotations

from datetime import UTC, datetime


class TestTradingStateRow:
    def test_row_creation(self):
        from crunch_node.db.tables.trading import TradingStateRow

        row = TradingStateRow(
            model_id="model_1",
            positions_jsonb=[
                {
                    "subject": "BTCUSDT",
                    "direction": "long",
                    "leverage": 0.5,
                    "entry_price": 50000.0,
                    "opened_at": "2026-01-01T00:00:00+00:00",
                    "current_price": 51000.0,
                    "accrued_carry": 0.001,
                }
            ],
            trades_jsonb=[],
            portfolio_fees=0.0005,
            closed_carry=0.0,
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert row.model_id == "model_1"
        assert len(row.positions_jsonb) == 1
        assert row.portfolio_fees == 0.0005

    def test_row_has_table_name(self):
        from crunch_node.db.tables.trading import TradingStateRow

        assert TradingStateRow.__tablename__ == "trading_portfolio_state"

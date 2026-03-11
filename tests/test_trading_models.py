from datetime import UTC, datetime

import pytest

from extensions.trading.models import Position, Trade


class TestPositionUnrealizedPnl:
    def test_position_unrealized_pnl_long(self):
        pos = Position(
            model_id="m1",
            subject="BTCUSDT",
            direction="long",
            size=0.5,
            entry_price=100.0,
            opened_at=datetime(2026, 1, 1, tzinfo=UTC),
            current_price=120.0,
        )
        assert pos.unrealized_pnl == 0.5 * (120.0 - 100.0) / 100.0

    def test_position_unrealized_pnl_short(self):
        pos = Position(
            model_id="m1",
            subject="BTCUSDT",
            direction="short",
            size=0.5,
            entry_price=100.0,
            opened_at=datetime(2026, 1, 1, tzinfo=UTC),
            current_price=80.0,
        )
        assert pos.unrealized_pnl == 0.5 * (100.0 - 80.0) / 100.0

    def test_position_unrealized_pnl_zero_entry_raises(self):
        pos = Position(
            model_id="m1",
            subject="BTCUSDT",
            direction="long",
            size=1.0,
            entry_price=0.0,
            opened_at=datetime(2026, 1, 1, tzinfo=UTC),
            current_price=50.0,
        )
        with pytest.raises(ValueError, match="entry_price is zero"):
            _ = pos.unrealized_pnl


class TestTradeRecord:
    def test_trade_record_creation(self):
        trade = Trade(
            model_id="m1",
            subject="BTCUSDT",
            direction="long",
            size=0.5,
            entry_price=100.0,
            opened_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert trade.exit_price is None
        assert trade.realized_pnl is None
        assert trade.closed_at is None
        assert trade.fees_paid == 0.0

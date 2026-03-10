from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from crunch_node.db.tables.trading import TradingStateRow
from crunch_node.services.trading.models import Position, Trade


class TradingStateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save_state(
        self,
        model_id: str,
        positions: list[Position],
        trades: list[Trade],
        portfolio_fees: float,
        closed_carry: float,
    ) -> None:
        positions_data = [
            {
                "subject": p.subject,
                "direction": p.direction,
                "size": p.size,
                "entry_price": p.entry_price,
                "opened_at": p.opened_at.astimezone(UTC).isoformat(),
                "current_price": p.current_price,
                "accrued_carry": p.accrued_carry,
            }
            for p in positions
        ]
        trades_data = [
            {
                "subject": t.subject,
                "direction": t.direction,
                "size": t.size,
                "entry_price": t.entry_price,
                "opened_at": t.opened_at.astimezone(UTC).isoformat(),
                "exit_price": t.exit_price,
                "closed_at": t.closed_at.astimezone(UTC).isoformat() if t.closed_at else None,
                "realized_pnl": t.realized_pnl,
                "fees_paid": t.fees_paid,
            }
            for t in trades
        ]

        existing = self._session.get(TradingStateRow, model_id)
        if existing is None:
            row = TradingStateRow(
                model_id=model_id,
                positions_jsonb=positions_data,
                trades_jsonb=trades_data,
                portfolio_fees=portfolio_fees,
                closed_carry=closed_carry,
                updated_at=datetime.now(UTC),
            )
            self._session.add(row)
        else:
            existing.positions_jsonb = positions_data
            existing.trades_jsonb = trades_data
            existing.portfolio_fees = portfolio_fees
            existing.closed_carry = closed_carry
            existing.updated_at = datetime.now(UTC)

        self._session.commit()

    def load_state(self, model_id: str) -> dict[str, Any] | None:
        row = self._session.get(TradingStateRow, model_id)
        if row is None:
            return None
        return {
            "model_id": row.model_id,
            "positions": row.positions_jsonb,
            "trades": row.trades_jsonb,
            "portfolio_fees": row.portfolio_fees,
            "closed_carry": row.closed_carry,
            "updated_at": row.updated_at,
        }

    def get_all_model_ids(self) -> list[str]:
        stmt = select(TradingStateRow.model_id)
        result = self._session.exec(stmt).all()
        return [r[0] if isinstance(r, tuple) else r for r in result]

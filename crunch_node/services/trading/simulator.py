from __future__ import annotations

from datetime import datetime
from typing import Any

from crunch_node.services.trading.costs import CostModel
from crunch_node.services.trading.models import Direction, Position, Trade


class TradingSimulator:
    def __init__(self, cost_model: CostModel) -> None:
        self._cost_model = cost_model
        self._positions: dict[tuple[str, str], Position] = {}
        self._trades: dict[str, list[Trade]] = {}
        self._portfolio_fees: dict[str, float] = {}

    def apply_order(
        self,
        model_id: str,
        subject: str,
        direction: Direction,
        leverage: float,
        *,
        price: float,
        timestamp: datetime,
    ) -> None:
        if leverage <= 0:
            raise ValueError("Leverage must be positive")

        fee = self._cost_model.order_cost(leverage)
        self._portfolio_fees[model_id] = self._portfolio_fees.get(model_id, 0.0) + fee

        key = (model_id, subject)
        existing = self._positions.get(key)

        if existing is None:
            self._positions[key] = Position(
                model_id=model_id,
                subject=subject,
                direction=direction,
                leverage=leverage,
                entry_price=price,
                opened_at=timestamp,
                current_price=price,
            )
            return

        if existing.direction == direction:
            new_leverage = existing.leverage + leverage
            existing.entry_price = (
                existing.entry_price * existing.leverage + price * leverage
            ) / new_leverage
            existing.leverage = new_leverage
            return

        self._apply_opposite_order(existing, key, direction, leverage, price, timestamp)

    def _apply_opposite_order(
        self,
        existing: Position,
        key: tuple[str, str],
        direction: Direction,
        leverage: float,
        price: float,
        timestamp: datetime,
    ) -> None:
        model_id, subject = key

        if leverage < existing.leverage:
            existing.leverage -= leverage
            return

        pnl = self._compute_realized_pnl(existing, price)
        trade = Trade(
            model_id=model_id,
            subject=subject,
            direction=existing.direction,
            leverage=existing.leverage,
            entry_price=existing.entry_price,
            opened_at=existing.opened_at,
            exit_price=price,
            closed_at=timestamp,
            realized_pnl=pnl,
        )
        self._trades.setdefault(model_id, []).append(trade)

        remainder = leverage - existing.leverage
        del self._positions[key]

        if remainder > 0:
            self._positions[key] = Position(
                model_id=model_id,
                subject=subject,
                direction=direction,
                leverage=remainder,
                entry_price=price,
                opened_at=timestamp,
                current_price=price,
            )

    def _compute_realized_pnl(self, position: Position, exit_price: float) -> float:
        if position.entry_price == 0.0:
            raise ValueError("Cannot compute PnL with zero entry price")
        price_return = (exit_price - position.entry_price) / position.entry_price
        if position.direction == "short":
            price_return = -price_return
        return position.leverage * price_return

    def mark_to_market(self, subject: str, price: float, timestamp: datetime) -> None:
        for key, position in self._positions.items():
            if key[1] == subject:
                position.current_price = price

    def get_position(self, model_id: str, subject: str) -> Position | None:
        return self._positions.get((model_id, subject))

    def get_trades(self, model_id: str) -> list[Trade]:
        return self._trades.get(model_id, [])

    def get_portfolio_snapshot(self, model_id: str, timestamp: datetime) -> dict[str, Any]:
        positions = [
            pos for key, pos in self._positions.items() if key[0] == model_id
        ]
        total_unrealized = sum(p.unrealized_pnl for p in positions)
        total_fees = self._portfolio_fees.get(model_id, 0.0)

        return {
            "model_id": model_id,
            "timestamp": timestamp,
            "positions": positions,
            "total_unrealized_pnl": total_unrealized,
            "total_fees": total_fees,
            "net_pnl": total_unrealized - total_fees,
        }

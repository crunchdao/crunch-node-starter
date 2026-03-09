from __future__ import annotations

from datetime import datetime
from typing import Any

from crunch_node.services.trading.costs import CostModel
from crunch_node.services.trading.models import Direction, Position, Trade


class TradingSimulator:
    def __init__(
        self,
        cost_model: CostModel,
        max_position_leverage: float = 10.0,
        max_portfolio_leverage: float = 20.0,
    ) -> None:
        self._cost_model = cost_model
        self._max_position_leverage = max_position_leverage
        self._max_portfolio_leverage = max_portfolio_leverage
        self._positions: dict[tuple[str, str], Position] = {}
        self._trades: dict[str, list[Trade]] = {}
        self._portfolio_fees: dict[str, float] = {}
        self._last_mark_at: dict[tuple[str, str], datetime] = {}
        self._closed_carry: dict[str, float] = {}

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
        if direction not in ("long", "short"):
            raise ValueError("Direction must be 'long' or 'short'")

        key = (model_id, subject)
        existing = self._positions.get(key)

        if existing is not None and existing.direction == direction:
            leverage = min(leverage, max(0.0, self._max_position_leverage - existing.leverage))
        else:
            leverage = min(leverage, self._max_position_leverage)

        current_portfolio_leverage = sum(
            p.leverage for k, p in self._positions.items() if k[0] == model_id
        )
        leverage = min(leverage, max(0.0, self._max_portfolio_leverage - current_portfolio_leverage))

        if leverage <= 0:
            return

        fee = self._cost_model.order_cost(leverage)
        self._portfolio_fees[model_id] = self._portfolio_fees.get(model_id, 0.0) + fee

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
            self._last_mark_at[key] = timestamp
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
            partial_pnl = self._compute_realized_pnl(existing, price) * (leverage / existing.leverage)
            trade = Trade(
                model_id=model_id,
                subject=subject,
                direction=existing.direction,
                leverage=leverage,
                entry_price=existing.entry_price,
                opened_at=existing.opened_at,
                exit_price=price,
                closed_at=timestamp,
                realized_pnl=partial_pnl,
            )
            self._trades.setdefault(model_id, []).append(trade)
            partial_ratio = leverage / existing.leverage
            partial_carry = existing.accrued_carry * partial_ratio
            self._closed_carry[model_id] = self._closed_carry.get(model_id, 0.0) + partial_carry
            existing.accrued_carry -= partial_carry
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

        self._closed_carry[model_id] = self._closed_carry.get(model_id, 0.0) + existing.accrued_carry
        remainder = leverage - existing.leverage
        del self._positions[key]
        self._last_mark_at.pop(key, None)

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
            self._last_mark_at[key] = timestamp

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
                last_mark = self._last_mark_at.get(key, position.opened_at)
                elapsed = (timestamp - last_mark).total_seconds()
                position.accrued_carry += self._cost_model.carry_cost(position.leverage, elapsed)
                self._last_mark_at[key] = timestamp
                position.current_price = price

    def get_position(self, model_id: str, subject: str) -> Position | None:
        return self._positions.get((model_id, subject))

    def get_all_positions(self, model_id: str) -> list[Position]:
        return [pos for key, pos in self._positions.items() if key[0] == model_id]

    def get_trades(self, model_id: str) -> list[Trade]:
        return list(self._trades.get(model_id, []))

    def get_portfolio_snapshot(self, model_id: str, timestamp: datetime) -> dict[str, Any]:
        positions = [
            pos for key, pos in self._positions.items() if key[0] == model_id
        ]
        total_unrealized = sum(p.unrealized_pnl for p in positions)
        total_realized = sum(
            t.realized_pnl for t in self._trades.get(model_id, []) if t.realized_pnl is not None
        )
        total_fees = self._portfolio_fees.get(model_id, 0.0)
        total_carry = (
            sum(p.accrued_carry for p in positions)
            + self._closed_carry.get(model_id, 0.0)
        )

        return {
            "model_id": model_id,
            "timestamp": timestamp,
            "positions": positions,
            "open_position_count": len(positions),
            "total_unrealized_pnl": total_unrealized,
            "total_realized_pnl": total_realized,
            "total_fees": total_fees,
            "total_carry_costs": total_carry,
            "net_pnl": total_unrealized + total_realized - total_fees - total_carry,
        }

    def get_full_state(self, model_id: str) -> dict[str, Any]:
        return {
            "positions": self.get_all_positions(model_id),
            "trades": self.get_trades(model_id),
            "portfolio_fees": self._portfolio_fees.get(model_id, 0.0),
            "closed_carry": self._closed_carry.get(model_id, 0.0),
        }

    def load_state(self, model_id: str, state: dict[str, Any]) -> None:
        for pos_data in state.get("positions", []):
            if isinstance(pos_data, Position):
                pos = pos_data
            else:
                opened_at = pos_data["opened_at"]
                if isinstance(opened_at, str):
                    opened_at = datetime.fromisoformat(opened_at)
                pos = Position(
                    model_id=model_id,
                    subject=pos_data["subject"],
                    direction=pos_data["direction"],
                    leverage=pos_data["leverage"],
                    entry_price=pos_data["entry_price"],
                    opened_at=opened_at,
                    current_price=pos_data.get("current_price", 0.0),
                    accrued_carry=pos_data.get("accrued_carry", 0.0),
                )
            key = (model_id, pos.subject)
            self._positions[key] = pos
            self._last_mark_at[key] = pos.opened_at

        for trade_data in state.get("trades", []):
            if isinstance(trade_data, Trade):
                trade = trade_data
            else:
                opened_at = trade_data["opened_at"]
                if isinstance(opened_at, str):
                    opened_at = datetime.fromisoformat(opened_at)
                closed_at = trade_data.get("closed_at")
                if isinstance(closed_at, str):
                    closed_at = datetime.fromisoformat(closed_at)
                trade = Trade(
                    model_id=model_id,
                    subject=trade_data["subject"],
                    direction=trade_data["direction"],
                    leverage=trade_data["leverage"],
                    entry_price=trade_data["entry_price"],
                    opened_at=opened_at,
                    exit_price=trade_data.get("exit_price"),
                    closed_at=closed_at,
                    realized_pnl=trade_data.get("realized_pnl"),
                    fees_paid=trade_data.get("fees_paid", 0.0),
                )
            self._trades.setdefault(model_id, []).append(trade)

        self._portfolio_fees[model_id] = state.get("portfolio_fees", 0.0)
        self._closed_carry[model_id] = state.get("closed_carry", 0.0)

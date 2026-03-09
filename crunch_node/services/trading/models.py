from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Direction = Literal["long", "short"]


@dataclass
class Position:
    model_id: str
    subject: str
    direction: Direction
    leverage: float
    entry_price: float
    opened_at: datetime
    current_price: float = 0.0
    accrued_carry: float = 0.0

    @property
    def unrealized_pnl(self) -> float:
        if self.entry_price == 0.0:
            return 0.0
        price_return = (self.current_price - self.entry_price) / self.entry_price
        if self.direction == "short":
            price_return = -price_return
        return self.leverage * price_return


@dataclass(frozen=True)
class Trade:
    model_id: str
    subject: str
    direction: Direction
    leverage: float
    entry_price: float
    opened_at: datetime
    exit_price: float | None = None
    closed_at: datetime | None = None
    realized_pnl: float | None = None
    fees_paid: float = 0.0

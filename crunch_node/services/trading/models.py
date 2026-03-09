from dataclasses import dataclass
from datetime import datetime


@dataclass
class Position:
    model_id: str
    subject: str
    direction: str
    leverage: float
    entry_price: float
    opened_at: datetime
    current_price: float = 0.0
    accrued_carry: float = 0.0

    @property
    def unrealized_pnl(self) -> float:
        if self.entry_price == 0.0:
            return 0.0
        if self.direction == "long":
            return self.leverage * (self.current_price - self.entry_price) / self.entry_price
        return self.leverage * (self.entry_price - self.current_price) / self.entry_price


@dataclass
class Trade:
    model_id: str
    subject: str
    direction: str
    leverage: float
    entry_price: float
    opened_at: datetime
    exit_price: float | None = None
    closed_at: datetime | None = None
    realized_pnl: float | None = None
    fees_paid: float = 0.0

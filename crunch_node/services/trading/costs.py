from __future__ import annotations

from pydantic import BaseModel, Field


class CostModel(BaseModel):
    trading_fee_pct: float = Field(default=0.001, ge=0)
    spread_pct: float = Field(default=0.0001, ge=0)
    carry_annual_pct: float = Field(default=0.1095, ge=0)

    def order_cost(self, size: float) -> float:
        return (self.trading_fee_pct + self.spread_pct) * abs(size)

    def carry_cost(self, size: float, seconds: float) -> float:
        return self.carry_annual_pct * abs(size) * seconds / (365 * 86400)

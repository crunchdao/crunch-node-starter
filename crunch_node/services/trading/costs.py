from __future__ import annotations

from pydantic import BaseModel, Field


class CostModel(BaseModel):
    trading_fee_pct: float = Field(default=0.001, description="Per-trade fee as fraction, scaled by leverage")
    spread_pct: float = Field(default=0.0001, description="Spread cost as fraction, scaled by leverage")
    carry_annual_pct: float = Field(default=0.1095, description="Annual carry cost as fraction, scaled by leverage")

    def order_cost(self, leverage: float) -> float:
        return (self.trading_fee_pct + self.spread_pct) * abs(leverage)

    def carry_cost(self, leverage: float, seconds: float) -> float:
        return self.carry_annual_pct * abs(leverage) * seconds / (365 * 86400)

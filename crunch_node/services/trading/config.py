from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from crunch_node.services.trading.costs import CostModel


class TradingConfig(BaseModel):
    cost_model: CostModel = Field(default_factory=CostModel)
    signal_mode: Literal["delta", "target", "order"] = "target"
    max_position_leverage: float = 10.0
    max_portfolio_leverage: float = 20.0

    @model_validator(mode="after")
    def _validate_leverage_limits(self) -> TradingConfig:
        if self.max_position_leverage <= 0:
            raise ValueError("max_position_leverage must be positive")
        if self.max_portfolio_leverage <= 0:
            raise ValueError("max_portfolio_leverage must be positive")
        if self.max_portfolio_leverage < self.max_position_leverage:
            raise ValueError("max_portfolio_leverage must be >= max_position_leverage")
        return self

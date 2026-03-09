from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from crunch_node.services.trading.costs import CostModel


class TradingConfig(BaseModel):
    cost_model: CostModel = Field(default_factory=CostModel)
    signal_mode: Literal["delta", "target", "order"] = "target"
    max_position_leverage: float = 10.0
    max_portfolio_leverage: float = 20.0

"""CrunchConfig for trading competitions.

Models receive live OHLCV candle data and return buy/sell orders.
PnL is tracked by the TradingEngine, not a scoring function.

Feed: Binance-style 1m candles for configurable pairs (default BTCUSDT, ETHUSDT)
Output: {"action": "buy"|"sell", "amount": float}
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from crunch_node.crunch_config import (
    Aggregation,
    ScheduledPrediction,
)
from crunch_node.crunch_config import (
    CrunchConfig as BaseCrunchConfig,
)
from crunch_node.services.realtime_predict import (
    RealtimePredictService,
    RealtimeServiceConfig,
)


class CostModel(BaseModel):
    trading_fee_pct: float = Field(default=0.001, ge=0)
    spread_pct: float = Field(default=0.0001, ge=0)
    carry_annual_pct: float = Field(default=0.1095, ge=0)

    def order_cost(self, size: float) -> float:
        return (self.trading_fee_pct + self.spread_pct) * abs(size)

    def carry_cost(self, size: float, seconds: float) -> float:
        return self.carry_annual_pct * abs(size) * seconds / (365 * 86400)


class TradingConfig(BaseModel):
    cost_model: CostModel = Field(default_factory=CostModel)
    signal_mode: Literal["delta", "target", "order"] = "target"
    max_position_size: float = 10.0
    max_portfolio_size: float = 20.0
    asset_price_mapping: dict[str, str] = Field(
        default_factory=lambda: {
            "BTC": "BTCUSDT",
            "ETH": "ETHUSDT",
        },
        description="Map asset names to trading pair subjects for price lookup",
    )

    @model_validator(mode="after")
    def _validate_size_limits(self) -> TradingConfig:
        if self.max_position_size <= 0:
            raise ValueError("max_position_size must be positive")
        if self.max_portfolio_size <= 0:
            raise ValueError("max_portfolio_size must be positive")
        if self.max_portfolio_size < self.max_position_size:
            raise ValueError("max_portfolio_size must be >= max_position_size")
        return self


class InferenceOutput(BaseModel):
    """What models must return: a buy/sell order.

    action: "buy" or "sell"
    amount: position size (notional units, must be >= 0)
    """

    action: str = "buy"
    amount: float = Field(default=0.0, ge=0.0)


class CrunchConfig(BaseCrunchConfig):
    """Trading competition configuration.

    Models return buy/sell orders, scored via TradingEngine PnL simulation.
    """

    predict_service_class: type = RealtimePredictService

    feed_normalizer: str = "candle"
    output_type: type[BaseModel] = InferenceOutput

    realtime_service: RealtimeServiceConfig = Field(
        default_factory=RealtimeServiceConfig
    )

    trading: TradingConfig = Field(
        default_factory=lambda: TradingConfig(
            signal_mode="order",
            max_position_size=1_000_000,
            max_portfolio_size=1_000_000,
            asset_price_mapping={
                "BTC": "BTCUSDT",
                "ETH": "ETHUSDT",
            },
        )
    )

    aggregation: Aggregation = Field(
        default_factory=lambda: Aggregation(
            windows={},
            value_field="net_pnl",
            ranking_key="net_pnl",
            ranking_direction="desc",
        )
    )

    metrics: list[str] = Field(
        default_factory=lambda: [
            "net_pnl",
            "hit_rate",
            "max_drawdown",
            "sortino_ratio",
        ]
    )

    scheduled_predictions: list[ScheduledPrediction] = Field(
        default_factory=lambda: [
            ScheduledPrediction(
                scope_key="trading-btc-60s",
                scope={"subject": "BTC"},
                prediction_interval_seconds=15,
                resolve_horizon_seconds=60,
            ),
            ScheduledPrediction(
                scope_key="trading-eth-60s",
                scope={"subject": "ETH"},
                prediction_interval_seconds=15,
                resolve_horizon_seconds=60,
            ),
        ]
    )

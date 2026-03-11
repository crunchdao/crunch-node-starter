"""CrunchConfig for trading competitions.

Models receive live OHLCV candle data and return buy/sell orders.
PnL is tracked by the TradingEngine, not a scoring function.

Feed: Binance-style 1m candles for configurable pairs (default BTCUSDT, ETHUSDT)
Output: {"action": "buy"|"sell", "amount": float}
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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
from extensions.trading.config import TradingConfig
from extensions.trading.costs import CostModel
from extensions.trading.factories import (
    build_simulator_sink,
    build_score_snapshots,
    build_trading_widgets,
)

import extensions.trading.tables  # noqa: F401


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

    build_simulator_sink: Callable[..., Any] | None = build_simulator_sink
    build_score_snapshots: Callable[..., Any] | None = build_score_snapshots
    build_trading_widgets: Callable[..., Any] | None = build_trading_widgets

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

    @model_validator(mode="after")
    def _derive_feed_subject_mapping(self) -> CrunchConfig:
        if not self.feed_subject_mapping:
            self.feed_subject_mapping = {
                v: k for k, v in self.trading.asset_price_mapping.items()
            }
        return self

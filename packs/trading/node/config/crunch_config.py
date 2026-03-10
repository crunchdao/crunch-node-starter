"""CrunchConfig for trading competitions.

Models receive live OHLCV candle data and return buy/sell orders.
PnL is tracked by the TradingEngine, not a scoring function.

Feed: Binance-style 1m candles for configurable pairs (default BTCUSDT, ETHUSDT)
Output: {"action": "buy"|"sell", "amount": float}
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from crunch_node.crunch_config import (
    Aggregation,
    AggregationWindow,
    ScheduledPrediction,
)
from crunch_node.crunch_config import (
    CrunchConfig as BaseCrunchConfig,
)
from crunch_node.services.realtime_predict import RealtimeServiceConfig
from crunch_node.services.trading.config import TradingConfig


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
            windows={
                "score_recent": AggregationWindow(hours=24),
                "score_steady": AggregationWindow(hours=72),
                "score_anchor": AggregationWindow(hours=168),
            },
            value_field="net_pnl",
            ranking_key="score_recent",
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
                scope_key="trading-btcusdt-60s",
                scope={"subject": "BTCUSDT"},
                prediction_interval_seconds=15,
                resolve_horizon_seconds=60,
            ),
            ScheduledPrediction(
                scope_key="trading-ethusdt-60s",
                scope={"subject": "ETHUSDT"},
                prediction_interval_seconds=15,
                resolve_horizon_seconds=60,
            ),
        ]
    )

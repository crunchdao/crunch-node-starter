from __future__ import annotations

from packs.trading.node.config.crunch_config import (
    CostModel,
    CrunchConfig,
    TradingConfig,
)


class TestTradingConfigWiring:
    def test_trading_config_present(self):
        config = CrunchConfig()
        assert hasattr(config, "trading")
        assert isinstance(config.trading, TradingConfig)
        assert config.trading.cost_model.trading_fee_pct > 0

    def test_trading_config_aggregation_uses_net_pnl(self):
        config = CrunchConfig()
        assert config.aggregation.value_field == "net_pnl"

    def test_trading_config_customizable(self):
        custom = TradingConfig(
            cost_model=CostModel(trading_fee_pct=0.002),
            signal_mode="delta",
        )
        config = CrunchConfig(trading=custom)
        assert config.trading.cost_model.trading_fee_pct == 0.002
        assert config.trading.signal_mode == "delta"

    def test_default_signal_mode_is_order(self):
        config = CrunchConfig()
        assert config.trading.signal_mode == "order"

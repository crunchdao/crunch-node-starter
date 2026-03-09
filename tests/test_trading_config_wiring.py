from __future__ import annotations

from packs.trading.node.config.crunch_config import CrunchConfig


class TestTradingConfigWiring:
    def test_trading_config_has_cost_model(self):
        config = CrunchConfig()
        assert hasattr(config, "cost_model")
        assert config.cost_model.trading_fee_pct > 0

    def test_trading_config_aggregation_uses_net_pnl(self):
        config = CrunchConfig()
        assert config.aggregation.value_field == "net_pnl"

    def test_cost_model_customizable(self):
        from crunch_node.services.trading.costs import CostModel

        custom = CostModel(trading_fee_pct=0.002)
        config = CrunchConfig(cost_model=custom)
        assert config.cost_model.trading_fee_pct == 0.002

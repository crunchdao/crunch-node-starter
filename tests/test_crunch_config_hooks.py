from __future__ import annotations

from crunch_node.crunch_config import CrunchConfig


class TestHookFieldDefaults:
    def test_build_simulator_sink_defaults_to_none(self):
        config = CrunchConfig()
        assert config.build_simulator_sink is None

    def test_build_score_snapshots_defaults_to_none(self):
        config = CrunchConfig()
        assert config.build_score_snapshots is None

    def test_build_trading_widgets_defaults_to_none(self):
        config = CrunchConfig()
        assert config.build_trading_widgets is None

    def test_feed_subject_mapping_defaults_to_empty_dict(self):
        config = CrunchConfig()
        assert config.feed_subject_mapping == {}


class TestHookFieldsAcceptValues:
    def test_build_simulator_sink_accepts_callable(self):
        def my_sink(session, config):
            return "sink"

        config = CrunchConfig(build_simulator_sink=my_sink)
        assert config.build_simulator_sink is my_sink

    def test_build_score_snapshots_accepts_callable(self):
        def my_snapshots(session, config, repo):
            return lambda now: []

        config = CrunchConfig(build_score_snapshots=my_snapshots)
        assert config.build_score_snapshots is my_snapshots

    def test_build_trading_widgets_accepts_callable(self):
        def my_widgets():
            return [{"type": "metric"}]

        config = CrunchConfig(build_trading_widgets=my_widgets)
        assert config.build_trading_widgets is my_widgets

    def test_feed_subject_mapping_accepts_dict(self):
        mapping = {"BTCUSDT": "BTC", "ETHUSDT": "ETH"}
        config = CrunchConfig(feed_subject_mapping=mapping)
        assert config.feed_subject_mapping == mapping


class TestExistingBehaviorNotBroken:
    def test_default_config_creates_successfully(self):
        config = CrunchConfig()
        assert config is not None

    def test_scoring_function_still_defaults_to_none(self):
        config = CrunchConfig()
        assert config.scoring_function is None

    def test_performance_config_still_has_defaults(self):
        config = CrunchConfig()
        assert config.performance is not None

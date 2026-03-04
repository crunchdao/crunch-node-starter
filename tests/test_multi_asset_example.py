"""Tests that multi-asset support works via CrunchConfig.scheduled_predictions.

Issue #7: Multi-asset is natively supported — verify it works with
multiple ScheduledPrediction entries using different subjects.
"""

from __future__ import annotations

from crunch_node.crunch_config import CrunchConfig, ScheduledPrediction


class TestMultiAssetSupport:
    """CrunchConfig should support multiple subjects via scheduled_predictions."""

    def test_multi_asset_config_accepted(self):
        """CrunchConfig with multiple subjects should validate."""
        config = CrunchConfig(
            scheduled_predictions=[
                ScheduledPrediction(
                    scope_key="realtime-btc",
                    scope={"subject": "BTC"},
                    prediction_interval_seconds=15,
                    resolve_horizon_seconds=60,
                ),
                ScheduledPrediction(
                    scope_key="realtime-eth",
                    scope={"subject": "ETH"},
                    prediction_interval_seconds=15,
                    resolve_horizon_seconds=60,
                    order=1,
                ),
                ScheduledPrediction(
                    scope_key="realtime-sol",
                    scope={"subject": "SOL"},
                    prediction_interval_seconds=15,
                    resolve_horizon_seconds=60,
                    order=2,
                ),
            ]
        )
        assert len(config.scheduled_predictions) == 3

        subjects = {sp.scope["subject"] for sp in config.scheduled_predictions}
        assert subjects == {"BTC", "ETH", "SOL"}

    def test_each_prediction_has_unique_scope_key(self):
        """scope_key must be unique across predictions."""
        config = CrunchConfig(
            scheduled_predictions=[
                ScheduledPrediction(
                    scope_key="btc",
                    scope={"subject": "BTC"},
                    resolve_horizon_seconds=60,
                ),
                ScheduledPrediction(
                    scope_key="eth",
                    scope={"subject": "ETH"},
                    resolve_horizon_seconds=60,
                ),
            ]
        )
        keys = [sp.scope_key for sp in config.scheduled_predictions]
        assert len(keys) == len(set(keys)), f"Duplicate scope_keys: {keys}"

    def test_inactive_predictions_preserved(self):
        """Inactive predictions should be stored but filterable."""
        config = CrunchConfig(
            scheduled_predictions=[
                ScheduledPrediction(
                    scope_key="btc",
                    scope={"subject": "BTC"},
                    resolve_horizon_seconds=60,
                    active=True,
                ),
                ScheduledPrediction(
                    scope_key="eth",
                    scope={"subject": "ETH"},
                    resolve_horizon_seconds=60,
                    active=False,
                ),
            ]
        )
        active = [sp for sp in config.scheduled_predictions if sp.active]
        assert len(active) == 1
        assert active[0].scope_key == "btc"

"""Tests for startup validation of resolve_horizon_seconds constraint.

Issue #5: If resolve_horizon_seconds < feed poll interval (and > 0), predictions
will never accumulate enough data to resolve ground truth. The system should
fail fast at startup instead of silently scoring 0.

resolve_horizon_seconds=0 is valid — it means immediate resolution (live trading).
"""

from __future__ import annotations

import pytest


class TestResolveHorizonSecondsValidation:
    """resolve_horizon_seconds must be >= feed poll interval (or 0 for immediate)."""

    def test_rejects_resolve_horizon_seconds_below_feed_interval(self):
        """If 0 < resolve_horizon_seconds < feed_poll_seconds, startup must raise."""
        from crunch_node.services.realtime_predict import RealtimePredictService

        configs = [
            {
                "scope_key": "test",
                "schedule": {
                    "prediction_interval_seconds": 15,
                    "resolve_horizon_seconds": 5,
                },
                "active": True,
            }
        ]

        with pytest.raises(ValueError, match="resolve_horizon_seconds"):
            RealtimePredictService.validate_prediction_configs(
                configs, feed_poll_seconds=10.0
            )

    def test_accepts_zero_for_immediate_resolution(self):
        """resolve_horizon_seconds=0 is valid (live trading, immediate scoring)."""
        from crunch_node.services.realtime_predict import RealtimePredictService

        configs = [
            {
                "scope_key": "test",
                "schedule": {
                    "prediction_interval_seconds": 15,
                    "resolve_horizon_seconds": 0,
                },
                "active": True,
            }
        ]

        # Should not raise
        RealtimePredictService.validate_prediction_configs(
            configs, feed_poll_seconds=10.0
        )

    def test_accepts_resolve_horizon_seconds_equal_to_feed_interval(self):
        """Exact match is OK."""
        from crunch_node.services.realtime_predict import RealtimePredictService

        configs = [
            {
                "scope_key": "test",
                "schedule": {
                    "prediction_interval_seconds": 15,
                    "resolve_horizon_seconds": 10,
                },
                "active": True,
            }
        ]

        RealtimePredictService.validate_prediction_configs(
            configs, feed_poll_seconds=10.0
        )

    def test_accepts_resolve_horizon_seconds_above_feed_interval(self):
        from crunch_node.services.realtime_predict import RealtimePredictService

        configs = [
            {
                "scope_key": "test",
                "schedule": {
                    "prediction_interval_seconds": 15,
                    "resolve_horizon_seconds": 60,
                },
                "active": True,
            }
        ]

        RealtimePredictService.validate_prediction_configs(
            configs, feed_poll_seconds=10.0
        )

    def test_skips_inactive_configs(self):
        """Inactive configs should not be validated."""
        from crunch_node.services.realtime_predict import RealtimePredictService

        configs = [
            {
                "scope_key": "test",
                "schedule": {
                    "prediction_interval_seconds": 15,
                    "resolve_horizon_seconds": 1,
                },
                "active": False,
            }
        ]

        RealtimePredictService.validate_prediction_configs(
            configs, feed_poll_seconds=10.0
        )

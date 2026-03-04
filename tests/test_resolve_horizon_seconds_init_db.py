"""Tests that init_db validates resolve_horizon_seconds at config load time.

Issue #5 (continued): The validation must also run during DB migration
when scheduled predictions are loaded, not just at predict worker
startup.
"""

from __future__ import annotations

import pytest


class TestInitDbConfigValidation:
    """validate_scheduled_configs should catch invalid timing."""

    def test_validate_scheduled_configs_accepts_zero_resolve(self):
        """resolve_horizon_seconds=0 is valid (immediate resolution, live trading)."""
        from crunch_node.db import init_db

        configs = [
            {
                "scope_key": "test",
                "scope_template": {"subject": "BTC"},
                "schedule": {
                    "prediction_interval_seconds": 15,
                    "resolve_horizon_seconds": 0,
                },
                "active": True,
            }
        ]

        # Should not raise
        init_db.validate_scheduled_configs(configs)

    def test_validate_scheduled_configs_rejects_negative_resolve(self):
        """A config with resolve_horizon_seconds < 0 must be rejected."""
        from crunch_node.db import init_db

        bad_configs = [
            {
                "scope_key": "test",
                "scope_template": {"subject": "BTC"},
                "schedule": {
                    "prediction_interval_seconds": 15,
                    "resolve_horizon_seconds": -1,
                },
                "active": True,
            }
        ]

        with pytest.raises(ValueError, match="resolve_horizon_seconds"):
            init_db.validate_scheduled_configs(bad_configs)

    def test_validate_scheduled_configs_accepts_positive(self):
        """A config with positive resolve_horizon_seconds should pass."""
        from crunch_node.db import init_db

        good_configs = [
            {
                "scope_key": "test",
                "scope_template": {"subject": "BTC"},
                "schedule": {
                    "prediction_interval_seconds": 15,
                    "resolve_horizon_seconds": 60,
                },
                "active": True,
            }
        ]

        # Should not raise
        init_db.validate_scheduled_configs(good_configs)

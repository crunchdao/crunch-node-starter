"""Tests for config_loader — fail-hard on broken configs."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from crunch_node.config_loader import _resolve_config, reset_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache()
    yield
    reset_cache()


def _make_try_load(scenario: dict):
    """Return a fake _try_load that returns per-path results.

    scenario maps path-prefix to (config, found) tuples.
    Unmatched paths return (None, False).
    """
    def fake_try_load(path: str):
        for key, result in scenario.items():
            if key in path:
                return result
        return (None, False)
    return fake_try_load


# ── CRUNCH_CONFIG_MODULE set but broken → must crash ────────────────

class TestExplicitEnvVarBroken:
    def test_raises_when_env_var_set_but_load_fails(self):
        """If CRUNCH_CONFIG_MODULE is set but the config can't load, crash."""
        fake = _make_try_load({"my_broken_config": (None, True)})

        with patch.dict("os.environ", {"CRUNCH_CONFIG_MODULE": "my_broken_config:Cfg"}):
            with patch("crunch_node.config_loader._try_load", side_effect=fake):
                with pytest.raises(RuntimeError, match="CRUNCH_CONFIG_MODULE"):
                    _resolve_config()

    def test_raises_when_env_var_module_not_found(self):
        """If CRUNCH_CONFIG_MODULE is set but module doesn't exist, crash."""
        fake = _make_try_load({"my_missing": (None, False)})

        with patch.dict("os.environ", {"CRUNCH_CONFIG_MODULE": "my_missing:Cfg"}):
            with patch("crunch_node.config_loader._try_load", side_effect=fake):
                with pytest.raises(RuntimeError, match="CRUNCH_CONFIG_MODULE"):
                    _resolve_config()


# ── Operator config found but broken → must crash ──────────────────

class TestOperatorConfigBroken:
    def test_raises_when_operator_config_found_but_broken(self):
        """Operator config exists but fails to instantiate → crash, don't fall back."""
        fake = _make_try_load({"config.crunch_config": (None, True)})

        with patch.dict("os.environ", {}, clear=True):
            with patch("crunch_node.config_loader._try_load", side_effect=fake):
                with pytest.raises(RuntimeError, match="config.crunch_config"):
                    _resolve_config()


# ── Happy paths still work ─────────────────────────────────────────

class TestHappyPaths:
    def test_explicit_env_var_works(self):
        sentinel = object()
        fake = _make_try_load({"my_config": (sentinel, True)})

        with patch.dict("os.environ", {"CRUNCH_CONFIG_MODULE": "my_config:Cfg"}):
            with patch("crunch_node.config_loader._try_load", side_effect=fake):
                assert _resolve_config() is sentinel

    def test_operator_config_works(self):
        sentinel = object()
        fake = _make_try_load({"config.crunch_config": (sentinel, True)})

        with patch.dict("os.environ", {}, clear=True):
            with patch("crunch_node.config_loader._try_load", side_effect=fake):
                assert _resolve_config() is sentinel

    def test_falls_back_to_default_when_no_operator_config(self):
        """No operator config at all → default CrunchConfig is fine."""
        fake = _make_try_load({})  # everything returns (None, False)

        with patch.dict("os.environ", {}, clear=True):
            with patch("crunch_node.config_loader._try_load", side_effect=fake):
                config = _resolve_config()
                from crunch_node.crunch_config import CrunchConfig
                assert isinstance(config, CrunchConfig)

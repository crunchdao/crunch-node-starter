from __future__ import annotations

from unittest.mock import patch

import pytest

from crunch_node.config_loader import _resolve_config, reset_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache()
    yield
    reset_cache()


def test_resolve_config_raises_when_operator_config_broken():
    with patch.dict("os.environ", {"CRUNCH_CONFIG_MODULE": ""}):
        with patch(
            "crunch_node.config_loader._try_load",
            return_value=(None, True),
        ):
            with pytest.raises(RuntimeError, match="failed to instantiate"):
                _resolve_config()


def test_resolve_config_raises_when_env_var_set_but_broken():
    """CRUNCH_CONFIG_MODULE set but fails → crash, don't try fallbacks."""
    def fake_try_load(path: str):
        if "my_broken" in path:
            return (None, True)
        return (None, False)

    with patch.dict("os.environ", {"CRUNCH_CONFIG_MODULE": "my_broken:Cfg"}):
        with patch("crunch_node.config_loader._try_load", side_effect=fake_try_load):
            with pytest.raises(RuntimeError, match="CRUNCH_CONFIG_MODULE"):
                _resolve_config()


def test_resolve_config_raises_when_env_var_module_not_found():
    """CRUNCH_CONFIG_MODULE set but module doesn't exist → crash."""
    def fake_try_load(path: str):
        if "my_missing" in path:
            return (None, False)
        return (None, False)

    with patch.dict("os.environ", {"CRUNCH_CONFIG_MODULE": "my_missing:Cfg"}):
        with patch("crunch_node.config_loader._try_load", side_effect=fake_try_load):
            with pytest.raises(RuntimeError, match="CRUNCH_CONFIG_MODULE"):
                _resolve_config()


def test_resolve_config_falls_back_when_no_operator_config():
    with patch.dict("os.environ", {"CRUNCH_CONFIG_MODULE": ""}):
        with patch(
            "crunch_node.config_loader._try_load",
            return_value=(None, False),
        ):
            config = _resolve_config()

    from crunch_node.crunch_config import CrunchConfig

    assert isinstance(config, CrunchConfig)

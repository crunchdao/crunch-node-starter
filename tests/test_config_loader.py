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
    with patch(
        "crunch_node.config_loader._try_load",
        return_value=(None, True),
    ):
        with pytest.raises(RuntimeError, match="failed to instantiate"):
            _resolve_config()


def test_resolve_config_falls_back_when_no_operator_config():
    with patch(
        "crunch_node.config_loader._try_load",
        return_value=(None, False),
    ):
        config = _resolve_config()

    from crunch_node.crunch_config import CrunchConfig

    assert isinstance(config, CrunchConfig)

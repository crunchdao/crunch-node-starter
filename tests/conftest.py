import os
import sys
from pathlib import Path

import pytest

_pack_node = str(Path(__file__).resolve().parent.parent / "packs" / "trading" / "node")

if _pack_node not in sys.path:
    sys.path.insert(0, _pack_node)


@pytest.fixture(scope="session", autouse=True)
def _crunch_config_module():
    """
    Ensure CRUNCH_CONFIG_MODULE has a default value for the test session
    without mutating os.environ at import time.
    """
    if "CRUNCH_CONFIG_MODULE" not in os.environ:
        os.environ["CRUNCH_CONFIG_MODULE"] = "crunch_node.crunch_config:CrunchConfig"
        yield
        del os.environ["CRUNCH_CONFIG_MODULE"]
    else:
        yield

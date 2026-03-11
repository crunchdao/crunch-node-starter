import os
import sys
from pathlib import Path

_pack_node = str(
    Path(__file__).resolve().parent.parent / "packs" / "trading" / "node"
)

if _pack_node not in sys.path:
    sys.path.append(_pack_node)

os.environ.setdefault(
    "CRUNCH_CONFIG_MODULE", "crunch_node.crunch_config:CrunchConfig"
)

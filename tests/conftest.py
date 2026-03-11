import sys
from pathlib import Path

_pack_node = str(Path(__file__).resolve().parent.parent / "packs" / "trading" / "node")
if _pack_node not in sys.path:
    sys.path.insert(0, _pack_node)

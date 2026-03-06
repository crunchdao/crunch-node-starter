"""Feed normalizer registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from crunch_node.feeds.normalizers.candle import Candle, CandleInput, CandleNormalizer
from crunch_node.feeds.normalizers.tick import Tick, TickInput, TickNormalizer

if TYPE_CHECKING:
    from crunch_node.feeds.normalizers.base import FeedNormalizer

__all__ = [
    "Candle",
    "CandleInput",
    "CandleNormalizer",
    "Tick",
    "TickInput",
    "TickNormalizer",
    "get_normalizer",
    "NORMALIZERS",
]

NORMALIZERS: dict[str, type[FeedNormalizer]] = {
    "candle": CandleNormalizer,
    "tick": TickNormalizer,
}


def get_normalizer(name: str | None = None) -> FeedNormalizer:
    """Get a normalizer instance by name.

    Args:
        name: Normalizer name (e.g., "candle", "tick"). Defaults to "candle" if not specified.

    Returns:
        Normalizer instance.

    Raises:
        KeyError: If normalizer name is not registered.
    """
    if name is None:
        name = "candle"
    if name not in NORMALIZERS:
        raise KeyError(
            f"Unknown normalizer: {name!r}. Available: {sorted(NORMALIZERS.keys())}"
        )
    return NORMALIZERS[name]()

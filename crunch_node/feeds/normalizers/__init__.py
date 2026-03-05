"""Feed normalizer registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from crunch_node.feeds.normalizers.candle import Candle, CandleInput, CandleNormalizer

if TYPE_CHECKING:
    from crunch_node.feeds.normalizers.base import FeedNormalizer

__all__ = ["Candle", "CandleInput", "CandleNormalizer", "get_normalizer", "NORMALIZERS"]

NORMALIZERS: dict[str, type[FeedNormalizer]] = {
    "candle": CandleNormalizer,
}


def get_normalizer(name: str | None = None) -> FeedNormalizer:
    """Get a normalizer instance by name.

    Args:
        name: Normalizer name (e.g., "candle"). Defaults to "candle" if not specified.

    Returns:
        Normalizer instance.
    """
    cls = NORMALIZERS.get(name or "candle", CandleNormalizer)
    return cls()

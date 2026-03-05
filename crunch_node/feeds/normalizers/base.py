"""Base protocol for feed normalizers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from crunch_node.feeds import FeedDataRecord


class FeedNormalizer(Protocol):
    """Protocol for transforming feed records into model input format.

    Each normalizer defines its own output structure.
    The contract config declares which normalizer to use.
    """

    def normalize(
        self,
        records: Sequence[FeedDataRecord],
        subject: str,
    ) -> dict[str, Any]:
        """Transform feed records into model input format.

        Args:
            records: Sequence of feed records to normalize.
            subject: The subject/symbol for these records.

        Returns:
            Dict with at minimum 'symbol' and 'asof_ts' keys,
            plus format-specific data (e.g., 'candles_1m').
        """
        ...

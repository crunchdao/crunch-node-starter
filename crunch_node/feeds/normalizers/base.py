"""Base protocol for feed normalizers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel

if TYPE_CHECKING:
    from crunch_node.feeds import FeedDataRecord


class FeedNormalizer(Protocol):
    """Protocol for transforming feed records into model input format.

    Each normalizer defines its own output structure via output_type.
    The contract config declares which normalizer to use.
    """

    output_type: type[BaseModel]

    def normalize(
        self,
        records: Sequence[FeedDataRecord],
        subject: str,
    ) -> BaseModel:
        """Transform feed records into model input format.

        Args:
            records: Sequence of feed records to normalize.
            subject: The subject/symbol for these records.

        Returns:
            Pydantic model instance with the normalized data.
        """
        ...

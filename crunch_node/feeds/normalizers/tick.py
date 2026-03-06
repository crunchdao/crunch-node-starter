"""Tick normalizer for price tick output format."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class Tick(BaseModel):
    """Single price tick."""

    ts: int
    price: float


class TickInput(BaseModel):
    """Model input containing tick data."""

    symbol: str
    asof_ts: int
    ticks: list[Tick]


class TickNormalizer:
    """Normalizes feed records to tick format.

    Only handles kind="tick". Skips records of other kinds.
    """

    output_type: type[BaseModel] = TickInput

    def normalize(
        self,
        records: Sequence[Any],
        subject: str,
    ) -> TickInput:
        ticks = []
        for record in records:
            tick = self._record_to_tick(record)
            if tick is not None:
                ticks.append(tick)

        asof_ts = ticks[-1].ts if ticks else 0

        return TickInput(
            symbol=subject,
            asof_ts=asof_ts,
            ticks=ticks,
        )

    def _record_to_tick(self, record: Any) -> Tick | None:
        if record.kind != "tick":
            return None

        values = getattr(record, "values", None) or {}
        price = self._extract_price(values)
        if price is None:
            return None

        ts_event = self._to_timestamp(record.ts_event)

        return Tick(ts=ts_event, price=price)

    @staticmethod
    def _extract_price(values: dict[str, Any]) -> float | None:
        for key in ("price", "close"):
            if key in values:
                try:
                    return float(values[key])
                except (TypeError, ValueError):
                    return None
        return None

    @staticmethod
    def _to_timestamp(ts_event: datetime | int | float) -> int:
        if isinstance(ts_event, datetime):
            return int(ts_event.timestamp())
        return int(ts_event)

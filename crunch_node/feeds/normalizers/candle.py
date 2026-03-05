"""Candle normalizer for OHLCV output format."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any


class CandleNormalizer:
    """Normalizes feed records to OHLCV candle format.

    Handles both:
    - kind="candle": Uses OHLCV values from record
    - kind="tick" (or other): Converts single price to candle (open=high=low=close)

    Accepts both FeedDataRecord (ts_event: int) and FeedRecord (ts_event: datetime).
    """

    def normalize(
        self,
        records: Sequence[Any],
        subject: str,
    ) -> dict[str, Any]:
        candles = []
        for record in records:
            candle = self._record_to_candle(record)
            if candle is not None:
                candles.append(candle)

        asof_ts = self._to_timestamp(records[-1].ts_event) if records else 0

        return {
            "symbol": subject,
            "asof_ts": asof_ts,
            "candles_1m": candles,
        }

    def _record_to_candle(self, record: Any) -> dict[str, Any] | None:
        values = getattr(record, "values", None) or {}
        price = self._extract_price(values)
        if price is None:
            return None

        ts_event = self._to_timestamp(record.ts_event)

        if record.kind == "candle":
            return {
                "ts": ts_event,
                "open": float(values.get("open", price)),
                "high": float(values.get("high", price)),
                "low": float(values.get("low", price)),
                "close": float(values.get("close", price)),
                "volume": float(values.get("volume", 0.0)),
            }
        else:
            return {
                "ts": ts_event,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0.0,
            }

    @staticmethod
    def _extract_price(values: dict[str, Any]) -> float | None:
        for key in ("close", "price"):
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

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from math import floor
from typing import Any

import requests

from coordinator_node.feeds.base import DataFeed, FeedHandle, FeedSink
from coordinator_node.feeds.contracts import (
    FeedDataRecord,
    FeedFetchRequest,
    FeedSubscription,
    SubjectDescriptor,
)
from coordinator_node.feeds.registry import FeedSettings

_logger = logging.getLogger(__name__)

_PYTH_HERMES = "https://hermes.pyth.network"

_DEFAULT_FEED_IDS = {
    "BTC": "0xe62df6c8b4a85fe1cc8b337a5f8854d9c1f5f59e4cb4ce8b063a492f6ed5b5b6",
    "ETH": "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
}

# Maximum consecutive errors before raising in polling loops
_MAX_CONSECUTIVE_ERRORS = 10
_INITIAL_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 60.0


@dataclass
class PythHermesClient:
    base_url: str = _PYTH_HERMES
    timeout_seconds: float = 8.0

    def latest_prices(self, feed_ids: list[str]) -> list[dict[str, Any]]:
        response = requests.get(
            f"{self.base_url.rstrip('/')}/v2/updates/price/latest",
            params={"ids[]": feed_ids, "parsed": "true"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        parsed = payload.get("parsed") if isinstance(payload, dict) else None
        if not isinstance(parsed, list):
            raise TypeError(
                f"Unexpected Pyth latest_prices response: expected dict with 'parsed' list, "
                f"got {type(payload).__name__}."
            )
        return parsed

    def price_feeds(self) -> list[dict[str, Any]]:
        response = requests.get(
            f"{self.base_url.rstrip('/')}/v2/price_feeds",
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise TypeError(
                f"Unexpected Pyth price_feeds response: expected list, "
                f"got {type(payload).__name__}."
            )
        return payload


class _PollingFeedHandle:
    def __init__(self, task: asyncio.Task[None]):
        self._task = task

    async def stop(self) -> None:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass


class PythFeed(DataFeed):
    def __init__(self, settings: FeedSettings, client: PythHermesClient | None = None):
        self.settings = settings
        self.client = client or PythHermesClient(
            base_url=settings.options.get("hermes_url", _PYTH_HERMES),
            timeout_seconds=float(settings.options.get("timeout_seconds", "8")),
        )
        self.poll_seconds = float(settings.options.get("poll_seconds", "5"))

    async def list_subjects(self) -> Sequence[SubjectDescriptor]:
        feed_map = _load_feed_map(self.settings)

        rows = await asyncio.to_thread(self.client.price_feeds)

        descriptors: list[SubjectDescriptor] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = _normalize_symbol(row)
            if not symbol:
                continue
            feed_id = row.get("id")
            descriptors.append(
                SubjectDescriptor(
                    symbol=symbol,
                    display_name=symbol,
                    kinds=("tick", "candle"),
                    granularities=("1s", "1m", "5m"),
                    source="pyth",
                    metadata={"feed_id": feed_id},
                )
            )

        if not descriptors:
            raise RuntimeError(
                "Pyth price_feeds returned no usable subjects. "
                "The Hermes API may be down or returning unexpected data. "
                f"Configured feed_map has {len(feed_map)} entries."
            )

        return descriptors

    async def listen(self, sub: FeedSubscription, sink: FeedSink) -> FeedHandle:
        async def _loop() -> None:
            watermark: dict[str, int] = {}
            consecutive_errors = 0
            backoff = _INITIAL_BACKOFF_SECONDS

            while True:
                try:
                    now_ts = int(datetime.now(UTC).timestamp())
                    req = FeedFetchRequest(
                        subjects=sub.subjects,
                        kind=sub.kind,
                        granularity=sub.granularity,
                        end_ts=now_ts,
                        limit=1,
                    )
                    records = await self.fetch(req)
                    for record in records:
                        last_ts = watermark.get(record.subject)
                        if last_ts is not None and record.ts_event <= last_ts:
                            continue
                        watermark[record.subject] = record.ts_event
                        await sink.on_record(record)

                    # Reset error state on success
                    consecutive_errors = 0
                    backoff = _INITIAL_BACKOFF_SECONDS

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    consecutive_errors += 1
                    if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                        raise RuntimeError(
                            f"Pyth feed polling failed {consecutive_errors} "
                            f"consecutive times, last error: {exc}"
                        ) from exc
                    _logger.warning(
                        "Pyth feed polling error (%d/%d): %s — retrying in %.1fs",
                        consecutive_errors,
                        _MAX_CONSECUTIVE_ERRORS,
                        exc,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
                    continue

                await asyncio.sleep(max(0.5, self.poll_seconds))

        task = asyncio.create_task(_loop())
        return _PollingFeedHandle(task)

    async def fetch(self, req: FeedFetchRequest) -> Sequence[FeedDataRecord]:
        feed_map = _load_feed_map(self.settings)
        requested_assets = [asset for asset in req.subjects if asset in feed_map]
        if not requested_assets:
            missing = [a for a in req.subjects if a not in feed_map]
            raise ValueError(
                f"No Pyth feed IDs configured for requested subjects: {missing}. "
                f"Available subjects: {sorted(feed_map.keys())}. "
                f"Add mappings via FEED_OPT_feed_id_<SYMBOL>=<hex_id>."
            )

        feed_ids = [feed_map[asset] for asset in requested_assets]

        rows = await asyncio.to_thread(self.client.latest_prices, feed_ids)

        by_feed_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            by_feed_id[str(row.get("id") or "").lower()] = row

        records: list[FeedDataRecord] = []
        for asset in requested_assets:
            feed_id = feed_map[asset]
            parsed = by_feed_id.get(feed_id.lower())

            if parsed is None:
                _logger.warning(
                    "Pyth returned no price data for %s (feed_id=%s). "
                    "The feed ID may be invalid or the asset may be temporarily "
                    "unavailable on Pyth. Skipping this subject.",
                    asset,
                    feed_id,
                )
                continue

            price = parsed.get("price") if isinstance(parsed, dict) else None
            if not isinstance(price, dict):
                _logger.warning(
                    "Pyth price data for %s has unexpected format: "
                    "missing or non-dict 'price' field (got %s). "
                    "Skipping this subject.",
                    asset,
                    type(price).__name__,
                )
                continue

            missing_keys = [k for k in ("price", "expo") if k not in price]
            if missing_keys:
                _logger.warning(
                    "Pyth price data for %s missing required field(s): %s. "
                    "Skipping this subject.",
                    asset,
                    ", ".join(missing_keys),
                )
                continue

            raw_price = int(price["price"])
            expo = int(price["expo"])
            publish_time = int(
                price.get(
                    "publish_time",
                    req.end_ts or datetime.now(UTC).timestamp(),
                )
            )
            value = float(raw_price) * (10**expo)
            ts_event = int(publish_time)

            if req.start_ts is not None and ts_event < req.start_ts:
                continue
            if req.end_ts is not None and ts_event > req.end_ts:
                continue

            if req.kind == "candle":
                bucket = _bucket_ts(ts_event, req.granularity)
                records.append(
                    FeedDataRecord(
                        subject=asset,
                        kind="candle",
                        granularity=req.granularity,
                        ts_event=bucket,
                        values={
                            "open": value,
                            "high": value,
                            "low": value,
                            "close": value,
                            "volume": 0.0,
                        },
                        source="pyth",
                    )
                )
            else:
                records.append(
                    FeedDataRecord(
                        subject=asset,
                        kind="tick",
                        granularity=req.granularity,
                        ts_event=ts_event,
                        values={"price": value},
                        source="pyth",
                    )
                )

        return records


def build_pyth_feed(settings: FeedSettings) -> PythFeed:
    return PythFeed(settings)


def _load_feed_map(settings: FeedSettings) -> dict[str, str]:
    mapping = dict(_DEFAULT_FEED_IDS)

    for key, value in settings.options.items():
        if not key.startswith("feed_id_"):
            continue
        symbol = key[len("feed_id_") :].strip().upper()
        if symbol:
            mapping[symbol] = value

    return mapping


def _normalize_symbol(row: dict[str, Any]) -> str | None:
    attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    symbol = attrs.get("symbol") if isinstance(attrs, dict) else None
    if isinstance(symbol, str) and symbol:
        return symbol.split("/")[0].upper()
    return None


def _bucket_ts(ts_event: int, granularity: str) -> int:
    seconds = {
        "1s": 1,
        "1m": 60,
        "5m": 300,
    }.get(str(granularity).strip(), 60)
    return int(floor(ts_event / seconds) * seconds)

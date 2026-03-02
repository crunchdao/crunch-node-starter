from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import requests

from coordinator_node.feeds.base import DataFeed, FeedHandle, FeedSink
from coordinator_node.feeds.contracts import (
    FeedDataRecord,
    FeedFetchRequest,
    FeedSubscription,
    SubjectDescriptor,
)
from coordinator_node.feeds.registry import FeedSettings

try:  # pragma: no cover - covered through injection tests
    from binance.client import Client as BinanceSDKClient
except (
    Exception
):  # pragma: no cover - keep runtime resilient when dependency is missing
    BinanceSDKClient = None

_logger = logging.getLogger(__name__)

_BINANCE_API = "https://api.binance.com"

# Maximum consecutive errors before raising in polling loops
_MAX_CONSECUTIVE_ERRORS = 10
_INITIAL_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 60.0


@runtime_checkable
class BinanceSDK(Protocol):
    """Typed interface for the subset of python-binance's Client we use.

    python-binance does not ship type stubs (returns bare ``Dict`` / ``**params``).
    This protocol pins down the exact methods and signatures we rely on so that
    ``sdk_client`` is not ``Any``.
    """

    def get_exchange_info(self) -> dict[str, Any]: ...

    def get_klines(self, **params: Any) -> list[list[Any]]: ...

    def get_symbol_ticker(
        self, **params: Any
    ) -> dict[str, Any] | list[dict[str, Any]]: ...


@dataclass
class BinanceRestClient:
    base_url: str = _BINANCE_API
    timeout_seconds: float = 8.0
    sdk_client: BinanceSDK | None = field(default=None)

    def __post_init__(self) -> None:
        if self.sdk_client is None:
            self.sdk_client = _build_default_sdk_client(
                timeout_seconds=self.timeout_seconds
            )

    def exchange_info(self) -> dict[str, Any]:
        if self.sdk_client is not None:
            payload = self.sdk_client.get_exchange_info()
            if not isinstance(payload, dict):
                raise TypeError(
                    f"Unexpected exchange_info payload type from Binance SDK: "
                    f"{type(payload).__name__}. Expected dict."
                )
            return payload

        response = requests.get(
            f"{self.base_url}/api/v3/exchangeInfo",
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def klines(
        self,
        symbol: str,
        interval: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int | None = None,
    ) -> list[list[Any]]:
        params: dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
        }
        if start_ms is not None:
            params["startTime"] = int(start_ms)
        if end_ms is not None:
            params["endTime"] = int(end_ms)
        if limit is not None:
            params["limit"] = int(limit)

        if self.sdk_client is not None:
            payload = self.sdk_client.get_klines(**params)
            if not isinstance(payload, list):
                raise TypeError(
                    f"Unexpected klines payload type from Binance SDK: "
                    f"{type(payload).__name__}. Expected list."
                )
            return payload

        response = requests.get(
            f"{self.base_url}/api/v3/klines",
            params=params,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise TypeError(
                f"Unexpected klines response type from Binance API: "
                f"{type(payload).__name__}. Expected list."
            )
        return payload

    def depth(self, symbol: str, limit: int = 10) -> dict[str, Any]:
        """Fetch order book depth snapshot (spot).

        Returns: {"bids": [[price, qty], ...], "asks": [[price, qty], ...]}
        """
        response = requests.get(
            f"{self.base_url}/api/v3/depth",
            params={"symbol": symbol, "limit": limit},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def funding_rate(self, symbol: str, limit: int = 1) -> list[dict[str, Any]]:
        """Fetch recent funding rate from Binance Futures (fapi).

        Returns list of: {"symbol", "fundingRate", "fundingTime", "markPrice"}
        """
        fapi_url = "https://fapi.binance.com"
        response = requests.get(
            f"{fapi_url}/fapi/v1/fundingRate",
            params={"symbol": symbol, "limit": limit},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def mark_price(self, symbol: str) -> dict[str, Any]:
        """Fetch current mark price + funding info from Binance Futures.

        Returns: {"symbol", "markPrice", "indexPrice", "lastFundingRate", "nextFundingTime", ...}
        """
        fapi_url = "https://fapi.binance.com"
        response = requests.get(
            f"{fapi_url}/fapi/v1/premiumIndex",
            params={"symbol": symbol},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def ticker_price(self, symbol: str) -> float:
        if self.sdk_client is not None:
            payload = self.sdk_client.get_symbol_ticker(symbol=symbol)
            if isinstance(payload, dict):
                return float(payload["price"])
            if isinstance(payload, list) and payload and isinstance(payload[0], dict):
                return float(payload[0]["price"])
            raise ValueError(
                f"Unexpected ticker payload from Binance SDK client: "
                f"{type(payload).__name__}"
            )

        response = requests.get(
            f"{self.base_url}/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return float(payload["price"])


class _PollingFeedHandle:
    def __init__(self, task: asyncio.Task[None]):
        self._task = task

    async def stop(self) -> None:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass


class BinanceFeed(DataFeed):
    def __init__(self, settings: FeedSettings, client: BinanceRestClient | None = None):
        self.settings = settings
        self.client = client or BinanceRestClient()
        self.poll_seconds = float(settings.options.get("poll_seconds", "5"))

    async def list_subjects(self) -> Sequence[SubjectDescriptor]:
        payload = await asyncio.to_thread(self.client.exchange_info)
        symbols = payload.get("symbols") if isinstance(payload, dict) else None
        if not isinstance(symbols, list):
            raise RuntimeError(
                "Binance exchange_info response missing 'symbols' list. "
                "API may be down or returning unexpected format."
            )

        if not symbols:
            raise RuntimeError(
                "Binance exchange_info returned empty symbols list. "
                "API may be experiencing issues."
            )

        descriptors: list[SubjectDescriptor] = []
        for row in symbols[:500]:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip()
            if not symbol:
                continue
            descriptors.append(
                SubjectDescriptor(
                    symbol=symbol,
                    display_name=symbol,
                    kinds=("tick", "kline", "candle"),
                    granularities=("1m", "5m", "15m", "1h"),
                    source="binance",
                    metadata={
                        "status": row.get("status"),
                        "quote": row.get("quoteAsset"),
                        "base": row.get("baseAsset"),
                    },
                )
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
                            f"Binance feed polling failed {consecutive_errors} "
                            f"consecutive times, last error: {exc}"
                        ) from exc
                    _logger.warning(
                        "Binance feed polling error (%d/%d): %s — retrying in %.1fs",
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
        if req.kind in ("candle", "kline"):
            records = await self._fetch_candles(req)
        elif req.kind == "depth":
            records = await self._fetch_depth(req)
        elif req.kind == "funding":
            records = await self._fetch_funding(req)
        else:
            records = await self._fetch_ticks(req)

        for subject in req.subjects:
            subject_count = sum(1 for r in records if r.subject == subject)
            if subject_count == 0:
                _logger.warning(
                    "Binance returned 0 records for subject=%r kind=%r granularity=%r. "
                    "Binance requires full pair symbols (e.g. BTCUSDT, not BTC).",
                    subject,
                    req.kind,
                    req.granularity,
                )

        return records

    async def _fetch_candles(self, req: FeedFetchRequest) -> list[FeedDataRecord]:
        records: list[FeedDataRecord] = []
        interval = _to_binance_interval(req.granularity)
        start_ms = int(req.start_ts * 1000) if req.start_ts is not None else None
        end_ms = int(req.end_ts * 1000) if req.end_ts is not None else None
        limit = req.limit or 500

        for asset in req.subjects:
            try:
                rows = await asyncio.to_thread(
                    self.client.klines,
                    asset,
                    interval,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    limit=limit,
                )
            except Exception as exc:
                _logger.warning(
                    "Binance klines fetch failed for %s: %s. "
                    "Symbol may be invalid or delisted. Skipping.",
                    asset,
                    exc,
                )
                continue

            for row in rows:
                if not isinstance(row, list) or len(row) < 6:
                    _logger.warning(
                        "Binance kline row for %s has unexpected format: "
                        "expected list with ≥6 elements, got %s "
                        "with %s elements. Skipping row.",
                        asset,
                        type(row).__name__,
                        len(row) if isinstance(row, list) else "N/A",
                    )
                    continue
                ts_event = int(row[0]) // 1000
                record = FeedDataRecord(
                    subject=asset,
                    kind=req.kind,
                    granularity=req.granularity,
                    ts_event=ts_event,
                    values={
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5]),
                    },
                    source="binance",
                )
                records.append(record)

        return records

    async def _fetch_depth(self, req: FeedFetchRequest) -> list[FeedDataRecord]:
        """Fetch order book depth snapshots for requested subjects."""
        records: list[FeedDataRecord] = []
        now_ts = int(datetime.now(UTC).timestamp())
        depth_limit = int(self.settings.options.get("depth_limit", "10"))

        for asset in req.subjects:
            try:
                data = await asyncio.to_thread(
                    self.client.depth, asset, limit=depth_limit
                )
            except Exception as exc:
                _logger.warning(
                    "Binance depth fetch failed for %s: %s. "
                    "Symbol may be invalid or not available. Skipping.",
                    asset,
                    exc,
                )
                continue

            bids = data.get("bids", [])
            asks = data.get("asks", [])

            # Compute derived microstructure features
            bid_prices = [float(b[0]) for b in bids[:depth_limit]]
            bid_qtys = [float(b[1]) for b in bids[:depth_limit]]
            ask_prices = [float(a[0]) for a in asks[:depth_limit]]
            ask_qtys = [float(a[1]) for a in asks[:depth_limit]]

            best_bid = bid_prices[0] if bid_prices else 0.0
            best_ask = ask_prices[0] if ask_prices else 0.0
            spread = best_ask - best_bid
            mid_price = (
                (best_bid + best_ask) / 2.0 if (best_bid + best_ask) > 0 else 0.0
            )

            total_bid_qty = sum(bid_qtys)
            total_ask_qty = sum(ask_qtys)
            imbalance = (
                (total_bid_qty - total_ask_qty) / (total_bid_qty + total_ask_qty)
                if (total_bid_qty + total_ask_qty) > 0
                else 0.0
            )

            records.append(
                FeedDataRecord(
                    subject=asset,
                    kind="depth",
                    granularity=req.granularity,
                    ts_event=now_ts,
                    values={
                        "best_bid": best_bid,
                        "best_ask": best_ask,
                        "spread": spread,
                        "mid_price": mid_price,
                        "bid_depth": total_bid_qty,
                        "ask_depth": total_ask_qty,
                        "imbalance": imbalance,
                        "bids_top": [[p, q] for p, q in zip(bid_prices, bid_qtys)],
                        "asks_top": [[p, q] for p, q in zip(ask_prices, ask_qtys)],
                    },
                    source="binance",
                )
            )

        return records

    async def _fetch_funding(self, req: FeedFetchRequest) -> list[FeedDataRecord]:
        """Fetch funding rate and mark price from Binance Futures."""
        records: list[FeedDataRecord] = []
        now_ts = int(datetime.now(UTC).timestamp())

        for asset in req.subjects:
            try:
                mark_data = await asyncio.to_thread(self.client.mark_price, asset)
            except Exception as exc:
                _logger.warning(
                    "Binance mark_price fetch failed for %s: %s. "
                    "Symbol may not be listed on Binance Futures. Skipping.",
                    asset,
                    exc,
                )
                continue

            if not isinstance(mark_data, dict):
                _logger.warning(
                    "Unexpected mark_price response for %s: %s. Expected dict. Skipping.",
                    asset,
                    type(mark_data).__name__,
                )
                continue

            funding_rate = float(mark_data.get("lastFundingRate", 0.0))
            mark_price = float(mark_data.get("markPrice", 0.0))
            index_price = float(mark_data.get("indexPrice", 0.0))
            next_funding_ts = int(mark_data.get("nextFundingTime", 0)) // 1000

            # Basis = (mark - index) / index — a mean-reversion signal
            basis = (mark_price - index_price) / index_price if index_price > 0 else 0.0

            records.append(
                FeedDataRecord(
                    subject=asset,
                    kind="funding",
                    granularity=req.granularity,
                    ts_event=now_ts,
                    values={
                        "funding_rate": funding_rate,
                        "mark_price": mark_price,
                        "index_price": index_price,
                        "basis": basis,
                        "next_funding_ts": next_funding_ts,
                    },
                    source="binance",
                )
            )

        return records

    async def _fetch_ticks(self, req: FeedFetchRequest) -> list[FeedDataRecord]:
        records: list[FeedDataRecord] = []
        now_ts = int(datetime.now(UTC).timestamp())

        for asset in req.subjects:
            try:
                price = await asyncio.to_thread(self.client.ticker_price, asset)
            except Exception as exc:
                _logger.warning(
                    "Binance ticker_price fetch failed for %s: %s. "
                    "Symbol may be invalid or delisted. Skipping.",
                    asset,
                    exc,
                )
                continue

            records.append(
                FeedDataRecord(
                    subject=asset,
                    kind=req.kind,
                    granularity=req.granularity,
                    ts_event=now_ts,
                    values={"price": float(price)},
                    source="binance",
                )
            )

        return records


def build_binance_feed(settings: FeedSettings) -> BinanceFeed:
    return BinanceFeed(settings)


def _build_default_sdk_client(*, timeout_seconds: float) -> BinanceSDK | None:
    if BinanceSDKClient is None:
        _logger.info(
            "python-binance SDK not installed — using direct REST API calls. "
            "Install with: pip install python-binance"
        )
        return None

    try:
        return BinanceSDKClient(
            api_key=None,
            api_secret=None,
            requests_params={"timeout": timeout_seconds},
            ping=False,
        )
    except Exception as exc:
        _logger.warning(
            "Failed to initialize Binance SDK client: %s — "
            "falling back to direct REST API calls.",
            exc,
        )
        return None


def _to_binance_interval(granularity: str) -> str:
    mapping = {
        "1s": "1m",
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "1h": "1h",
    }
    return mapping.get(str(granularity).strip(), "1m")

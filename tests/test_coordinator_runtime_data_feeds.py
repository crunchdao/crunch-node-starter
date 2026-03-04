from __future__ import annotations

import unittest

from crunch_node.feeds.contracts import (
    FeedDataRecord,
    FeedFetchRequest,
    FeedSubscription,
    SubjectDescriptor,
)
from crunch_node.feeds.registry import DataFeedRegistry, create_default_registry


class DummyFeed:
    def __init__(self, settings):
        self.settings = settings

    async def list_assets(self):
        return []

    async def listen(self, sub, sink):
        raise NotImplementedError

    async def fetch(self, req):
        return []


class TestCoordinatorRuntimeDataFeeds(unittest.TestCase):
    def test_contracts_capture_core_feed_shapes(self):
        descriptor = SubjectDescriptor(
            symbol="BTCUSD",
            display_name="Bitcoin / USD",
            kinds=("tick", "candle"),
            granularities=("1s", "1m"),
            source="pyth",
        )

        sub = FeedSubscription(subjects=("BTCUSD",), kind="tick", granularity="1s")
        req = FeedFetchRequest(
            subjects=("BTCUSD",),
            kind="candle",
            granularity="1m",
            start_ts=1700000000,
            end_ts=1700000600,
            limit=100,
        )
        rec = FeedDataRecord(
            source="pyth",
            subject="BTCUSD",
            kind="candle",
            granularity="1m",
            ts_event=1700000000,
            values={"close": 50000.0},
        )

        self.assertEqual(descriptor.symbol, "BTCUSD")
        self.assertEqual(sub.kind, "tick")
        self.assertEqual(req.limit, 100)
        self.assertEqual(rec.values["close"], 50000.0)

    def test_registry_registers_and_creates_provider_instances(self):
        registry = DataFeedRegistry()
        registry.register("pyth", lambda settings: DummyFeed(settings))

        feed = registry.create("pyth", options={"assets": "BTCUSD"})

        self.assertIsInstance(feed, DummyFeed)
        self.assertEqual(feed.settings.provider, "pyth")
        self.assertEqual(feed.settings.options["assets"], "BTCUSD")

    def test_registry_reads_provider_from_env(self):
        registry = DataFeedRegistry()
        registry.register("pyth", lambda settings: DummyFeed(settings))

        feed = registry.create_from_env(
            {"FEED_PROVIDER": "pyth", "FEED_OPT_ASSETS": "BTCUSD"}
        )

        self.assertEqual(feed.settings.provider, "pyth")
        self.assertEqual(feed.settings.options["assets"], "BTCUSD")

    def test_default_registry_registers_builtin_feed_providers(self):
        registry = create_default_registry()
        providers = registry.providers()

        self.assertIn("pyth", providers)
        self.assertIn("binance", providers)
        self.assertIn("mongodb", providers)


if __name__ == "__main__":
    unittest.main()

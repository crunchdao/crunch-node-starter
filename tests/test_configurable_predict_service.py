"""Tests for configurable predict_service_class on CrunchConfig."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from crunch_node.crunch_config import CrunchConfig
from crunch_node.services.predict import PredictService
from crunch_node.services.realtime_predict import RealtimePredictService
from crunch_node.workers.predict_worker import _resolve_service_class

# ── test fixtures ──


class CustomPredictService(PredictService):
    """A custom predict service for testing."""

    def __init__(self, custom_option: str = "hello", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.custom_option = custom_option

    async def run(self) -> None:
        pass


class NotAPredictService:
    """Not a PredictService subclass — should be rejected."""

    pass


class FakeFeedReader:
    def __init__(self):
        self.source = "pyth"
        self.subject = "BTC"
        self.kind = "tick"
        self.granularity = "1s"

    def get_input(self, now):
        return {}


class FakeRunner:
    async def init(self):
        pass

    async def sync(self):
        pass

    async def call(self, method, args):
        return {}


# ── CrunchConfig field tests ──


class TestCrunchConfigPredictServiceClass(unittest.TestCase):
    def test_default_is_none(self):
        """Default CrunchConfig has predict_service_class=None."""
        config = CrunchConfig()
        self.assertIsNone(config.predict_service_class)

    def test_accepts_predict_service_subclass(self):
        """CrunchConfig accepts a PredictService subclass."""
        config = CrunchConfig(predict_service_class=CustomPredictService)
        self.assertIs(config.predict_service_class, CustomPredictService)

    def test_accepts_base_predict_service(self):
        """CrunchConfig accepts the base PredictService class."""
        config = CrunchConfig(predict_service_class=PredictService)
        self.assertIs(config.predict_service_class, PredictService)

    def test_accepts_realtime_predict_service(self):
        """CrunchConfig accepts RealtimePredictService explicitly."""
        config = CrunchConfig(predict_service_class=RealtimePredictService)
        self.assertIs(config.predict_service_class, RealtimePredictService)


# ── _resolve_service_class tests ──


class TestResolveServiceClass(unittest.TestCase):
    def test_default_returns_realtime(self):
        """When predict_service_class is None, defaults to RealtimePredictService."""
        config = CrunchConfig()
        cls = _resolve_service_class(config)
        self.assertIs(cls, RealtimePredictService)

    def test_custom_class_returned(self):
        """When predict_service_class is set, returns that class."""
        config = CrunchConfig(predict_service_class=CustomPredictService)
        cls = _resolve_service_class(config)
        self.assertIs(cls, CustomPredictService)

    def test_base_class_returned(self):
        """Base PredictService is a valid choice."""
        config = CrunchConfig(predict_service_class=PredictService)
        cls = _resolve_service_class(config)
        self.assertIs(cls, PredictService)

    def test_rejects_non_subclass(self):
        """Non-PredictService subclass raises TypeError."""
        config = CrunchConfig(predict_service_class=NotAPredictService)
        with self.assertRaises(TypeError) as ctx:
            _resolve_service_class(config)
        self.assertIn("PredictService subclass", str(ctx.exception))

    def test_rejects_string_at_config_level(self):
        """String value is rejected by Pydantic (must be a type, not a dotted path)."""
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            CrunchConfig(predict_service_class="some.module:SomeClass")


# ── integration: service instantiation ──


class TestServiceInstantiation(unittest.TestCase):
    def test_base_service_can_instantiate(self):
        """Base service instantiates without feed-related parameters."""
        service = PredictService(config=CrunchConfig(), runner=FakeRunner())
        self.assertFalse(hasattr(service, "feed_reader"))

    def test_custom_service_instantiated_with_kwargs(self):
        """Custom service class receives standard PredictService kwargs."""
        config = CrunchConfig(predict_service_class=CustomPredictService)
        cls = _resolve_service_class(config)

        service = cls(
            config=config,
            runner=FakeRunner(),
        )

        self.assertIsInstance(service, CustomPredictService)
        self.assertIsInstance(service, PredictService)
        self.assertEqual(service.custom_option, "hello")

    def test_realtime_service_default_instantiation(self):
        """Default path instantiates RealtimePredictService with feed_reader."""
        config = CrunchConfig()
        cls = _resolve_service_class(config)

        service = cls(
            feed_reader=FakeFeedReader(),
            checkpoint_interval_seconds=60,
            config=config,
            runner=FakeRunner(),
        )

        self.assertIsInstance(service, RealtimePredictService)
        self.assertIsNotNone(service.feed_reader)


# ── scaffold usage pattern ──


class TestScaffoldUsagePattern(unittest.TestCase):
    """Tests showing how a scaffold's crunch_config.py would use this."""

    def test_scaffold_overrides_predict_service(self):
        """Scaffold CrunchConfig can set predict_service_class."""

        class ScaffoldConfig(CrunchConfig):
            predict_service_class: type | None = CustomPredictService

        config = ScaffoldConfig()
        cls = _resolve_service_class(config)
        self.assertIs(cls, CustomPredictService)

    def test_scaffold_default_inherits_none(self):
        """Scaffold CrunchConfig without override keeps None → Realtime."""

        class ScaffoldConfig(CrunchConfig):
            pass

        config = ScaffoldConfig()
        cls = _resolve_service_class(config)
        self.assertIs(cls, RealtimePredictService)


if __name__ == "__main__":
    unittest.main()

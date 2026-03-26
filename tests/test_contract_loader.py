"""Tests for config_loader — resolve operator's CrunchConfig."""

from __future__ import annotations

import os
import sys
import types
import unittest

from crunch_node.config_loader import _try_load, load_config, reset_cache


class TestTryLoad(unittest.TestCase):
    def test_loads_class_and_instantiates(self):
        mod = types.ModuleType("_test_config_mod")
        from crunch_node.crunch_config import CrunchConfig

        class CustomConfig(CrunchConfig):
            pass

        mod.CrunchConfig = CustomConfig
        sys.modules["_test_config_mod"] = mod

        try:
            config, found = _try_load("_test_config_mod:CrunchConfig")
            self.assertIsInstance(config, CustomConfig)
            self.assertTrue(found)
        finally:
            del sys.modules["_test_config_mod"]

    def test_loads_instance_directly(self):
        mod = types.ModuleType("_test_config_inst")
        from crunch_node.crunch_config import CrunchConfig

        instance = CrunchConfig(metrics=["ic"])
        mod.CONTRACT = instance
        sys.modules["_test_config_inst"] = mod

        try:
            config, found = _try_load("_test_config_inst:CONTRACT")
            self.assertIs(config, instance)
            self.assertTrue(found)
            self.assertEqual(config.metrics, ["ic"])
        finally:
            del sys.modules["_test_config_inst"]

    def test_missing_module_returns_none_not_found(self):
        config, found = _try_load("nonexistent_module_xyz:CrunchConfig")
        self.assertIsNone(config)
        self.assertFalse(found)

    def test_missing_attribute_returns_none_not_found(self):
        config, found = _try_load("crunch_node.crunch_config:NonExistentClass")
        self.assertIsNone(config)
        self.assertFalse(found)


class TestLoadConfig(unittest.TestCase):
    def setUp(self):
        reset_cache()

    def tearDown(self):
        reset_cache()
        os.environ.pop("CRUNCH_CONFIG_MODULE", None)

    def test_falls_back_to_engine_default(self):
        config = load_config()
        from crunch_node.crunch_config import CrunchConfig

        self.assertIsInstance(config, CrunchConfig)

    def test_explicit_env_var(self):
        mod = types.ModuleType("_test_explicit")
        from crunch_node.crunch_config import CrunchConfig

        class ExplicitConfig(CrunchConfig):
            metrics: list[str] = ["custom_metric"]

        mod.ExplicitConfig = ExplicitConfig
        sys.modules["_test_explicit"] = mod
        os.environ["CRUNCH_CONFIG_MODULE"] = "_test_explicit:ExplicitConfig"

        try:
            config = load_config()
            self.assertIsInstance(config, ExplicitConfig)
            self.assertEqual(config.metrics, ["custom_metric"])
        finally:
            del sys.modules["_test_explicit"]

    def test_caches_result(self):
        c1 = load_config()
        c2 = load_config()
        self.assertIs(c1, c2)

    def test_reset_cache_works(self):
        c1 = load_config()
        reset_cache()
        c2 = load_config()
        self.assertIsNot(c1, c2)


class TestAggregationWindowSchema(unittest.TestCase):
    """AggregationWindow accepts only `hours` — rejects stale scaffold fields."""

    def test_hours_only(self):
        from crunch_node.crunch_config import AggregationWindow

        w = AggregationWindow(hours=24)
        self.assertEqual(w.hours, 24)

    def test_rejects_name_and_seconds(self):
        from pydantic import ValidationError

        from crunch_node.crunch_config import AggregationWindow

        with self.assertRaises(ValidationError):
            AggregationWindow(name="pnl_24h", seconds=86400)

    def test_rejects_extra_fields_even_with_hours(self):
        from pydantic import ValidationError

        from crunch_node.crunch_config import AggregationWindow

        with self.assertRaises(ValidationError):
            AggregationWindow(hours=24, name="pnl_24h", seconds=86400)


class TestAggregationSchema(unittest.TestCase):
    """Aggregation.windows must be a dict, ranking field is ranking_direction."""

    def test_windows_dict_accepted(self):
        from crunch_node.crunch_config import Aggregation, AggregationWindow

        agg = Aggregation(windows={"w1": AggregationWindow(hours=12)})
        self.assertIn("w1", agg.windows)

    def test_rejects_ranking_order(self):
        from pydantic import ValidationError

        from crunch_node.crunch_config import Aggregation

        with self.assertRaises(ValidationError):
            Aggregation(ranking_order="desc")

    def test_rejects_extra_fields(self):
        from pydantic import ValidationError

        from crunch_node.crunch_config import Aggregation

        with self.assertRaises(ValidationError):
            Aggregation(bogus="value")


class TestTryLoadValidationError(unittest.TestCase):
    """_try_load must log a warning (not debug) when instantiation fails."""

    def test_validation_error_logs_warning_and_reports_found(self):
        mod = types.ModuleType("_test_bad_config")

        class BadConfig:
            def __init__(self):
                raise ValueError("bad config value")

        mod.BadConfig = BadConfig
        sys.modules["_test_bad_config"] = mod

        try:
            with self.assertLogs("crunch_node.config_loader", level="WARNING") as cm:
                config, found = _try_load("_test_bad_config:BadConfig")
            self.assertIsNone(config)
            self.assertTrue(found)
            self.assertTrue(any("bad config value" in msg for msg in cm.output))
        finally:
            del sys.modules["_test_bad_config"]


class TestResolveConfigBrokenOverride(unittest.TestCase):
    """When an operator override is found but broken, _resolve_config must
    raise RuntimeError instead of falling back silently."""

    def setUp(self):
        reset_cache()
        self._saved_modules = {}
        for key in list(sys.modules):
            if key == "config" or key.startswith("config."):
                self._saved_modules[key] = sys.modules.pop(key)

    def tearDown(self):
        reset_cache()
        for key in list(sys.modules):
            if key == "config" or key.startswith("config."):
                del sys.modules[key]
        sys.modules.update(self._saved_modules)

    def test_raises_when_override_exists_but_broken(self):
        """Inject a broken config.crunch_config and verify that
        _resolve_config raises instead of falling back silently."""
        pkg = types.ModuleType("config")
        pkg.__path__ = []
        sys.modules["config"] = pkg

        mod = types.ModuleType("config.crunch_config")

        class BrokenConfig:
            def __init__(self):
                raise ValueError("broken scope")

        mod.CrunchConfig = BrokenConfig
        sys.modules["config.crunch_config"] = mod

        with self.assertRaises(RuntimeError) as ctx:
            load_config()

        self.assertIn("failed to instantiate", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

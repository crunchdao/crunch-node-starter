"""Tests for config_loader — resolve operator's CrunchConfig."""
from __future__ import annotations

import os
import sys
import types
import unittest

from coordinator_node.config_loader import load_config, reset_cache, _try_load


class TestTryLoad(unittest.TestCase):
    def test_loads_class_and_instantiates(self):
        mod = types.ModuleType("_test_config_mod")
        from coordinator_node.crunch_config import CrunchConfig

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
        from coordinator_node.crunch_config import CrunchConfig

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
        config, found = _try_load("coordinator_node.crunch_config:NonExistentClass")
        self.assertIsNone(config)
        self.assertFalse(found)


class TestLoadConfig(unittest.TestCase):
    def setUp(self):
        reset_cache()

    def tearDown(self):
        reset_cache()
        os.environ.pop("CRUNCH_CONFIG_MODULE", None)
        os.environ.pop("CONTRACT_MODULE", None)

    def test_falls_back_to_engine_default(self):
        config = load_config()
        from coordinator_node.crunch_config import CrunchConfig
        self.assertIsInstance(config, CrunchConfig)

    def test_explicit_env_var(self):
        mod = types.ModuleType("_test_explicit")
        from coordinator_node.crunch_config import CrunchConfig

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

    def test_backward_compat_contract_module_env(self):
        mod = types.ModuleType("_test_compat")
        from coordinator_node.crunch_config import CrunchConfig

        class CompatConfig(CrunchConfig):
            metrics: list[str] = ["compat"]

        mod.CompatConfig = CompatConfig
        sys.modules["_test_compat"] = mod
        os.environ["CONTRACT_MODULE"] = "_test_compat:CompatConfig"

        try:
            config = load_config()
            self.assertIsInstance(config, CompatConfig)
        finally:
            del sys.modules["_test_compat"]

    def test_caches_result(self):
        c1 = load_config()
        c2 = load_config()
        self.assertIs(c1, c2)

    def test_reset_cache_works(self):
        c1 = load_config()
        reset_cache()
        c2 = load_config()
        self.assertIsNot(c1, c2)


class TestPredictionScopeValidation(unittest.TestCase):
    """PredictionScope must accept horizon_seconds=0 for order-based competitions."""

    def test_horizon_zero_accepted(self):
        from coordinator_node.crunch_config import PredictionScope
        scope = PredictionScope(horizon_seconds=0)
        self.assertEqual(scope.horizon_seconds, 0)

    def test_horizon_negative_rejected(self):
        from coordinator_node.crunch_config import PredictionScope
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            PredictionScope(horizon_seconds=-1)


class TestAggregationWindowSchema(unittest.TestCase):
    """AggregationWindow accepts only `hours` — rejects stale scaffold fields."""

    def test_hours_only(self):
        from coordinator_node.crunch_config import AggregationWindow
        w = AggregationWindow(hours=24)
        self.assertEqual(w.hours, 24)

    def test_rejects_name_and_seconds(self):
        """Scaffold bug #2: AggregationWindow(name='pnl_24h', seconds=86400)."""
        from coordinator_node.crunch_config import AggregationWindow
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            AggregationWindow(name="pnl_24h", seconds=86400)

    def test_rejects_extra_fields_even_with_hours(self):
        from coordinator_node.crunch_config import AggregationWindow
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            AggregationWindow(hours=24, name="pnl_24h", seconds=86400)


class TestAggregationSchema(unittest.TestCase):
    """Aggregation.windows must be a dict, ranking field is ranking_direction."""

    def test_windows_dict_accepted(self):
        from coordinator_node.crunch_config import Aggregation, AggregationWindow
        agg = Aggregation(windows={"w1": AggregationWindow(hours=12)})
        self.assertIn("w1", agg.windows)

    def test_rejects_ranking_order(self):
        """Scaffold bug #4: ranking_order='desc' instead of ranking_direction."""
        from coordinator_node.crunch_config import Aggregation
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            Aggregation(ranking_order="desc")

    def test_rejects_extra_fields(self):
        from coordinator_node.crunch_config import Aggregation
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            Aggregation(bogus="value")


class TestTryLoadValidationError(unittest.TestCase):
    """_try_load must log a warning (not debug) when instantiation fails."""

    def test_validation_error_logs_warning_and_reports_found(self):
        """A config class whose __init__ raises should produce a warning log
        and return found=True so the caller knows the module existed."""
        mod = types.ModuleType("_test_bad_config")

        class BadConfig:
            def __init__(self):
                raise ValueError("bad config value")

        mod.BadConfig = BadConfig
        sys.modules["_test_bad_config"] = mod

        try:
            with self.assertLogs("coordinator_node.config_loader", level="WARNING") as cm:
                config, found = _try_load("_test_bad_config:BadConfig")
            self.assertIsNone(config)
            self.assertTrue(found)
            self.assertTrue(any("bad config value" in msg for msg in cm.output))
        finally:
            del sys.modules["_test_bad_config"]


class TestResolveConfigFallbackMessage(unittest.TestCase):
    """When an operator override is found but broken, the fallback message
    must say so — not 'no operator override found'."""

    def setUp(self):
        reset_cache()
        # Evict ALL runtime_definitions.* entries that prior tests may have
        # cached (e.g. scaffold integration tests with base/node on PYTHONPATH).
        self._saved_modules = {}
        for key in list(sys.modules):
            if key == "runtime_definitions" or key.startswith("runtime_definitions."):
                self._saved_modules[key] = sys.modules.pop(key)

    def tearDown(self):
        reset_cache()
        # Remove anything we injected
        for key in list(sys.modules):
            if key == "runtime_definitions" or key.startswith("runtime_definitions."):
                del sys.modules[key]
        # Restore previously cached modules
        sys.modules.update(self._saved_modules)

    def test_fallback_warns_when_override_exists_but_broken(self):
        """Inject a broken runtime_definitions.crunch_config and verify the
        fallback message mentions it failed, not 'no override found'."""
        # Create runtime_definitions package with empty path so no
        # submodules can be discovered from the real filesystem.
        pkg = types.ModuleType("runtime_definitions")
        pkg.__path__ = []
        sys.modules["runtime_definitions"] = pkg

        # Create crunch_config submodule with a broken CrunchConfig
        mod = types.ModuleType("runtime_definitions.crunch_config")

        class BrokenConfig:
            def __init__(self):
                raise ValueError("broken scope")

        mod.CrunchConfig = BrokenConfig
        sys.modules["runtime_definitions.crunch_config"] = mod

        with self.assertLogs("coordinator_node.config_loader", level="WARNING") as cm:
            config = load_config()

        # Should still get a working config (engine default)
        from coordinator_node.crunch_config import CrunchConfig
        self.assertIsInstance(config, CrunchConfig)

        # The WARNING must mention the failed path, not "no override found"
        all_output = "\n".join(cm.output)
        self.assertIn("runtime_definitions.crunch_config", all_output)
        self.assertIn("failed to instantiate", all_output)


class TestBackwardCompat(unittest.TestCase):
    """Verify old import paths still work."""

    def setUp(self):
        reset_cache()

    def tearDown(self):
        reset_cache()

    def test_import_from_contracts(self):
        from coordinator_node.contracts import CrunchContract
        from coordinator_node.crunch_config import CrunchConfig
        # CrunchContract now resolves via config_loader → returns an instance
        self.assertIsInstance(CrunchContract, CrunchConfig)

    def test_crunch_contract_returns_same_cached_instance(self):
        from coordinator_node import contracts
        a = contracts.CrunchContract
        b = contracts.CrunchContract
        self.assertIs(a, b)

    def test_crunch_config_class_still_importable(self):
        from coordinator_node.contracts import CrunchConfig
        # The raw class must still be importable for subclassing
        self.assertTrue(isinstance(CrunchConfig, type))

    def test_import_load_contract(self):
        from coordinator_node.contract_loader import load_contract
        from coordinator_node.config_loader import load_config
        # Both should be the same function
        self.assertIs(load_contract, load_config)


if __name__ == "__main__":
    unittest.main()

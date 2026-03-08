"""Scaffold integration tests — verify CrunchConfig wiring is consistent.

These tests catch silent mismatches between the pieces a coordinator wires
together: config files, type definitions, scoring, ground truth resolution,
aggregation, and the model interface.  They run without Docker, DB, or
network — just imports and in-memory calls.

After scaffolding a new competition, run these FIRST.  Any failure means the
pipeline will break at runtime in a way that's hard to diagnose.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

# ── Paths ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent / "base"
NODE_DIR = BASE_DIR / "node"
CHALLENGE_DIR = BASE_DIR / "challenge"
# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def crunch_config():
    """Load the scaffold's CrunchConfig (config/ override)."""
    from config.crunch_config import CrunchConfig

    return CrunchConfig()


@pytest.fixture(scope="module")
def prediction_configs(crunch_config) -> list[dict[str, Any]]:
    """Load scheduled predictions from CrunchConfig."""
    return [
        {
            "scope_key": sp.scope_key,
            "scope_template": sp.scope,
            "schedule": {
                "prediction_interval_seconds": sp.prediction_interval_seconds,
                "resolve_horizon_seconds": sp.resolve_horizon_seconds,
            },
            "active": sp.active,
            "order": sp.order,
            "meta": sp.meta,
        }
        for sp in crunch_config.scheduled_predictions
    ]


@pytest.fixture(scope="module")
def scoring_function():
    """Load the scoring function the scaffold wires via callables.env."""
    from starter_challenge.scoring import score_prediction

    return score_prediction


# ── 1. Config file validates ───────────────────────────────────────────


class TestConfigFileValid:
    """CrunchConfig.scheduled_predictions must validate without error."""

    def test_has_predictions(self, crunch_config):
        assert len(crunch_config.scheduled_predictions) > 0, (
            "No scheduled_predictions defined in CrunchConfig"
        )

    def test_each_entry_validates_as_envelope(self, prediction_configs):
        from crunch_node.schemas import ScheduledPredictionConfigEnvelope

        for i, entry in enumerate(prediction_configs):
            try:
                ScheduledPredictionConfigEnvelope.model_validate(entry)
            except Exception as exc:
                pytest.fail(f"Config entry [{i}] failed validation: {exc}")

    def test_schedule_envelope_validates(self, prediction_configs):
        """Catches typos like 'every_seconds' (extra="forbid" on ScheduleEnvelope)."""
        from crunch_node.schemas import ScheduleEnvelope

        for i, entry in enumerate(prediction_configs):
            schedule = entry.get("schedule", {})
            try:
                ScheduleEnvelope.model_validate(schedule)
            except Exception as exc:
                pytest.fail(f"Config entry [{i}] schedule failed validation: {exc}")


# ── 2. scope_template ↔ PredictionScope ↔ CallMethodConfig ────────────


class TestScopeTemplateAlignment:
    """scope_template keys must land on real PredictionScope fields,
    and CallMethodConfig.args must be resolvable from the merged scope."""

    def test_scope_template_keys_are_valid_scope_fields(
        self, crunch_config, prediction_configs
    ):
        scope_fields = set(type(crunch_config.scope).model_fields.keys())
        for i, entry in enumerate(prediction_configs):
            template = entry.get("scope_template", {})
            unknown = set(template.keys()) - scope_fields
            assert not unknown, (
                f"Config entry [{i}] scope_template has keys {unknown} "
                f"not in PredictionScope fields {scope_fields}. "
                f"These values will be silently ignored."
            )

    def test_call_method_args_resolvable_from_scope(
        self, crunch_config, prediction_configs
    ):
        """Every arg the model runner sends must exist in the merged scope.

        resolve_horizon_seconds is injected at runtime from
        ScheduledPrediction.resolve_horizon_seconds, so it's always available.
        """
        # runtime-injected keys that don't come from PredictionScope
        runtime_injected = {"resolve_horizon_seconds"}

        scope_defaults = crunch_config.scope.model_dump()
        for i, entry in enumerate(prediction_configs):
            template = entry.get("scope_template", {})
            merged = {**scope_defaults, **template}
            for arg in crunch_config.call_method.args:
                if arg.name in runtime_injected:
                    continue
                assert arg.name in merged, (
                    f"Config entry [{i}]: CallMethodConfig.arg '{arg.name}' "
                    f"not found in merged scope {set(merged.keys())}. "
                    f"predict() will receive a default/empty value."
                )

    def test_scope_template_not_empty_when_multi_subject(
        self, crunch_config, prediction_configs
    ):
        """If there are multiple configs, each should specify a subject."""
        if len(prediction_configs) <= 1:
            pytest.skip("Single config — multi-subject check not applicable")
        for i, entry in enumerate(prediction_configs):
            template = entry.get("scope_template", {})
            assert "subject" in template, (
                f"Config entry [{i}] has no 'subject' in scope_template. "
                f"In multi-config setups, each entry needs an explicit subject."
            )


# ── 3. resolve_ground_truth + RawInput shape ──────────────────────────


class TestGroundTruthResolution:
    """resolve_ground_truth must produce non-None output from data
    matching the RawInput shape the feed actually produces."""

    def _make_feed_record(self, subject: str, price: float, ts: datetime):
        from crunch_node.entities.feed_record import FeedRecord

        return FeedRecord(
            source="binance",
            subject=subject,
            kind="candle",
            granularity="1m",
            ts_event=ts,
            values={
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 1.0,
            },
        )

    def test_produces_result_with_two_records(self, crunch_config):
        now = datetime.now(UTC)
        records = [
            self._make_feed_record("BTC", 40000.0, now),
            self._make_feed_record("BTC", 40100.0, now),
        ]
        result = crunch_config.resolve_ground_truth(records)
        assert result is not None, (
            "resolve_ground_truth returned None for valid feed records. "
            "Scoring will never run."
        )

    def test_result_has_expected_keys(self, crunch_config):
        now = datetime.now(UTC)
        records = [
            self._make_feed_record("BTC", 40000.0, now),
            self._make_feed_record("BTC", 40100.0, now),
        ]
        result = crunch_config.resolve_ground_truth(records)
        # Default resolver produces price return fields
        for key in (
            "symbol",
            "asof_ts",
            "entry_price",
            "resolved_price",
            "profit",
            "direction_up",
        ):
            assert key in result, (
                f"resolve_ground_truth missing key '{key}'. "
                f"Scoring function may KeyError at runtime."
            )

    def test_result_has_price_data(self, crunch_config):
        now = datetime.now(UTC)
        records = [
            self._make_feed_record("BTC", 40000.0, now),
            self._make_feed_record("BTC", 40100.0, now),
        ]
        result = crunch_config.resolve_ground_truth(records)
        assert result["entry_price"] > 0, (
            "resolve_ground_truth returned zero entry_price. "
            "Scoring function cannot compute returns."
        )
        assert result["resolved_price"] > 0, (
            "resolve_ground_truth returned zero resolved_price. "
            "Scoring function cannot compute returns."
        )
        assert result["profit"] != 0, (
            "resolve_ground_truth returned zero profit for different prices."
        )

    def test_returns_none_for_empty_records(self, crunch_config):
        result = crunch_config.resolve_ground_truth([])
        assert result is None


# ── 4. Scoring pipeline roundtrip ─────────────────────────────────────


class TestScoringPipelineRoundtrip:
    """InferenceOutput defaults → scoring_function → ScoreResult.
    Catches field-name mismatches between the three."""

    def test_scoring_accepts_default_inference_output(
        self, crunch_config, scoring_function
    ):
        """scoring_function must not KeyError on default InferenceOutput fields."""
        sample_output = crunch_config.output_type().model_dump()
        sample_gt = {
            "entry_price": 40000,
            "resolved_price": 40100,
            "profit": 0.0025,
            "direction_up": True,
        }
        # Must not raise
        result = scoring_function(sample_output, sample_gt)
        assert isinstance(result, dict)

    def test_scoring_output_validates_as_score_result(
        self, crunch_config, scoring_function
    ):
        sample_output = crunch_config.output_type().model_dump()
        sample_gt = {
            "entry_price": 40000,
            "resolved_price": 40100,
            "profit": 0.0025,
            "direction_up": True,
        }
        result = scoring_function(sample_output, sample_gt)
        try:
            crunch_config.score_type(**result)
        except Exception as exc:
            pytest.fail(
                f"Scoring output {result!r} does not validate as "
                f"{crunch_config.score_type.__name__}: {exc}"
            )

    def test_score_result_has_value_field(self, crunch_config, scoring_function):
        sample_output = crunch_config.output_type().model_dump()
        sample_gt = {
            "entry_price": 40000,
            "resolved_price": 40100,
            "profit": 0.0025,
            "direction_up": True,
        }
        result = scoring_function(sample_output, sample_gt)
        validated = crunch_config.score_type(**result)
        assert hasattr(validated, "value"), "ScoreResult must have a 'value' field"
        assert isinstance(validated.value, (int, float))


# ── 5. Aggregation roundtrip ──────────────────────────────────────────


class TestAggregationRoundtrip:
    """aggregate_snapshot must handle score results and produce
    something the leaderboard ranking can use."""

    def test_aggregates_score_results(self, crunch_config, scoring_function):
        sample_output = crunch_config.output_type().model_dump()
        sample_gt = {
            "entry_price": 40000,
            "resolved_price": 40100,
            "profit": 0.0025,
            "direction_up": True,
        }
        score_result = scoring_function(sample_output, sample_gt)

        summary = crunch_config.aggregate_snapshot([score_result, score_result])
        assert isinstance(summary, dict)
        assert len(summary) > 0, (
            "aggregate_snapshot returned empty dict — leaderboard will have no data"
        )

    def test_ranking_key_is_a_known_aggregation_source(
        self, crunch_config, scoring_function
    ):
        """The ranking key must come from either a windowed aggregation window
        name or a key that aggregate_snapshot / compute_metrics produces.

        Windowed keys (score_recent, score_steady, score_anchor) are computed
        by ScoreService._aggregate_from_snapshots over snapshot summaries.
        The ranking_key can reference either those OR a direct metric key.
        """
        sample_output = crunch_config.output_type().model_dump()
        sample_gt = {
            "entry_price": 40000,
            "resolved_price": 40100,
            "profit": 0.0025,
            "direction_up": True,
        }
        score_result = scoring_function(sample_output, sample_gt)

        summary = crunch_config.aggregate_snapshot([score_result])
        ranking_key = crunch_config.aggregation.ranking_key

        # Valid sources: windowed aggregation keys, snapshot summary keys, or metric names
        window_keys = set(crunch_config.aggregation.windows.keys())
        summary_keys = set(summary.keys())
        metric_keys = set(crunch_config.metrics)
        all_valid = window_keys | summary_keys | metric_keys

        assert ranking_key in all_valid, (
            f"Aggregation.ranking_key='{ranking_key}' not found in any known source: "
            f"window_keys={window_keys}, summary_keys={summary_keys}, metric_keys={metric_keys}. "
            f"Leaderboard ranking will always be 0.0."
        )

    def test_value_field_exists_in_snapshots(self, crunch_config, scoring_function):
        """The windowed aggregation reads ``value_field`` from each snapshot's
        result_summary. That field must exist so windows aren't always 0."""
        value_field = crunch_config.aggregation.value_field

        sample_output = crunch_config.output_type().model_dump()
        sample_gt = {
            "entry_price": 40000,
            "resolved_price": 40100,
            "profit": 0.0025,
            "direction_up": True,
        }
        score_result = scoring_function(sample_output, sample_gt)
        summary = crunch_config.aggregate_snapshot([score_result])

        assert value_field in summary, (
            f"Aggregation.value_field='{value_field}' not found in "
            f"aggregate_snapshot output {set(summary.keys())}. "
            f"All windowed values will be 0.0."
        )

    def test_handles_empty_input(self, crunch_config):
        summary = crunch_config.aggregate_snapshot([])
        assert isinstance(summary, dict)


# ── 6. Tracker output ↔ InferenceOutput ───────────────────────────────


class TestTrackerOutputMatchesInferenceOutput:
    """Example trackers must return dicts that validate as InferenceOutput."""

    @pytest.fixture(
        params=[
            "starter_challenge.examples.mean_reversion_tracker:MeanReversionTracker",
            "starter_challenge.examples.trend_following_tracker:TrendFollowingTracker",
            "starter_challenge.examples.volatility_regime_tracker:VolatilityRegimeTracker",
        ]
    )
    def example_tracker(self, request):
        module_path, class_name = request.param.split(":")
        import importlib

        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        return cls()

    def test_predict_output_validates_as_inference_output(
        self, crunch_config, example_tracker
    ):
        tick_data = {
            "symbol": "BTC",
            "asof_ts": 1700000000,
            "candles_1m": [
                {"ts": 1700000000 + i * 60, "close": 40000 + i * 10} for i in range(10)
            ],
        }
        example_tracker.feed_update(tick_data)

        scope = crunch_config.scope.model_dump()
        resolve = crunch_config.scheduled_predictions[0].resolve_horizon_seconds
        output = example_tracker.predict(
            subject=scope["subject"],
            resolve_horizon_seconds=resolve,
            step_seconds=scope["step_seconds"],
        )

        try:
            crunch_config.output_type(**output)
        except Exception as exc:
            pytest.fail(
                f"Tracker output {output!r} does not validate as "
                f"{crunch_config.output_type.__name__}: {exc}. "
                f"The model runner will reject this prediction."
            )

    def test_predict_output_keys_match_scoring_expectations(
        self, crunch_config, scoring_function, example_tracker
    ):
        """Full roundtrip: tracker.predict() → scoring_function() — no KeyError."""
        tick_data = {
            "symbol": "BTC",
            "asof_ts": 1700000000,
            "candles_1m": [
                {"ts": 1700000000 + i * 60, "close": 40000 + i * 10} for i in range(10)
            ],
        }
        example_tracker.feed_update(tick_data)

        scope = crunch_config.scope.model_dump()
        resolve = crunch_config.scheduled_predictions[0].resolve_horizon_seconds
        output = example_tracker.predict(
            subject=scope["subject"],
            resolve_horizon_seconds=resolve,
            step_seconds=scope["step_seconds"],
        )
        ground_truth = {
            "entry_price": 40000,
            "resolved_price": 40100,
            "profit": 0.0025,
            "direction_up": True,
        }

        # Must not raise KeyError
        result = scoring_function(output, ground_truth)
        assert isinstance(result, dict)

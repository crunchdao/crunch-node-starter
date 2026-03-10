"""Prediction pack validation — catches integration bugs before deployment.

Verifies internal consistency of packs/realtime/:
- Scoring function accepts Pydantic models (not dicts)
- feed_normalizer matches FEED_KIND in .local.env.example
- resolve_ground_truth produces output compatible with scoring
- Scope subjects match FEED_SUBJECTS
- Single authoritative scoring path (no conflicting dual functions)
- End-to-end pipeline roundtrip works with real data shapes
"""

from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

# ── Pack paths ─────────────────────────────────────────────────────────
PACK_DIR = Path(__file__).resolve().parent.parent / "packs" / "prediction"
PACK_NODE_CONFIG = PACK_DIR / "node" / "config"
PACK_CHALLENGE = PACK_DIR / "challenge"
PACK_ENV_EXAMPLE = PACK_DIR / "node" / ".local.env.example"


# ── Helpers ────────────────────────────────────────────────────────────


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict, ignoring comments and blank lines."""
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def _load_pack_config():
    """Import the pack's crunch_config module.

    The pack's crunch_config imports from starter_challenge.scoring,
    so we must temporarily replace starter_challenge in sys.modules
    with the pack's version, then restore the scaffold's after loading.
    """
    import types

    scoring_path = PACK_CHALLENGE / "starter_challenge" / "scoring.py"

    # Save existing starter_challenge modules
    saved = {
        k: sys.modules[k]
        for k in list(sys.modules)
        if k.startswith("starter_challenge")
    }

    try:
        # Clear scaffold versions
        for k in saved:
            del sys.modules[k]

        # Register a minimal starter_challenge package (no __init__ execution)
        pkg_mod = types.ModuleType("starter_challenge")
        pkg_mod.__path__ = [str(PACK_CHALLENGE / "starter_challenge")]
        sys.modules["starter_challenge"] = pkg_mod

        # Load the pack's scoring submodule
        scoring_spec = importlib.util.spec_from_file_location(
            "starter_challenge.scoring", scoring_path
        )
        scoring_mod = importlib.util.module_from_spec(scoring_spec)
        sys.modules["starter_challenge.scoring"] = scoring_mod
        scoring_spec.loader.exec_module(scoring_mod)

        # Load crunch_config (it will find starter_challenge.scoring)
        config_path = PACK_NODE_CONFIG / "crunch_config.py"
        config_spec = importlib.util.spec_from_file_location(
            "pack_realtime_crunch_config", config_path
        )
        config_mod = importlib.util.module_from_spec(config_spec)
        config_spec.loader.exec_module(config_mod)

        config_mod.CrunchConfig.model_rebuild()
        return config_mod.CrunchConfig()
    finally:
        # Restore scaffold modules
        for k in list(sys.modules):
            if k.startswith("starter_challenge"):
                del sys.modules[k]
        sys.modules.update(saved)


def _load_pack_scoring():
    """Import the pack's challenge scoring module directly from file."""
    scoring_path = PACK_CHALLENGE / "starter_challenge" / "scoring.py"
    assert scoring_path.exists(), f"Missing {scoring_path}"

    spec = importlib.util.spec_from_file_location("pack_realtime_scoring", scoring_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.score_prediction


def _make_feed_record(subject: str, price: float, ts: datetime):
    """Create a FeedRecord matching candle feed output."""
    from crunch_node.entities.feed_record import FeedRecord

    return FeedRecord(
        source="binance",
        subject=subject,
        kind="candle",
        granularity="1s",
        ts_event=ts,
        values={
            "open": price,
            "high": price + 10,
            "low": price - 10,
            "close": price,
            "volume": 1.0,
        },
    )


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def pack_config():
    return _load_pack_config()


@pytest.fixture(scope="module")
def pack_scoring():
    return _load_pack_scoring()


@pytest.fixture(scope="module")
def env_config() -> dict[str, str]:
    assert PACK_ENV_EXAMPLE.exists(), f"Missing {PACK_ENV_EXAMPLE}"
    return _parse_env_file(PACK_ENV_EXAMPLE)


# ── 1. Scoring accepts Pydantic models (Bug 1) ────────────────────────


class TestScoringAcceptsPydantic:
    """Scoring function must accept Pydantic models — the engine always
    coerces to typed objects before calling scoring."""

    def test_challenge_scoring_accepts_pydantic_output(self, pack_config, pack_scoring):
        """scoring.py must work with Pydantic model instances, not just dicts."""
        output = pack_config.output_type(value=0.5)
        gt = pack_config.ground_truth_type.model_validate(
            {
                "profit": 0.01,
                "entry_price": 40000,
                "resolved_price": 40040,
                "direction_up": True,
            }
        )
        result = pack_scoring(output, gt)
        # Must return a BaseModel or dict with required keys
        result_dict = result.model_dump() if hasattr(result, "model_dump") else result
        assert "value" in result_dict
        assert "success" in result_dict

    def test_config_scoring_accepts_pydantic_models(self, pack_config):
        """CrunchConfig.scoring_function must accept typed Pydantic objects."""
        output = pack_config.output_type(value=0.5)
        gt = pack_config.ground_truth_type.model_validate(
            {
                "profit": 0.01,
                "entry_price": 40000,
                "resolved_price": 40040,
                "direction_up": True,
            }
        )
        result = pack_config.scoring_function(output, gt)
        result_dict = result.model_dump() if hasattr(result, "model_dump") else result
        assert "value" in result_dict
        assert result_dict.get("success", True) is True


# ── 2. feed_normalizer ↔ FEED_KIND consistency (Bug 2) ────────────────


class TestFeedNormalizerMatchesEnv:
    """feed_normalizer in CrunchConfig must match FEED_KIND in .local.env.example."""

    def test_normalizer_matches_feed_kind(self, pack_config, env_config):
        feed_kind = env_config.get("FEED_KIND", "candle")
        normalizer = pack_config.feed_normalizer
        assert normalizer == feed_kind, (
            f"CrunchConfig.feed_normalizer='{normalizer}' but "
            f".local.env.example FEED_KIND='{feed_kind}'. "
            f"The normalizer will skip all feed records."
        )


# ── 3. resolve_ground_truth → scoring roundtrip (Bug 3) ───────────────


class TestGroundTruthScoringRoundtrip:
    """resolve_ground_truth output must be compatible with the scoring function."""

    def test_resolve_produces_valid_ground_truth(self, pack_config):
        """resolve_ground_truth must produce output parseable as ground_truth_type."""
        now = datetime.now(UTC)
        records = [
            _make_feed_record("BTC", 40000.0, now),
            _make_feed_record("BTC", 40100.0, now),
        ]
        result = pack_config.resolve_ground_truth(records)
        assert result is not None, "resolve_ground_truth returned None for valid data"

        # Must parse as the configured ground_truth_type
        gt = pack_config.ground_truth_type.model_validate(result)
        assert gt is not None

    def test_resolved_ground_truth_scores_successfully(self, pack_config):
        """Full roundtrip: feed records → resolve → score must produce success=True."""
        now = datetime.now(UTC)
        records = [
            _make_feed_record("BTC", 40000.0, now),
            _make_feed_record("BTC", 40100.0, now),
        ]
        gt_dict = pack_config.resolve_ground_truth(records)
        assert gt_dict is not None

        gt = pack_config.ground_truth_type.model_validate(gt_dict)
        output = pack_config.output_type(value=0.5)

        result = pack_config.scoring_function(output, gt)
        result_dict = result.model_dump() if hasattr(result, "model_dump") else result
        assert result_dict.get("success", True) is True, (
            f"Scoring failed: {result_dict.get('failed_reason')}"
        )
        assert result_dict["value"] != 0.0, (
            "Scoring returned 0.0 for different entry/resolved prices — "
            "ground truth fields may not match what scoring reads"
        )


# ── 4. Scope subject ↔ FEED_SUBJECTS (Bug 4) ──────────────────────────


class TestScopeSubjectMatchesFeed:
    """Scope subjects in scheduled_predictions must match FEED_SUBJECTS."""

    def test_scope_subjects_are_feed_subjects(self, pack_config, env_config):
        feed_subjects = {
            s.strip()
            for s in env_config.get("FEED_SUBJECTS", "").split(",")
            if s.strip()
        }
        scope_subjects = {
            sp.scope.get("subject", "") for sp in pack_config.scheduled_predictions
        }

        assert scope_subjects, "No subjects found in scheduled_predictions scopes"
        assert feed_subjects, "No FEED_SUBJECTS in .local.env.example"

        mismatched = scope_subjects - feed_subjects
        assert not mismatched, (
            f"Scope subjects {mismatched} not in FEED_SUBJECTS {feed_subjects}. "
            f"Models will receive no data for these subjects — predict() gets None."
        )


# ── 5. Single authoritative scoring path (Bug 5) ──────────────────────


class TestSingleScoringPath:
    """If CrunchConfig.scoring_function is set, it must be consistent
    with the challenge scoring.py. No conflicting dual functions."""

    def test_config_and_challenge_scoring_produce_same_results(
        self, pack_config, pack_scoring, env_config
    ):
        """When CrunchConfig sets scoring_function AND .local.env points to
        a different one, the pipeline will silently use the CrunchConfig one.
        Both must produce identical results for the same inputs."""
        if pack_config.scoring_function is None:
            pytest.skip("No scoring_function in CrunchConfig — env var used")

        env_scoring_ref = env_config.get("SCORING_FUNCTION", "")
        if not env_scoring_ref:
            pytest.skip("No SCORING_FUNCTION in .local.env.example")

        # Build typed inputs
        output = pack_config.output_type(value=0.5)
        gt = pack_config.ground_truth_type.model_validate(
            {
                "profit": 0.01,
                "entry_price": 40000,
                "resolved_price": 40040,
                "direction_up": True,
            }
        )

        # Both functions must accept the same Pydantic types
        config_result = pack_config.scoring_function(output, gt)
        challenge_result = pack_scoring(output, gt)

        def _to_dict(r):
            return r.model_dump() if hasattr(r, "model_dump") else r

        config_dict = _to_dict(config_result)
        challenge_dict = _to_dict(challenge_result)

        assert config_dict["value"] == challenge_dict["value"], (
            f"CrunchConfig.scoring_function and challenge scoring.py produce "
            f"different scores: {config_dict['value']} vs {challenge_dict['value']}. "
            f"CrunchConfig takes precedence — env var SCORING_FUNCTION={env_scoring_ref} "
            f"will be silently ignored."
        )

    def test_config_scoring_produces_valid_score_type(self, pack_config):
        """CrunchConfig.scoring_function output must validate as score_type."""
        output = pack_config.output_type(value=0.5)
        gt = pack_config.ground_truth_type.model_validate(
            {
                "profit": 0.01,
                "entry_price": 40000,
                "resolved_price": 40040,
                "direction_up": True,
            }
        )

        if pack_config.scoring_function is None:
            pytest.skip("No scoring_function in CrunchConfig")

        result = pack_config.scoring_function(output, gt)
        result_dict = result.model_dump() if hasattr(result, "model_dump") else result
        try:
            pack_config.score_type.model_validate(result_dict)
        except Exception as exc:
            pytest.fail(
                f"CrunchConfig.scoring_function output {result_dict!r} "
                f"does not validate as {pack_config.score_type.__name__}: {exc}"
            )


# ── 6. End-to-end pipeline roundtrip ──────────────────────────────────


class TestEndToEndRoundtrip:
    """Full pipeline: FeedRecord → resolve → score → validate.
    Catches any combination of mismatches across the whole chain."""

    def test_bullish_prediction_scored_correctly(self, pack_config):
        """Bullish prediction + price up = positive score."""
        now = datetime.now(UTC)
        records = [
            _make_feed_record("BTC", 40000.0, now),
            _make_feed_record("BTC", 40100.0, now),
        ]
        gt_dict = pack_config.resolve_ground_truth(records)
        assert gt_dict is not None

        gt = pack_config.ground_truth_type.model_validate(gt_dict)
        output = pack_config.output_type(value=0.5)  # bullish

        result = pack_config.scoring_function(output, gt)
        result_dict = result.model_dump() if hasattr(result, "model_dump") else result
        validated = pack_config.score_type.model_validate(result_dict)

        assert validated.value > 0, (
            f"Bullish prediction + price up should score positive, got {validated.value}"
        )

    def test_bearish_prediction_scored_correctly(self, pack_config):
        """Bearish prediction + price down = positive score."""
        now = datetime.now(UTC)
        records = [
            _make_feed_record("BTC", 40100.0, now),
            _make_feed_record("BTC", 40000.0, now),
        ]
        gt_dict = pack_config.resolve_ground_truth(records)
        gt = pack_config.ground_truth_type.model_validate(gt_dict)
        output = pack_config.output_type(value=-0.5)  # bearish

        result = pack_config.scoring_function(output, gt)
        result_dict = result.model_dump() if hasattr(result, "model_dump") else result
        validated = pack_config.score_type.model_validate(result_dict)

        assert validated.value > 0, (
            f"Bearish prediction + price down should score positive, got {validated.value}"
        )

    def test_wrong_direction_scores_negative(self, pack_config):
        """Bullish prediction + price down = negative score."""
        now = datetime.now(UTC)
        records = [
            _make_feed_record("BTC", 40100.0, now),
            _make_feed_record("BTC", 40000.0, now),
        ]
        gt_dict = pack_config.resolve_ground_truth(records)
        gt = pack_config.ground_truth_type.model_validate(gt_dict)
        output = pack_config.output_type(value=0.5)  # bullish but price went down

        result = pack_config.scoring_function(output, gt)
        result_dict = result.model_dump() if hasattr(result, "model_dump") else result
        validated = pack_config.score_type.model_validate(result_dict)

        assert validated.value < 0, (
            f"Wrong direction should score negative, got {validated.value}"
        )

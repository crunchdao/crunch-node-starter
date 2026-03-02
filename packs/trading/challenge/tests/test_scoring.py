"""Tests for trading signal scoring."""

from __future__ import annotations

import pytest
from starter_challenge.scoring import score_prediction


class TestScoringContract:
    """Shape/type requirements — must pass for ANY valid implementation."""

    def test_returns_dict(self):
        result = score_prediction({"signal": 0.5}, {"profit": 0.01})
        assert isinstance(result, dict)

    def test_has_value_key(self):
        result = score_prediction({"signal": 0.5}, {"profit": 0.01})
        assert "value" in result
        assert isinstance(result["value"], (int, float))

    def test_has_success_key(self):
        result = score_prediction({"signal": 0.5}, {"profit": 0.01})
        assert "success" in result
        assert isinstance(result["success"], bool)

    def test_has_failed_reason_key(self):
        result = score_prediction({"signal": 0.5}, {"profit": 0.01})
        assert "failed_reason" in result
        assert result["failed_reason"] is None or isinstance(
            result["failed_reason"], str
        )

    def test_has_pnl_key(self):
        result = score_prediction({"signal": 0.5}, {"profit": 0.01})
        assert "pnl" in result
        assert isinstance(result["pnl"], (int, float))


class TestScoringBehavior:
    """Behavioral tests for PnL-based trading scoring."""

    def test_correct_long_scores_positive(self):
        """Long signal + price up = positive PnL."""
        result = score_prediction({"signal": 0.8}, {"profit": 0.02})
        assert result["value"] > 0
        assert result["direction_correct"] is True

    def test_correct_short_scores_positive(self):
        """Short signal + price down = positive PnL."""
        result = score_prediction({"signal": -0.8}, {"profit": -0.02})
        assert result["value"] > 0
        assert result["direction_correct"] is True

    def test_wrong_long_scores_negative(self):
        """Long signal + price down = negative PnL."""
        result = score_prediction({"signal": 0.8}, {"profit": -0.02})
        assert result["value"] < 0
        assert result["direction_correct"] is False

    def test_wrong_short_scores_negative(self):
        """Short signal + price up = negative PnL."""
        result = score_prediction({"signal": -0.8}, {"profit": 0.02})
        assert result["value"] < 0
        assert result["direction_correct"] is False

    def test_zero_signal_only_pays_no_spread(self):
        """Flat position = no PnL, no spread cost."""
        result = score_prediction({"signal": 0.0}, {"profit": 0.05})
        assert result["value"] == 0.0
        assert result["spread_cost"] == 0.0

    def test_spread_cost_is_deducted(self):
        """PnL includes spread cost proportional to signal magnitude."""
        result = score_prediction({"signal": 1.0}, {"profit": 0.0})
        assert result["value"] < 0  # pure spread cost
        assert result["spread_cost"] > 0

    def test_signal_clamped_to_range(self):
        """Signals outside [-1, 1] are clamped."""
        result = score_prediction({"signal": 5.0}, {"profit": 0.01})
        assert result["signal_clamped"] == 1.0

        result = score_prediction({"signal": -5.0}, {"profit": 0.01})
        assert result["signal_clamped"] == -1.0

    def test_higher_conviction_amplifies_pnl(self):
        """Higher |signal| = larger absolute PnL."""
        low = score_prediction({"signal": 0.2}, {"profit": 0.01})
        high = score_prediction({"signal": 0.8}, {"profit": 0.01})
        assert abs(high["pnl"]) > abs(low["pnl"])

    def test_invalid_signal_fails_gracefully(self):
        """Non-numeric signal returns failure."""
        result = score_prediction({"signal": "bad"}, {"profit": 0.01})
        assert result["success"] is False
        assert result["failed_reason"] is not None

    def test_missing_signal_defaults_to_zero(self):
        """Missing signal key treated as 0.0 (flat)."""
        result = score_prediction({}, {"profit": 0.01})
        assert result["value"] == 0.0
        assert result["success"] is True

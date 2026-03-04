"""Tests for contribution-weighted emission strategy."""

from __future__ import annotations

import unittest

from crunch_node.crunch_config import FRAC_64_MULTIPLIER
from crunch_node.extensions.emission_strategies import (
    contribution_weighted_emission,
)


class TestContributionWeightedEmission(unittest.TestCase):
    def _make_entries(self, data: list[dict]) -> list[dict]:
        """Build ranked entries with result_summary."""
        entries = []
        for i, d in enumerate(data):
            entries.append(
                {
                    "rank": d.get("rank", i + 1),
                    "model_id": d.get("model_id", f"m{i + 1}"),
                    "result_summary": d.get("summary", {}),
                }
            )
        return entries

    def test_rewards_sum_to_frac64(self):
        entries = self._make_entries(
            [
                {
                    "rank": 1,
                    "summary": {"contribution": 0.05, "model_correlation": 0.2},
                },
                {
                    "rank": 2,
                    "summary": {"contribution": 0.02, "model_correlation": 0.8},
                },
                {
                    "rank": 3,
                    "summary": {"contribution": -0.01, "model_correlation": 0.5},
                },
            ]
        )
        emission = contribution_weighted_emission(entries, "crunch123")

        total = sum(r["reward_pct"] for r in emission["cruncher_rewards"])
        self.assertEqual(total, FRAC_64_MULTIPLIER)

    def test_high_contribution_gets_more(self):
        entries = self._make_entries(
            [
                {
                    "rank": 1,
                    "summary": {"contribution": 0.10, "model_correlation": 0.1},
                },
                {
                    "rank": 2,
                    "summary": {"contribution": 0.00, "model_correlation": 0.9},
                },
            ]
        )
        emission = contribution_weighted_emission(entries, "crunch123")

        rewards = emission["cruncher_rewards"]
        # Model 1: high contribution + low correlation → more reward
        self.assertGreater(rewards[0]["reward_pct"], rewards[1]["reward_pct"])

    def test_diverse_model_gets_more(self):
        """Model with low correlation should get diversity bonus."""
        entries = self._make_entries(
            [
                {
                    "rank": 1,
                    "summary": {"contribution": 0.02, "model_correlation": 0.1},
                },
                {
                    "rank": 2,
                    "summary": {"contribution": 0.02, "model_correlation": 0.9},
                },
            ]
        )
        emission = contribution_weighted_emission(
            entries,
            "crunch123",
            rank_weight=0.0,
            contribution_weight=0.0,
            diversity_weight=1.0,
        )

        rewards = emission["cruncher_rewards"]
        # Pure diversity: low-correlation model gets more
        self.assertGreater(rewards[0]["reward_pct"], rewards[1]["reward_pct"])

    def test_empty_entries(self):
        emission = contribution_weighted_emission([], "crunch123")
        self.assertEqual(emission["cruncher_rewards"], [])

    def test_single_model_gets_all(self):
        entries = self._make_entries(
            [
                {"rank": 1, "summary": {"contribution": 0.05}},
            ]
        )
        emission = contribution_weighted_emission(entries, "crunch123")
        self.assertEqual(
            emission["cruncher_rewards"][0]["reward_pct"], FRAC_64_MULTIPLIER
        )

    def test_min_pct_floor(self):
        entries = self._make_entries(
            [
                {"rank": 1, "summary": {"contribution": 1.0, "model_correlation": 0.0}},
                {
                    "rank": 2,
                    "summary": {"contribution": -1.0, "model_correlation": 1.0},
                },
            ]
        )
        emission = contribution_weighted_emission(entries, "crunch123", min_pct=5.0)

        rewards = emission["cruncher_rewards"]
        # Worst model should still get at least min_pct equivalent
        min_frac = int(
            5.0 / 100.0 * FRAC_64_MULTIPLIER * 0.9
        )  # ~4.5% accounting for renorm
        self.assertGreater(rewards[1]["reward_pct"], min_frac)

    def test_providers_passed_through(self):
        entries = self._make_entries([{"rank": 1}])
        emission = contribution_weighted_emission(
            entries,
            "crunch123",
            compute_provider="cp_wallet",
            data_provider="dp_wallet",
        )
        self.assertEqual(
            emission["compute_provider_rewards"][0]["provider"], "cp_wallet"
        )
        self.assertEqual(emission["data_provider_rewards"][0]["provider"], "dp_wallet")


if __name__ == "__main__":
    unittest.main()

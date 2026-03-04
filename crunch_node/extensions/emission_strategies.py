"""Built-in emission strategies for reward distribution.

The default `build_emission` uses tier-based ranking (1st=35%, 2-5=10%, 6-10=5%).
These alternatives factor in model diversity and ensemble contribution.

Usage in crunch_config.py:

    from crunch_node.extensions.emission_strategies import contribution_weighted_emission

    class CrunchConfig(BaseCrunchConfig):
        build_emission = contribution_weighted_emission
"""

from __future__ import annotations

from typing import Any

from crunch_node.crunch_config import (
    FRAC_64_MULTIPLIER,
    CruncherReward,
    EmissionCheckpoint,
    ProviderReward,
    pct_to_frac64,
)


def contribution_weighted_emission(
    ranked_entries: list[dict[str, Any]],
    crunch_pubkey: str,
    compute_provider: str | None = None,
    data_provider: str | None = None,
    *,
    rank_weight: float = 0.5,
    contribution_weight: float = 0.3,
    diversity_weight: float = 0.2,
    min_pct: float = 1.0,
) -> EmissionCheckpoint:
    """Emission that blends rank, contribution, and diversity.

    Three factors combined into a composite score per model:
    - **Rank component** (default 50%): inverse rank (1st gets most)
    - **Contribution component** (default 30%): ensemble contribution metric
    - **Diversity component** (default 20%): 1 - model_correlation (uniqueness)

    Models with negative contribution still get `min_pct` (configurable floor).
    Weights must sum to 1.0.

    Args:
        ranked_entries: list of dicts with 'rank', 'result_summary' keys
        crunch_pubkey: on-chain crunch address
        compute_provider: optional compute provider wallet
        data_provider: optional data provider wallet
        rank_weight: weight for rank-based component (0-1)
        contribution_weight: weight for contribution metric (0-1)
        diversity_weight: weight for diversity/uniqueness metric (0-1)
        min_pct: minimum reward percentage per model (floor)
    """
    if not ranked_entries:
        return EmissionCheckpoint(
            crunch=crunch_pubkey,
            cruncher_rewards=[],
            compute_provider_rewards=[],
            data_provider_rewards=[],
        )

    n = len(ranked_entries)

    # Extract metrics from result_summary
    contributions = []
    correlations = []
    for entry in ranked_entries:
        summary = entry.get("result_summary", {})
        contributions.append(float(summary.get("contribution", 0.0)))
        correlations.append(float(summary.get("model_correlation", 0.0)))

    # Normalize each component to [0, 1]
    def _normalize(values: list[float]) -> list[float]:
        mn, mx = min(values), max(values)
        if mx - mn < 1e-12:
            return [1.0 / n] * n
        return [(v - mn) / (mx - mn) for v in values]

    # Rank component: inverse rank (1st → highest)
    rank_scores = _normalize([1.0 / entry.get("rank", n) for entry in ranked_entries])

    # Contribution component: higher contribution → higher reward
    contribution_scores = _normalize(contributions)

    # Diversity component: lower correlation → higher reward
    diversity_scores = _normalize([1.0 - c for c in correlations])

    # Composite score
    composite = [
        rank_weight * rank_scores[i]
        + contribution_weight * contribution_scores[i]
        + diversity_weight * diversity_scores[i]
        for i in range(n)
    ]

    # Convert to percentages with floor
    total_composite = sum(composite)
    if total_composite < 1e-12:
        raw_pcts = [100.0 / n] * n
    else:
        raw_pcts = [max(min_pct, (c / total_composite) * 100.0) for c in composite]

    # Re-normalize to sum to exactly 100%
    pct_sum = sum(raw_pcts)
    raw_pcts = [p / pct_sum * 100.0 for p in raw_pcts]

    # Convert to frac64
    frac64_values = [pct_to_frac64(p) for p in raw_pcts]
    if frac64_values:
        diff = FRAC_64_MULTIPLIER - sum(frac64_values)
        frac64_values[0] += diff

    cruncher_rewards = [
        CruncherReward(cruncher_index=i, reward_pct=frac64_values[i]) for i in range(n)
    ]

    compute_rewards = (
        [ProviderReward(provider=compute_provider, reward_pct=FRAC_64_MULTIPLIER)]
        if compute_provider
        else []
    )
    data_rewards = (
        [ProviderReward(provider=data_provider, reward_pct=FRAC_64_MULTIPLIER)]
        if data_provider
        else []
    )

    return EmissionCheckpoint(
        crunch=crunch_pubkey,
        cruncher_rewards=cruncher_rewards,
        compute_provider_rewards=compute_rewards,
        data_provider_rewards=data_rewards,
    )

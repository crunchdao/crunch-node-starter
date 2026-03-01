"""Compare benchmark results across runs."""

from __future__ import annotations

import json
import os
import sys

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


def load_results() -> list[dict]:
    """Load all result JSON files, sorted by timestamp."""
    results = []
    for fname in sorted(os.listdir(RESULTS_DIR)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(RESULTS_DIR, fname)
        with open(path) as f:
            data = json.load(f)
            data["_filename"] = fname
            results.append(data)
    return results


def milestone_count(result: dict) -> tuple[int, int]:
    """Return (passed, total) milestone counts."""
    milestones = result.get("milestones", {})
    total = len(milestones)
    passed = sum(1 for m in milestones.values() if m.get("passed"))
    return passed, total


def _fmt_tokens(n: int) -> str:
    """Format token count for display (e.g. 1234567 → 1.23M, 12345 → 12.3k)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _fmt_cost(c: float) -> str:
    """Format cost in USD."""
    if c <= 0:
        return "—"
    return f"${c:.4f}"


def _print_token_insights(result: dict, label: str = "") -> None:
    """Print token usage insights for a single result."""
    tokens = result.get("tokens", {})
    if not tokens:
        return

    prefix = f"  {label}" if label else "  "
    total = tokens.get("total", 0)
    inp = tokens.get("input", 0)
    out = tokens.get("output", 0)
    cache_r = tokens.get("cache_read", 0)
    cache_w = tokens.get("cache_write", 0)
    cache_pct = tokens.get("cache_hit_rate_pct", 0)
    cost = result.get("cost_usd", 0)
    turns = result.get("turns", 0)
    tool_calls_count = result.get("tool_calls", 0)
    compactions = result.get("compaction_count", 0)
    efficiency = result.get("efficiency", {})

    print(
        f"{prefix}Tokens:  {_fmt_tokens(total)} total "
        f"({_fmt_tokens(inp)} in, {_fmt_tokens(out)} out, "
        f"{_fmt_tokens(cache_r)} cache-read, {_fmt_tokens(cache_w)} cache-write)"
    )
    print(
        f"{prefix}Cost:    {_fmt_cost(cost)}  |  "
        f"Cache hit: {cache_pct}%  |  Compactions: {compactions}"
    )
    print(
        f"{prefix}Turns:   {turns}  |  "
        f"Tool calls: {tool_calls_count}  |  "
        f"Tokens/turn: {efficiency.get('tokens_per_turn', '?')}  |  "
        f"Tools/turn: {efficiency.get('tool_calls_per_turn', '?')}"
    )


def compare_last_two() -> int:
    """Compare the two most recent results. Returns exit code."""
    results = load_results()

    if len(results) == 0:
        print("No benchmark results found.")
        return 1

    if len(results) == 1:
        current = results[0]
        c_passed, c_total = milestone_count(current)
        print("Only one benchmark result found — no comparison available.\n")
        print(f"  Run:        {current['_filename']}")
        print(f"  Agent:      {current.get('agent_cmd', '?')}")
        print(f"  Milestones: {c_passed}/{c_total}")
        print(f"  Duration:   {current.get('duration_seconds', '?')}s")
        print(f"  Timed out:  {current.get('timed_out', '?')}")
        print()
        _print_token_insights(current)
        return 0

    previous = results[-2]
    current = results[-1]

    p_passed, p_total = milestone_count(previous)
    c_passed, c_total = milestone_count(current)

    milestone_delta = c_passed - p_passed
    duration_delta = (current.get("duration_seconds", 0) or 0) - (
        previous.get("duration_seconds", 0) or 0
    )

    print("=" * 72)
    print("  BENCHMARK COMPARISON")
    print("=" * 72)
    print()
    print(
        f"  Previous: {previous['_filename']:<30} "
        f"{p_passed}/{p_total} milestones  "
        f"{previous.get('duration_seconds', '?')}s"
    )
    print(
        f"  Current:  {current['_filename']:<30} "
        f"{c_passed}/{c_total} milestones  "
        f"{current.get('duration_seconds', '?')}s"
    )

    # Delta
    m_sign = "+" if milestone_delta >= 0 else ""
    d_sign = "+" if duration_delta >= 0 else ""
    # For duration, less is better
    d_indicator = "⬇️" if duration_delta < 0 else ("⬆️" if duration_delta > 0 else "")

    overall = "✅" if milestone_delta >= 0 and duration_delta <= 0 else ""
    if milestone_delta < 0:
        overall = "🔴 REGRESSION"

    print(
        f"  Delta:    {m_sign}{milestone_delta} milestones, "
        f"{d_sign}{duration_delta:.0f}s {d_indicator}  {overall}"
    )

    # Per-milestone diff
    prev_milestones = previous.get("milestones", {})
    curr_milestones = current.get("milestones", {})
    all_keys = list(
        dict.fromkeys(list(prev_milestones.keys()) + list(curr_milestones.keys()))
    )

    regressions = []
    improvements = []

    print()
    print(f"  {'Milestone':<25} {'Previous':<10} {'Current':<10} {'Change'}")
    print("  " + "-" * 60)

    for key in all_keys:
        prev_pass = prev_milestones.get(key, {}).get("passed")
        curr_pass = curr_milestones.get(key, {}).get("passed")

        prev_str = "✅" if prev_pass else "❌" if prev_pass is not None else "—"
        curr_str = "✅" if curr_pass else "❌" if curr_pass is not None else "—"

        if prev_pass and not curr_pass:
            change = "🔴 REGRESSED"
            regressions.append(key)
        elif not prev_pass and curr_pass:
            change = "🟢 IMPROVED"
            improvements.append(key)
        else:
            change = ""

        print(f"  {key:<25} {prev_str:<10} {curr_str:<10} {change}")

    print()
    if regressions:
        print(f"  Regressions: {', '.join(regressions)}")
    else:
        print("  Regressions: none")
    if improvements:
        print(f"  Improvements: {', '.join(improvements)}")

    # --- Token / efficiency comparison ---
    curr_tokens = current.get("tokens", {})
    prev_tokens = previous.get("tokens", {})

    if curr_tokens or prev_tokens:
        print()
        print("  " + "-" * 60)
        print("  TOKEN USAGE & EFFICIENCY")
        print("  " + "-" * 60)

        metrics = [
            ("Total tokens", "total", _fmt_tokens),
            ("Input tokens", "input", _fmt_tokens),
            ("Output tokens", "output", _fmt_tokens),
            ("Cache read", "cache_read", _fmt_tokens),
            ("Cache hit %", "cache_hit_rate_pct", lambda x: f"{x}%"),
        ]

        for label, key, fmt in metrics:
            p_val = prev_tokens.get(key, 0)
            c_val = curr_tokens.get(key, 0)
            delta = c_val - p_val
            if key == "cache_hit_rate_pct":
                delta_str = f"{delta:+.1f}pp"
            elif isinstance(c_val, float):
                delta_str = f"{delta:+.1f}"
            else:
                delta_str = _fmt_tokens(abs(delta))
                delta_str = f"+{delta_str}" if delta > 0 else f"-{delta_str}"
            print(f"  {label:<20} {fmt(p_val):>10} → {fmt(c_val):>10}  ({delta_str})")

        # Cost
        p_cost = previous.get("cost_usd", 0)
        c_cost = current.get("cost_usd", 0)
        cost_delta = c_cost - p_cost
        print(
            f"  {'Cost':<20} {_fmt_cost(p_cost):>10} → "
            f"{_fmt_cost(c_cost):>10}  ({_fmt_cost(abs(cost_delta))})"
        )

        # Turns / tool calls
        p_turns = previous.get("turns", 0)
        c_turns = current.get("turns", 0)
        p_tools = previous.get("tool_calls", 0)
        c_tools = current.get("tool_calls", 0)
        print(
            f"  {'Turns':<20} {p_turns:>10} → {c_turns:>10}  ({c_turns - p_turns:+d})"
        )
        print(
            f"  {'Tool calls':<20} {p_tools:>10} → {c_tools:>10}  "
            f"({c_tools - p_tools:+d})"
        )

        # Compactions
        p_comp = previous.get("compaction_count", 0)
        c_comp = current.get("compaction_count", 0)
        print(
            f"  {'Compactions':<20} {p_comp:>10} → {c_comp:>10}  ({c_comp - p_comp:+d})"
        )

    print()
    print("=" * 72)

    return 1 if regressions else 0


def main() -> int:
    return compare_last_two()


if __name__ == "__main__":
    sys.exit(main())

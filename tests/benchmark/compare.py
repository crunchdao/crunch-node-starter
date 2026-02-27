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
        return 0

    previous = results[-2]
    current = results[-1]

    p_passed, p_total = milestone_count(previous)
    c_passed, c_total = milestone_count(current)

    milestone_delta = c_passed - p_passed
    duration_delta = (current.get("duration_seconds", 0) or 0) - (
        previous.get("duration_seconds", 0) or 0
    )

    print("=" * 60)
    print("  BENCHMARK COMPARISON")
    print("=" * 60)
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
    print()
    print("=" * 60)

    return 1 if regressions else 0


def main() -> int:
    return compare_last_two()


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import os
import subprocess
import time
from datetime import UTC, datetime, timedelta

import requests

_MODEL_FAILURE_MARKERS = [
    "BAD_IMPLEMENTATION",
    "No Inherited class found",
    "Import error occurred",
    "BuilderStatus.FAILURE",
    "ModuleNotFoundError",
    "SyntaxError",
]


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _get_json(base_url: str, path: str, params: dict | None = None):
    response = requests.get(f"{base_url}{path}", params=params, timeout=5)
    response.raise_for_status()
    return response.json()


def _detect_model_runner_failure(log_text: str) -> str | None:
    for line in log_text.splitlines():
        if any(marker in line for marker in _MODEL_FAILURE_MARKERS):
            return line.strip()
    return None


def _read_model_orchestrator_logs() -> str:
    cmd = [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "--env-file",
        ".local.env",
        "logs",
        "model-orchestrator",
        "--tail",
        "500",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return (result.stdout or "") + "\n" + (result.stderr or "")


def _print_model_failure_context(log_text: str) -> None:
    print()
    print("[verify-e2e] === MODEL ORCHESTRATOR LOGS (last 30 lines) ===")
    for line in log_text.splitlines()[-30:]:
        print(f"  {line}")
    print()


def check_score_quality(scored: list[dict]) -> tuple[bool, str]:
    """Check if scored predictions indicate a real scoring function.

    Returns (passed, reason). Fails on all-zero or all-identical scores,
    which indicate a stub scorer or broken ground truth.
    """
    if not scored:
        return False, "no scored predictions"

    score_values = [row["score_value"] for row in scored]

    if all(v == 0.0 for v in score_values):
        return False, (
            "All scores are 0.0 — scoring function may be a stub "
            "or ground truth resolver returns zero."
        )

    if len(set(score_values)) <= 1:
        return False, (
            f"All scores are identical ({score_values[0]}) — scoring function "
            f"may be a stub returning a constant value."
        )

    return True, "ok"


def _print_summary(
    base_url: str,
    models: list[dict],
    scored: list[dict],
    leaderboard: list[dict],
    since: datetime,
) -> None:
    """Print a detailed summary for agent review."""
    now = datetime.now(UTC)

    print()
    print("=" * 70)
    print("  VERIFICATION SUMMARY")
    print("=" * 70)

    # Leaderboard
    print()
    print("  LEADERBOARD:")
    print(f"  {'Rank':<6} {'Model':<35} {'Score':<12}")
    print("  " + "-" * 55)
    for entry in leaderboard:
        rank = entry.get("rank", "?")
        model_id = entry.get("model_id", "?")
        score_val = entry.get("score_ranking", {}).get("value")
        score_str = f"{score_val:.6f}" if score_val is not None else "null"
        print(f"  #{rank:<5} {model_id:<35} {score_str}")

    # Per-model score breakdown
    by_model: dict[str, list[float]] = {}
    for p in scored:
        mid = p.get("model_id", "?")
        by_model.setdefault(mid, []).append(p["score_value"])

    print()
    print("  SCORES PER MODEL:")
    print(f"  {'Model':<35} {'Count':<7} {'Mean':<12} {'Min':<12} {'Max':<12}")
    print("  " + "-" * 78)
    for mid, vals in sorted(by_model.items()):
        mean = sum(vals) / len(vals)
        print(
            f"  {mid:<35} {len(vals):<7} {mean:<12.6f} {min(vals):<12.6f} {max(vals):<12.6f}"
        )

    # Feed data
    try:
        feeds_tail = _get_json(base_url, "/reports/feeds/tail")
        if feeds_tail:
            print()
            print("  LATEST FEED DATA:")
            for rec in feeds_tail[:3]:
                source = rec.get("source", "?")
                subject = rec.get("subject", "?")
                ts = rec.get("received_at") or rec.get("ts", "?")
                print(f"    {source}/{subject}  received_at={ts}")
    except Exception:
        pass

    # Failed predictions
    failed = [p for p in scored if p.get("score_failed")]
    if failed:
        print()
        print(f"  FAILED PREDICTIONS: {len(failed)}")
        for p in failed[-3:]:
            print(
                f"    model={p.get('model_id')} reason={p.get('score_failed_reason', '?')}"
            )

    print()
    print("=" * 70)


def main() -> int:
    port = os.getenv("REPORT_API_PORT", "8000")
    base_url = os.getenv("REPORT_API_URL", f"http://localhost:{port}")
    timeout_seconds = int(os.getenv("E2E_VERIFY_TIMEOUT_SECONDS", "240"))
    poll_seconds = int(os.getenv("E2E_VERIFY_POLL_SECONDS", "5"))

    print(f"[verify-e2e] base_url={base_url} timeout={timeout_seconds}s")

    deadline = time.time() + timeout_seconds
    since = datetime.now(UTC) - timedelta(hours=1)

    last_error: str | None = None

    while time.time() < deadline:
        try:
            # Check for model errors (non-fatal — some models may fail
            # while others run fine; only matters if zero models work)
            model_logs = _read_model_orchestrator_logs()
            failure = _detect_model_runner_failure(model_logs)
            if failure is not None:
                print(
                    f"[verify-e2e] ⚠️  model error in logs (non-fatal): {failure[:120]}"
                )

            health = _get_json(base_url, "/healthz")
            if health.get("status") != "ok":
                raise RuntimeError(f"healthcheck not ok: {health}")

            models = _get_json(base_url, "/reports/models")
            if not models:
                raise RuntimeError("no models registered yet")

            model_id = models[0]["model_id"]
            now = datetime.now(UTC)
            predictions = _get_json(
                base_url,
                "/reports/predictions",
                params={
                    "projectIds": model_id,
                    "start": _iso(since),
                    "end": _iso(now),
                },
            )
            leaderboard = _get_json(base_url, "/reports/leaderboard")

            scored = [
                row
                for row in predictions
                if row.get("score_value") is not None
                and row.get("score_failed") is False
            ]
            if scored and leaderboard:
                quality_ok, quality_reason = check_score_quality(scored)

                print(
                    "[verify-e2e] success "
                    f"models={len(models)} scored_predictions={len(scored)} leaderboard_entries={len(leaderboard)}"
                )
                if not quality_ok:
                    print(f"[verify-e2e] FAILED: {quality_reason}")
                    _print_summary(base_url, models, scored, leaderboard, since)
                    return 1
                _print_summary(base_url, models, scored, leaderboard, since)
                return 0

            raise RuntimeError(
                f"waiting for scored predictions/leaderboard (predictions={len(predictions)} leaderboard={len(leaderboard)})"
            )
        except RuntimeError as exc:
            last_error = str(exc)
            print(f"[verify-e2e] waiting: {last_error}")
            time.sleep(poll_seconds)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            print(f"[verify-e2e] waiting: {last_error}")
            time.sleep(poll_seconds)

    print(f"[verify-e2e] FAILED: timeout reached. last_error={last_error}")
    model_logs = _read_model_orchestrator_logs()
    _print_model_failure_context(model_logs)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

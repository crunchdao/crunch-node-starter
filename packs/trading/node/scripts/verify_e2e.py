"""E2E verification for trading packs.

Trading packs don't use a scoring function — PnL is computed by the
TradingEngine and written as snapshots. This verifier checks:

1. Models are registered and producing predictions
2. Trading snapshots exist (score worker is running _score_trading)
3. Leaderboard is populated
"""

from __future__ import annotations

import os
import subprocess
import sys
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


def _print_summary(
    models: list[dict],
    snapshots: list[dict],
    leaderboard: list[dict],
) -> None:
    print()
    print("=" * 70)
    print("  VERIFICATION SUMMARY (TRADING)")
    print("=" * 70)

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

    print()
    print("  TRADING SNAPSHOTS:")
    print(f"  {'Model':<35} {'PnL':<12} {'Positions':<10} {'Time'}")
    print("  " + "-" * 75)
    for snap in snapshots[-10:]:
        model_id = snap.get("model_id", "?")
        summary = snap.get("result_summary", {})
        net_pnl = summary.get("net_pnl", 0.0)
        positions = summary.get("open_position_count", 0)
        ts = snap.get("created_at", "?")
        print(f"  {model_id:<35} {net_pnl:<12.4f} {positions:<10} {ts}")

    print()
    print("=" * 70)


def main() -> int:
    port = os.getenv("REPORT_API_PORT", "8000")
    base_url = os.getenv("REPORT_API_URL", f"http://localhost:{port}")
    timeout_seconds = int(os.getenv("E2E_VERIFY_TIMEOUT_SECONDS", "240"))
    poll_seconds = int(os.getenv("E2E_VERIFY_POLL_SECONDS", "5"))

    print(f"[verify-e2e] base_url={base_url} timeout={timeout_seconds}s")
    print("[verify-e2e] Trading mode — checking for snapshots instead of scores")

    deadline = time.time() + timeout_seconds
    since = datetime.now(UTC) - timedelta(hours=1)

    last_error: str | None = None

    while time.time() < deadline:
        try:
            model_logs = _read_model_orchestrator_logs()
            failure = _detect_model_runner_failure(model_logs)
            if failure is not None:
                print(
                    f"[verify-e2e] \u26a0\ufe0f  model error in logs (non-fatal): {failure[:120]}"
                )

            health = _get_json(base_url, "/healthz")
            if health.get("status") != "ok":
                raise RuntimeError(f"healthcheck not ok: {health}")

            models = _get_json(base_url, "/reports/models")
            if not models:
                raise RuntimeError("no models registered yet")

            now = datetime.now(UTC)
            all_model_ids = ",".join(m["model_id"] for m in models)
            predictions = _get_json(
                base_url,
                "/reports/predictions",
                params={
                    "projectIds": all_model_ids,
                    "start": _iso(since),
                    "end": _iso(now),
                },
            )

            if len(predictions) < 2:
                raise RuntimeError(
                    f"waiting for predictions ({len(predictions)} so far)"
                )

            snapshots = _get_json(base_url, "/reports/snapshots")
            leaderboard = _get_json(base_url, "/reports/leaderboard")

            if snapshots and leaderboard:
                print(
                    "[verify-e2e] success "
                    f"models={len(models)} predictions={len(predictions)} "
                    f"snapshots={len(snapshots)} leaderboard={len(leaderboard)}"
                )
                _print_summary(models, snapshots, leaderboard)
                return 0

            raise RuntimeError(
                f"waiting for snapshots/leaderboard "
                f"(predictions={len(predictions)} snapshots={len(snapshots)} "
                f"leaderboard={len(leaderboard)})"
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

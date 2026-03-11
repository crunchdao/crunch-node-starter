"""E2E verification — supports both realtime and tournament modes.

Realtime mode: polls for scored predictions that arrive automatically.
Tournament mode: triggers a round via the tournament API using test data,
then verifies predictions and scores propagated to the leaderboard.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta, timezone

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


def _post_json(base_url: str, path: str, body: dict):
    response = requests.post(f"{base_url}{path}", json=body, timeout=60)
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
    """Check if scored predictions indicate a real scoring function."""
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


# ── Tournament mode ──


def _is_tournament_mode() -> bool:
    """Detect tournament mode from CrunchConfig.predict_service_class."""
    try:
        node_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        challenge_dir = os.path.join(os.path.dirname(node_dir), "challenge")
        for p in (node_dir, challenge_dir):
            if p not in sys.path:
                sys.path.insert(0, p)
        from config.crunch_config import CrunchConfig

        config = CrunchConfig()
        svc_class = getattr(config, "predict_service_class", None)
        if svc_class is None:
            return False
        return "Tournament" in svc_class.__name__
    except Exception:
        return False


def _find_test_data() -> list[dict] | None:
    """Find test data for tournament verification.

    Searches for in_sample.json or out_of_sample.json in the challenge data dir.
    Falls back to a minimal synthetic dataset if nothing found.
    """
    node_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base_dir = os.path.dirname(node_dir)

    # Search common locations
    search_paths = [
        os.path.join(
            base_dir, "challenge", "starter_challenge", "data", "out_of_sample.json"
        ),
        os.path.join(
            base_dir, "challenge", "starter_challenge", "data", "in_sample.json"
        ),
    ]

    for path in search_paths:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                if isinstance(data, list) and len(data) > 0:
                    print(
                        f"[verify-e2e] Using test data from {os.path.basename(path)} ({len(data)} records)"
                    )
                    return data
            except Exception:
                continue

    return None


def _run_tournament_round(base_url: str) -> tuple[bool, str]:
    """Trigger a tournament round: inference then scoring.

    Loads test data, strips ground truth for inference, then scores
    with the actual values.
    """
    test_data = _find_test_data()
    if not test_data:
        return False, "no test data found for tournament verification"

    round_id = f"verify-e2e-{int(time.time())}"

    # Split features from ground truth
    # Ground truth field detection: look for 'price' or any field not in
    # the features that the model receives. Simple heuristic: if the
    # CrunchConfig ground_truth_type has specific fields, use those.
    gt_fields = _detect_gt_fields()

    features = []
    ground_truth_list = []
    for row in test_data:
        feature_row = {k: v for k, v in row.items() if k not in gt_fields}
        gt_row = {k: v for k, v in row.items() if k in gt_fields}
        features.append(feature_row)
        ground_truth_list.append(gt_row)

    print(f"[verify-e2e] Tournament round {round_id}: {len(features)} records")

    # Step 1: Inference
    try:
        inf_result = _post_json(
            base_url,
            f"/tournament/rounds/{round_id}/inference",
            {"features": features},
        )
        model_count = inf_result.get("model_count", 0)
        pred_count = inf_result.get("prediction_count", 0)
        print(f"[verify-e2e] Inference: {model_count} models, {pred_count} predictions")

        if pred_count == 0:
            return False, "inference returned 0 predictions — no models registered?"
    except requests.HTTPError as exc:
        return (
            False,
            f"inference HTTP error: {exc.response.status_code} {exc.response.text[:200]}",
        )
    except Exception as exc:
        return False, f"inference failed: {exc}"

    # Step 2: Scoring
    try:
        score_result = _post_json(
            base_url,
            f"/tournament/rounds/{round_id}/score",
            {"ground_truth": ground_truth_list},
        )
        scores_count = score_result.get("scores_count", 0)
        print(f"[verify-e2e] Scoring: {scores_count} scores")

        results = score_result.get("results", [])
        for r in results:
            score_val = r.get("score", 0.0)
            success = r.get("success", False)
            pred_id = r.get("prediction_id", "?")
            status = "✅" if success else "❌"
            print(f"  {status} {pred_id}: score={score_val:.4f}")

            if not success:
                return (
                    False,
                    f"scoring failed for {pred_id}: {r.get('result', {}).get('failed_reason', '?')}",
                )

        if scores_count == 0:
            return False, "scoring returned 0 scores"

    except requests.HTTPError as exc:
        return (
            False,
            f"scoring HTTP error: {exc.response.status_code} {exc.response.text[:200]}",
        )
    except Exception as exc:
        return False, f"scoring failed: {exc}"

    return True, f"round {round_id}: {pred_count} predictions, {scores_count} scores"


def _detect_gt_fields() -> set[str]:
    """Detect ground truth field names from CrunchConfig.ground_truth_type."""
    try:
        node_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        challenge_dir = os.path.join(os.path.dirname(node_dir), "challenge")
        for p in (node_dir, challenge_dir):
            if p not in sys.path:
                sys.path.insert(0, p)
        from config.crunch_config import CrunchConfig

        config = CrunchConfig()
        gt_type = config.ground_truth_type
        return set(gt_type.model_fields.keys())
    except Exception:
        # Fallback — common ground truth field names
        return {"price", "actual_price", "target", "label"}


# ── Summary printing ──


def _print_summary(
    base_url: str,
    models: list[dict],
    scored: list[dict],
    leaderboard: list[dict],
    since: datetime,
) -> None:
    print()
    print("=" * 70)
    print("  VERIFICATION SUMMARY")
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


# ── Main ──


def main() -> int:
    port = os.getenv("REPORT_API_PORT", "8000")
    base_url = os.getenv("REPORT_API_URL", f"http://localhost:{port}")
    timeout_seconds = int(os.getenv("E2E_VERIFY_TIMEOUT_SECONDS", "240"))
    poll_seconds = int(os.getenv("E2E_VERIFY_POLL_SECONDS", "5"))

    print(f"[verify-e2e] base_url={base_url} timeout={timeout_seconds}s")

    tournament_mode = _is_tournament_mode()
    if tournament_mode:
        print("[verify-e2e] Tournament mode detected — will trigger rounds via API")

    deadline = time.time() + timeout_seconds
    since = datetime.now(UTC) - timedelta(hours=1)

    last_error: str | None = None
    tournament_round_triggered = False

    while time.time() < deadline:
        try:
            # Check for model build errors (non-fatal)
            model_logs = _read_model_orchestrator_logs()
            failure = _detect_model_runner_failure(model_logs)
            if failure is not None:
                print(
                    f"[verify-e2e] ⚠️  model error in logs (non-fatal): {failure[:120]}"
                )

            health = _get_json(base_url, "/healthz")
            if health.get("status") != "ok":
                raise RuntimeError(f"healthcheck not ok: {health}")

            # Tournament mode: trigger a round without waiting for models.
            # Models get registered in the DB only when inference runs,
            # so we can't gate on /reports/models first.
            if tournament_mode and not tournament_round_triggered:
                print("[verify-e2e] Triggering tournament round...")
                ok, reason = _run_tournament_round(base_url)
                if not ok:
                    raise RuntimeError(f"tournament round failed: {reason}")
                tournament_round_triggered = True
                print(f"[verify-e2e] Tournament round complete: {reason}")

            models = _get_json(base_url, "/reports/models")
            if not models:
                raise RuntimeError("no models registered yet")

            # Check for scored predictions + leaderboard across ALL models
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
            leaderboard = _get_json(base_url, "/reports/leaderboard")

            scored = [
                row
                for row in predictions
                if row.get("score_value") is not None
                and row.get("score_failed") is False
            ]
            # Need ≥2 scored predictions to check score diversity
            if len(scored) >= 2 and leaderboard:
                quality_ok, quality_reason = check_score_quality(scored)

                print(
                    "[verify-e2e] success "
                    f"models={len(models)} scored_predictions={len(scored)} "
                    f"leaderboard_entries={len(leaderboard)}"
                )
                if not quality_ok:
                    print(f"[verify-e2e] FAILED: {quality_reason}")
                    _print_summary(base_url, models, scored, leaderboard, since)
                    return 1
                _print_summary(base_url, models, scored, leaderboard, since)
                return 0

            raise RuntimeError(
                f"waiting for scored predictions/leaderboard "
                f"(predictions={len(predictions)} scored={len(scored)} "
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

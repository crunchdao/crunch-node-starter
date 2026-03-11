"""E2E verification — tournament mode.

Tournament competitions don't have streaming feeds or automatic predictions.
This script drives the full pipeline by:

1. Waiting for the report worker to be healthy
2. Triggering a tournament round via the API (inference + scoring)
3. Verifying predictions and scores propagated to the leaderboard
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

NODE_DIR = Path(__file__).resolve().parent.parent
CHALLENGE_DIR = NODE_DIR.parent / "challenge"


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


# ── Test data ────────────────────────────────────────────────────────


def _find_test_data() -> list[dict] | None:
    """Find out_of_sample.json or in_sample.json for tournament verification."""
    search_paths = [
        CHALLENGE_DIR / "starter_challenge" / "data" / "out_of_sample.json",
        CHALLENGE_DIR / "starter_challenge" / "data" / "in_sample.json",
    ]

    for path in search_paths:
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if isinstance(data, list) and len(data) > 0:
                    print(
                        f"[verify-e2e] Using test data: {path.name} ({len(data)} records)"
                    )
                    return data
            except Exception:
                continue

    return None


def _detect_gt_fields() -> set[str]:
    """Detect ground truth field names from CrunchConfig.ground_truth_type."""
    try:
        sys.path.insert(0, str(NODE_DIR))
        sys.path.insert(0, str(CHALLENGE_DIR))
        from config.crunch_config import CrunchConfig

        config = CrunchConfig()
        return set(config.ground_truth_type.model_fields.keys())
    except Exception:
        return {"price"}


# ── Tournament round ─────────────────────────────────────────────────


def _run_tournament_round(
    base_url: str,
) -> tuple[bool, str]:
    """Trigger inference + scoring for one round."""
    test_data = _find_test_data()
    if not test_data:
        return False, "no test data found (need out_of_sample.json or in_sample.json)"

    round_id = f"verify-e2e-{int(time.time())}"
    gt_fields = _detect_gt_fields()

    features = []
    ground_truth_list = []
    for row in test_data:
        feature_row = {k: v for k, v in row.items() if k not in gt_fields}
        gt_row = {k: v for k, v in row.items() if k in gt_fields}
        features.append(feature_row)
        ground_truth_list.append(gt_row)

    print(f"[verify-e2e] Round {round_id}: {len(features)} records")

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
            return False, "inference returned 0 predictions"
    except requests.HTTPError as exc:
        return (
            False,
            f"inference HTTP {exc.response.status_code}: {exc.response.text[:200]}",
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
                reason = r.get("result", {}).get("failed_reason", "?")
                return False, f"scoring failed for {pred_id}: {reason}"

        if scores_count == 0:
            return False, "scoring returned 0 scores"

    except requests.HTTPError as exc:
        return (
            False,
            f"scoring HTTP {exc.response.status_code}: {exc.response.text[:200]}",
        )
    except Exception as exc:
        return False, f"scoring failed: {exc}"

    return True, f"{pred_count} predictions, {scores_count} scores"


# ── Summary ──────────────────────────────────────────────────────────


def _print_summary(
    models: list[dict],
    scored: list[dict],
    leaderboard: list[dict],
) -> None:
    print()
    print("=" * 70)
    print("  VERIFICATION SUMMARY (tournament)")
    print("=" * 70)

    if leaderboard:
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

    if scored:
        by_model: dict[str, list[float]] = {}
        for p in scored:
            mid = p.get("model_id", "?")
            by_model.setdefault(mid, []).append(p.get("score_value", 0.0))

        print()
        print("  SCORES PER MODEL:")
        print(f"  {'Model':<35} {'Count':<7} {'Mean':<12} {'Min':<12} {'Max':<12}")
        print("  " + "-" * 78)
        for mid, vals in sorted(by_model.items()):
            mean = sum(vals) / len(vals)
            print(
                f"  {mid:<35} {len(vals):<7} {mean:<12.6f} {min(vals):<12.6f} {max(vals):<12.6f}"
            )

    print()
    print("=" * 70)


# ── Main ─────────────────────────────────────────────────────────────


def _wait_for_models(base_url: str, deadline: float, poll: int = 5) -> bool:
    """Wait until the model orchestrator reports at least one model."""
    orchestrator_url = base_url.replace(":8000", ":8001")
    while time.time() < deadline:
        try:
            resp = requests.get(f"{orchestrator_url}/models", timeout=5)
            if resp.ok:
                models = resp.json()
                running = [
                    m
                    for m in models
                    if m.get("status", "").upper() in ("RUNNING", "READY")
                ]
                if running:
                    print(
                        f"[verify-e2e] {len(running)} model(s) ready "
                        f"({', '.join(m.get('name', '?') for m in running)})"
                    )
                    return True
                # Models exist but not running yet — wait
                building = [
                    m
                    for m in models
                    if m.get("status", "").upper() in ("BUILDING", "PENDING", "CREATED")
                ]
                if building:
                    print(f"[verify-e2e] {len(building)} model(s) building, waiting...")
                elif models:
                    print(
                        f"[verify-e2e] {len(models)} model(s) found, waiting for RUNNING..."
                    )
        except Exception as exc:
            print(f"[verify-e2e] waiting for orchestrator: {exc}")
        time.sleep(poll)
    return False


def main() -> int:
    port = os.getenv("REPORT_API_PORT", "8000")
    base_url = os.getenv("REPORT_API_URL", f"http://localhost:{port}")
    timeout_seconds = int(os.getenv("E2E_VERIFY_TIMEOUT_SECONDS", "180"))
    poll_seconds = int(os.getenv("E2E_VERIFY_POLL_SECONDS", "5"))

    print(
        f"[verify-e2e] tournament mode — base_url={base_url} timeout={timeout_seconds}s"
    )

    deadline = time.time() + timeout_seconds
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    round_triggered = False
    last_error: str | None = None

    # Phase 1: wait for report worker to be healthy
    while time.time() < deadline:
        try:
            health = _get_json(base_url, "/healthz")
            if health.get("status") == "ok":
                print("[verify-e2e] report worker healthy")
                break
        except Exception as exc:
            print(f"[verify-e2e] waiting for report worker: {exc}")
        time.sleep(poll_seconds)
    else:
        print("[verify-e2e] FAILED: report worker not healthy within timeout")
        return 1

    # Phase 2: wait for models to be ready
    if not _wait_for_models(base_url, deadline, poll_seconds):
        print("[verify-e2e] FAILED: no models ready within timeout")
        return 1

    while time.time() < deadline:
        try:
            # Trigger one tournament round (only once)
            if not round_triggered:
                ok, reason = _run_tournament_round(base_url)
                if not ok:
                    raise RuntimeError(f"tournament round failed: {reason}")
                round_triggered = True
                print(f"[verify-e2e] Round complete: {reason}")

            # Check that predictions + scores + leaderboard propagated
            models = _get_json(base_url, "/reports/models")
            leaderboard = _get_json(base_url, "/reports/leaderboard")

            if not models:
                raise RuntimeError("no models in report DB after tournament round")

            model_id = models[0]["model_id"]
            now = datetime.now(timezone.utc)
            predictions = _get_json(
                base_url,
                "/reports/predictions",
                params={
                    "projectIds": model_id,
                    "start": _iso(since),
                    "end": _iso(now),
                },
            )

            scored = [
                row
                for row in predictions
                if row.get("score_value") is not None
                and row.get("score_failed") is False
            ]

            if scored and leaderboard:
                print(
                    f"[verify-e2e] success: "
                    f"models={len(models)} scored={len(scored)} "
                    f"leaderboard={len(leaderboard)}"
                )
                _print_summary(models, scored, leaderboard)
                return 0

            raise RuntimeError(
                f"waiting for data propagation "
                f"(models={len(models)} scored={len(scored)} "
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
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

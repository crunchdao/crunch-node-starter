"""Generate a verification report for LLM review.

Collects container status, API responses, feed data, docker logs,
and UI reachability into a single markdown file. Deterministic —
no assertions, no pass/fail. The LLM reads the output and judges
whether the system is working correctly for the competition.

Usage:
    python scripts/generate_report.py [--output report.md]
    make report
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from io import StringIO

import requests

# ── Config ───────────────────────────────────────────────────────────

API_PORT = os.getenv("REPORT_API_PORT", "8000")
UI_PORT = os.getenv("REPORT_UI_PORT", "3000")
API_URL = os.getenv("REPORT_API_URL", f"http://localhost:{API_PORT}")
UI_URL = os.getenv("REPORT_UI_URL", f"http://localhost:{UI_PORT}")
COMPOSE_CMD = [
    "docker",
    "compose",
    "-f",
    "docker-compose.yml",
    "--env-file",
    ".local.env",
]

WORKERS = [
    "feed-data-worker",
    "predict-worker",
    "score-worker",
    "report-worker",
    "model-orchestrator",
]

API_ENDPOINTS = [
    ("/healthz", "Health check"),
    ("/info", "Node identity"),
    ("/reports/models", "Registered models"),
    ("/reports/leaderboard", "Leaderboard"),
    ("/reports/predictions", "Predictions (last hour)"),
    ("/reports/feeds", "Feed subscriptions"),
    ("/reports/feeds/tail", "Latest feed records"),
    ("/reports/snapshots", "Snapshots"),
    ("/reports/checkpoints", "Checkpoints"),
    ("/reports/diversity", "Model diversity"),
    ("/reports/schema/leaderboard-columns", "Leaderboard columns"),
]

LOG_ERROR_PATTERNS = [
    "error",
    "exception",
    "traceback",
    "failed",
    "RuntimeError",
    "KeyError",
    "BAD_IMPLEMENTATION",
    "VALIDATION_ERROR",
]

LOG_TAIL_LINES = 100


# ── Helpers ──────────────────────────────────────────────────────────


def _json_compact(data, max_items: int = 10) -> str:
    """Pretty-print JSON, truncating arrays to max_items."""
    if isinstance(data, list) and len(data) > max_items:
        truncated = data[:max_items]
        suffix = f"\n... ({len(data) - max_items} more items, {len(data)} total)"
        return json.dumps(truncated, indent=2, default=str) + suffix
    return json.dumps(data, indent=2, default=str)


def _get(path: str, params: dict | None = None) -> tuple[int, object]:
    """GET an API endpoint. Returns (status_code, json_or_error_string)."""
    try:
        resp = requests.get(f"{API_URL}{path}", params=params, timeout=10)
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:500]
        return resp.status_code, body
    except requests.ConnectionError:
        return 0, "connection refused"
    except Exception as exc:
        return 0, str(exc)


def _docker_ps() -> str:
    """Get docker container status."""
    result = subprocess.run(
        [*COMPOSE_CMD, "ps", "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


def _docker_logs(service: str, tail: int = LOG_TAIL_LINES) -> str:
    """Get recent logs for a service."""
    result = subprocess.run(
        [*COMPOSE_CMD, "logs", service, "--tail", str(tail), "--no-color"],
        capture_output=True,
        text=True,
        check=False,
    )
    return (result.stdout or "") + (result.stderr or "")


def _filter_error_lines(log_text: str) -> list[str]:
    """Extract lines matching error patterns."""
    errors = []
    for line in log_text.splitlines():
        lower = line.lower()
        if any(p.lower() in lower for p in LOG_ERROR_PATTERNS):
            stripped = line.strip()
            if stripped and stripped not in errors:
                errors.append(stripped)
    return errors


def _check_ui() -> tuple[int, str]:
    """Check UI reachability, return (status_code, page_title_or_error)."""
    try:
        resp = requests.get(UI_URL, timeout=10)
        title = ""
        text = resp.text
        if "<title>" in text:
            start = text.index("<title>") + 7
            end = text.index("</title>", start)
            title = text[start:end].strip()
        return resp.status_code, title
    except requests.ConnectionError:
        return 0, "connection refused"
    except Exception as exc:
        return 0, str(exc)


# ── Report generation ────────────────────────────────────────────────


def generate_report() -> str:
    out = StringIO()
    now = datetime.now(UTC)
    since = now - timedelta(hours=1)

    out.write(f"# Verification Report\n\n")
    out.write(f"Generated: {now.isoformat(timespec='seconds')}Z\n")
    out.write(f"API: {API_URL} | UI: {UI_URL}\n\n")

    # ── 1. Containers ────────────────────────────────────────────────

    out.write("## Containers\n\n")
    ps_output = _docker_ps()
    if ps_output.strip():
        try:
            containers = []
            for line in ps_output.strip().splitlines():
                c = json.loads(line)
                containers.append(c)
            out.write("| Service | State | Status |\n")
            out.write("|---|---|---|\n")
            for c in containers:
                name = c.get("Service") or c.get("Name", "?")
                state = c.get("State", "?")
                status = c.get("Status", "?")
                out.write(f"| {name} | {state} | {status} |\n")
        except json.JSONDecodeError:
            out.write(f"```\n{ps_output[:2000]}\n```\n")
    else:
        out.write("No containers found. Is `make deploy` running?\n")
    out.write("\n")

    # ── 2. API Endpoints ─────────────────────────────────────────────

    out.write("## API Responses\n\n")
    for path, label in API_ENDPOINTS:
        params = None
        if path == "/reports/predictions":
            params = {
                "start": since.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "end": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            }
        status, body = _get(path, params)
        out.write(f"### {label} (`{path}`) — HTTP {status}\n\n")
        if status == 200:
            out.write(f"```json\n{_json_compact(body)}\n```\n\n")
        else:
            out.write(f"```\n{body}\n```\n\n")

    # ── 3. Score quality ─────────────────────────────────────────────

    out.write("## Score Analysis\n\n")
    _, predictions = _get(
        "/reports/predictions",
        {
            "start": since.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "end": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        },
    )
    if isinstance(predictions, list):
        scored = [
            p
            for p in predictions
            if p.get("score_value") is not None and not p.get("score_failed")
        ]
        failed = [p for p in predictions if p.get("score_failed")]
        unscored = [p for p in predictions if p.get("score_value") is None]

        out.write(f"- Total predictions: {len(predictions)}\n")
        out.write(f"- Scored: {len(scored)}\n")
        out.write(f"- Failed: {len(failed)}\n")
        out.write(f"- Unscored (pending): {len(unscored)}\n")

        if scored:
            values = [p["score_value"] for p in scored]
            out.write(f"- Score range: {min(values):.6f} to {max(values):.6f}\n")
            out.write(f"- Unique score values: {len(set(values))}\n")
            out.write(
                f"- All zero: {'YES' if all(v == 0.0 for v in values) else 'no'}\n"
            )
            out.write(
                f"- All identical: {'YES' if len(set(values)) <= 1 else 'no'}\n"
            )

            # Per-model breakdown
            by_model: dict[str, list[float]] = {}
            for p in scored:
                mid = p.get("model_id", "?")
                by_model.setdefault(mid, []).append(p["score_value"])
            out.write("\nPer-model scores:\n")
            out.write("| Model | Count | Mean | Min | Max |\n")
            out.write("|---|---|---|---|---|\n")
            for mid, vals in sorted(by_model.items()):
                mean = sum(vals) / len(vals)
                out.write(
                    f"| {mid} | {len(vals)} | {mean:.6f} | {min(vals):.6f} | {max(vals):.6f} |\n"
                )

        if failed:
            out.write("\nFailed predictions (last 5):\n")
            out.write("```json\n")
            for p in failed[-5:]:
                out.write(
                    json.dumps(
                        {
                            "model_id": p.get("model_id"),
                            "score_failed_reason": p.get("score_failed_reason"),
                        },
                        default=str,
                    )
                    + "\n"
                )
            out.write("```\n")
    else:
        out.write(f"Could not fetch predictions: {predictions}\n")
    out.write("\n")

    # ── 4. Docker logs (errors only) ─────────────────────────────────

    out.write("## Docker Logs (errors only)\n\n")
    any_errors = False
    for worker in WORKERS:
        log_text = _docker_logs(worker)
        errors = _filter_error_lines(log_text)
        if errors:
            any_errors = True
            out.write(f"### {worker} ({len(errors)} error lines)\n\n")
            out.write("```\n")
            for line in errors[-20:]:
                out.write(f"{line}\n")
            if len(errors) > 20:
                out.write(f"... ({len(errors) - 20} more)\n")
            out.write("```\n\n")
    if not any_errors:
        out.write("No errors found in any worker logs.\n\n")

    # ── 5. UI ────────────────────────────────────────────────────────

    out.write("## UI\n\n")
    ui_status, ui_title = _check_ui()
    out.write(f"- URL: {UI_URL}\n")
    out.write(f"- HTTP status: {ui_status}\n")
    out.write(f"- Page title: {ui_title or '(none)'}\n")
    out.write("\n")

    return out.getvalue()


# ── Main ─────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate verification report")
    parser.add_argument(
        "--output",
        "-o",
        default="report.md",
        help="Output file (default: report.md)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print to stdout instead of file",
    )
    args = parser.parse_args()

    report = generate_report()

    if args.stdout:
        print(report)
    else:
        with open(args.output, "w") as f:
            f.write(report)
        print(f"Report written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

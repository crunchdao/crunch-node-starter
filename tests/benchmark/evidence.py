"""Evidence collection for benchmark runs.

Three levels:
- fast:     milestones only (no extra artifacts)
- standard: + session file, session metadata (tool calls, turns, files changed)
- full:     + session HTML export, UI screenshots
"""

from __future__ import annotations

import glob
import json
import os
import subprocess

# ---------------------------------------------------------------------------
# Session metadata extraction
# ---------------------------------------------------------------------------


def parse_session(session_path: str) -> dict:
    """Extract metadata from a pi/claude session .jsonl file.

    Returns a dict with: turns, tool_calls, tool_names, errors, model,
    provider, files_modified, duration_estimate.
    """
    if not os.path.exists(session_path):
        return {"error": f"session file not found: {session_path}"}

    turns = 0
    tool_calls = 0
    tool_results = 0
    tool_names: dict[str, int] = {}
    errors: list[str] = []
    model = None
    provider = None
    first_ts = None
    last_ts = None
    files_written: set[str] = set()
    files_read: set[str] = set()
    bash_commands: list[str] = []

    with open(session_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            obj_type = obj.get("type")
            ts = obj.get("timestamp")

            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts

            if obj_type == "model_change":
                model = obj.get("modelId")
                provider = obj.get("provider")

            elif obj_type == "message":
                msg = obj.get("message", {})
                role = msg.get("role")

                if role == "assistant":
                    turns += 1

                content = msg.get("content", [])
                if not isinstance(content, list):
                    continue

                for part in content:
                    if not isinstance(part, dict):
                        continue

                    if part.get("type") == "toolCall":
                        tool_calls += 1
                        name = part.get("name", "?")
                        tool_names[name] = tool_names.get(name, 0) + 1

                        # Extract file paths from tool arguments
                        args = part.get("arguments", {})
                        if isinstance(args, dict):
                            path = args.get("path", "")
                            if path and name in ("write", "edit"):
                                files_written.add(path)
                            elif path and name == "read":
                                files_read.add(path)
                            cmd = args.get("command", "")
                            if cmd and name == "bash":
                                bash_commands.append(
                                    cmd[:120] if len(cmd) > 120 else cmd
                                )

                    elif part.get("type") == "toolResult":
                        tool_results += 1
                        # Check for errors in tool results
                        if part.get("isError"):
                            result_text = str(part.get("text", ""))[:200]
                            errors.append(result_text)

    return {
        "turns": turns,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "tool_breakdown": tool_names,
        "errors_encountered": len(errors),
        "error_samples": errors[:10],
        "model": model,
        "provider": provider,
        "files_written": sorted(files_written),
        "files_read": sorted(files_read)[:30],  # cap to avoid bloat
        "bash_command_count": len(bash_commands),
        "bash_samples": bash_commands[:20],
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
    }


# ---------------------------------------------------------------------------
# Session HTML export
# ---------------------------------------------------------------------------


def export_session_html(session_path: str, output_path: str) -> bool:
    """Export a pi session .jsonl to HTML using pi --export."""
    if not os.path.exists(session_path):
        return False

    try:
        result = subprocess.run(
            ["pi", "--export", session_path, output_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0 and os.path.exists(output_path)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# ---------------------------------------------------------------------------
# UI Screenshots
# ---------------------------------------------------------------------------

UI_PAGES = [
    ("leaderboard", "http://localhost:3000"),
    ("leaderboard_detail", "http://localhost:3000/leaderboard"),
]

API_PAGES = [
    ("api_healthz", "http://localhost:8000/healthz"),
    ("api_leaderboard", "http://localhost:8000/reports/leaderboard"),
    ("api_models", "http://localhost:8000/reports/models"),
]


def capture_screenshots(output_dir: str) -> list[dict]:
    """Capture screenshots of the running UI and API pages.

    Uses agent-browser if available. Falls back to curl for API pages.
    Returns list of {name, path, success} dicts.
    """
    os.makedirs(output_dir, exist_ok=True)
    results = []

    # Capture API responses first (fast, no browser needed)
    for name, url in API_PAGES:
        json_path = os.path.join(output_dir, f"{name}.json")
        success = _capture_api_response(url, json_path)
        results.append({"name": name, "path": json_path, "success": success})

    # Then capture UI screenshots (slower, needs browser)
    try:
        has_browser = (
            subprocess.run(
                ["agent-browser", "--help"],
                capture_output=True,
                timeout=5,
            ).returncode
            == 0
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        has_browser = False

    if has_browser:
        for name, url in UI_PAGES:
            screenshot_path = os.path.join(output_dir, f"{name}.png")
            success = _screenshot_with_browser(url, screenshot_path)
            results.append({"name": name, "path": screenshot_path, "success": success})

    return results


def _screenshot_with_browser(url: str, output_path: str) -> bool:
    """Take a screenshot using agent-browser."""
    try:
        # Open the page
        subprocess.run(
            ["agent-browser", "open", url],
            capture_output=True,
            timeout=20,
        )
        # Wait for network to settle and content to render
        subprocess.run(
            ["agent-browser", "wait", "--load", "networkidle"],
            capture_output=True,
            timeout=20,
        )
        # Extra pause for JS rendering (React hydration, data fetching)
        import time

        time.sleep(3)

        # Take screenshot
        result = subprocess.run(
            ["agent-browser", "screenshot", output_path, "--full"],
            capture_output=True,
            timeout=15,
        )
        return result.returncode == 0 and os.path.exists(output_path)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    finally:
        # Always try to close
        try:
            subprocess.run(
                ["agent-browser", "close"],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass


def _capture_api_response(url: str, output_path: str) -> bool:
    """Capture an API response as JSON using curl."""
    try:
        result = subprocess.run(
            ["curl", "-s", "-o", output_path, "-w", "%{http_code}", url],
            capture_output=True,
            text=True,
            timeout=10,
        )
        status_code = result.stdout.strip()
        return status_code == "200" and os.path.exists(output_path)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# ---------------------------------------------------------------------------
# Collect all evidence for a run
# ---------------------------------------------------------------------------


def collect_evidence(
    level: str,
    workspace: str,
    evidence_dir: str,
    session_path: str | None = None,
) -> dict:
    """Collect evidence at the given level. Returns evidence dict for result JSON.

    Args:
        level: "fast", "standard", or "full"
        workspace: path to the benchmark workspace
        evidence_dir: where to store artifacts (screenshots, exports)
        session_path: path to agent session .jsonl (None if unavailable)
    """
    evidence: dict = {"level": level}

    if level == "fast":
        return evidence

    # --- standard: session metadata ---
    os.makedirs(evidence_dir, exist_ok=True)

    if session_path and os.path.exists(session_path):
        metadata = parse_session(session_path)
        evidence["session"] = metadata

        # Copy session file to evidence dir
        import shutil

        session_copy = os.path.join(evidence_dir, "session.jsonl")
        shutil.copy2(session_path, session_copy)
        evidence["session_file"] = session_copy
    else:
        evidence["session"] = {"error": "no session file available"}

    # Diff against scaffold to see what the agent changed
    files_changed = _get_files_changed(workspace)
    if files_changed is not None:
        evidence["files_changed"] = files_changed

    if level == "standard":
        return evidence

    # --- full: HTML export + screenshots ---
    if session_path and os.path.exists(session_path):
        html_path = os.path.join(evidence_dir, "session.html")
        exported = export_session_html(session_path, html_path)
        evidence["session_html"] = html_path if exported else None

    print("[evidence] Capturing screenshots...")
    screenshots = capture_screenshots(os.path.join(evidence_dir, "screenshots"))
    evidence["screenshots"] = screenshots

    passed = sum(1 for s in screenshots if s["success"])
    print(f"[evidence] Screenshots: {passed}/{len(screenshots)} captured")

    return evidence


def _get_files_changed(workspace: str) -> list[str] | None:
    """Get list of files the agent modified vs the scaffold baseline."""
    # Find scaffold source — walk up from this file to repo root
    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    scaffold_src = os.path.join(repo_root, "scaffold")

    if not os.path.isdir(scaffold_src):
        return None

    try:
        result = subprocess.run(
            [
                "diff",
                "-rq",
                "--exclude=.venv",
                "--exclude=__pycache__",
                "--exclude=.pytest_cache",
                "--exclude=.ruff_cache",
                "--exclude=*.pyc",
                "--exclude=BENCHMARK_SPEC.md",
                "--exclude=.pi",
                "--exclude=uv.lock",
                scaffold_src,
                workspace,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # diff returns 1 if differences found, that's fine
        lines = [
            line.strip() for line in result.stdout.strip().splitlines() if line.strip()
        ]
        return lines[:50]  # cap to avoid bloat
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

"""Post-deploy model health check — tournament mode.

Tournament models are registered in the DB when the first inference
round runs (via the tournament API), not at startup. This script
checks the model-orchestrator for runner status but uses a shorter
timeout and succeeds if the orchestrator is healthy, even without
RUNNING models — tournament rounds will register them on first call.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time


def _read_orchestrator_logs() -> str:
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


def _count_model_states(log_text: str) -> dict[str, str]:
    """Parse latest state per model_id from orchestrator logs."""
    states: dict[str, str] = {}
    for line in log_text.splitlines():
        m = re.search(r"Model (\d+) state changed.*RunnerStatus\.(\w+)", line)
        if m:
            states[m.group(1)] = m.group(2)
        m = re.search(r"Model (\d+) is (RUNNING|STOPPED)", line)
        if m:
            states[m.group(1)] = m.group(2)
    return states


def _count_expected_models() -> int:
    """Count models in models.dev.yml (if accessible)."""
    cmd = [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "--env-file",
        ".local.env",
        "exec",
        "-T",
        "model-orchestrator",
        "cat",
        "/app/data/models.dev.yml",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return 0
    return result.stdout.count("submission_id:")


def _orchestrator_is_up() -> bool:
    """Check that the model-orchestrator container is running."""
    cmd = [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "--env-file",
        ".local.env",
        "ps",
        "-q",
        "model-orchestrator",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return bool(result.stdout.strip())


def main() -> int:
    timeout = int(os.getenv("CHECK_MODELS_TIMEOUT_SECONDS", "60"))
    poll = int(os.getenv("CHECK_MODELS_POLL_SECONDS", "5"))

    print(f"[check-models] tournament mode — waiting up to {timeout}s")
    deadline = time.time() + timeout

    while time.time() < deadline:
        if not _orchestrator_is_up():
            print("[check-models] waiting: orchestrator not running yet")
            time.sleep(poll)
            continue

        logs = _read_orchestrator_logs()
        states = _count_model_states(logs)
        expected = _count_expected_models()

        running = [mid for mid, s in states.items() if s == "RUNNING"]
        stopped = [mid for mid, s in states.items() if s in ("STOPPED", "FAILED")]

        if running:
            print(f"[check-models] OK: {len(running)} model(s) RUNNING", end="")
            if expected:
                print(f" (of {expected} expected)", end="")
            print()

            if stopped:
                print(f"[check-models] ⚠  {len(stopped)} model(s) stopped: {stopped}")

            return 0

        # In tournament mode, models may not show RUNNING in orchestrator
        # logs until the first inference call. If orchestrator is up and
        # has expected models configured, that's enough.
        if expected:
            print(
                f"[check-models] OK: orchestrator up with {expected} model(s) configured"
            )
            if states:
                print(f"[check-models]   current states: {states}")
            return 0

        time.sleep(poll)

    # Fallback: if orchestrator is up, proceed anyway for tournament mode
    if _orchestrator_is_up():
        print("[check-models] OK: orchestrator is running (tournament mode)")
        return 0

    print(f"[check-models] FAILED: orchestrator not available after {timeout}s")
    logs = _read_orchestrator_logs()
    print("\n[check-models] Recent orchestrator logs:")
    for line in logs.splitlines()[-20:]:
        print(f"  {line}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

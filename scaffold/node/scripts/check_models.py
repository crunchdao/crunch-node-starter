"""Post-deploy model health check.

Waits for at least one model to reach RUNNING state.
Reports failed models as warnings but only fails if ZERO models run.
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


def main() -> int:
    timeout = int(os.getenv("CHECK_MODELS_TIMEOUT_SECONDS", "180"))
    poll = int(os.getenv("CHECK_MODELS_POLL_SECONDS", "5"))
    expected = 0

    print(f"[check-models] waiting up to {timeout}s for models")
    deadline = time.time() + timeout

    while time.time() < deadline:
        logs = _read_orchestrator_logs()
        states = _count_model_states(logs)

        if not expected:
            expected = _count_expected_models()

        running = [mid for mid, s in states.items() if s == "RUNNING"]
        stopped = [mid for mid, s in states.items() if s in ("STOPPED", "FAILED")]

        if running:
            print(f"[check-models] OK: {len(running)} model(s) RUNNING", end="")
            if expected:
                print(f" (of {expected} expected)", end="")
            print()

            if stopped:
                print(f"[check-models] ⚠  {len(stopped)} model(s) failed: {stopped}")
                for mid in stopped:
                    for line in logs.splitlines():
                        if f"Model {mid}" in line and (
                            "STOPPED" in line or "FAILED" in line
                        ):
                            print(f"  model {mid}: {line.strip()[-120:]}")
                            break

            return 0

        time.sleep(poll)

    print(f"[check-models] FAILED: no models reached RUNNING after {timeout}s")
    logs = _read_orchestrator_logs()
    print("\n[check-models] Recent orchestrator logs:")
    for line in logs.splitlines()[-20:]:
        print(f"  {line}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

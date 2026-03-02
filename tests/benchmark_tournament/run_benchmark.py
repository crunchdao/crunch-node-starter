"""Tournament benchmark — tests agent ability to build a tournament competition.

Usage:
    python -m tests.benchmark_tournament.run_benchmark [--agent-cmd CMD] [--timeout SECS]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime

from tests.benchmark_tournament.spec import AGENT_PROMPT, SPEC_VERSION
from tests.benchmark_tournament.verify import run_all

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")

DEFAULT_TIMEOUT = 600  # 10 minutes
DEFAULT_AGENT_CMD = "pi"


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d-%H-%M-%S")


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ignore_patterns(directory: str, contents: list[str]) -> set[str]:
    ignored = set()
    for item in contents:
        if item in (".venv", "__pycache__", ".pytest_cache", ".ruff_cache"):
            ignored.add(item)
        elif item.endswith(".pyc"):
            ignored.add(item)
    return ignored


def setup_workspace(repo_root: str, target: str | None = None) -> str:
    """Copy scaffold/ to a fresh directory."""
    scaffold_src = os.path.join(repo_root, "scaffold")
    if not os.path.isdir(scaffold_src):
        raise FileNotFoundError(f"scaffold/ not found at {scaffold_src}")

    if target:
        workspace = target
        if os.path.exists(workspace):
            shutil.rmtree(workspace)
    else:
        workspace = tempfile.mkdtemp(prefix="benchmark-tournament-")

    shutil.copytree(
        scaffold_src, workspace, ignore=_ignore_patterns, dirs_exist_ok=True
    )

    # Apply tournament pack overlay (tournament-aware scripts, config, etc.)
    pack_dir = os.path.join(repo_root, "packs", "tournament")
    if os.path.isdir(pack_dir):
        shutil.copytree(
            pack_dir, workspace, ignore=_ignore_patterns, dirs_exist_ok=True
        )
        print("[benchmark] Applied tournament pack overlay")

    print(f"[benchmark] Workspace: {workspace}")
    return workspace


def _build_agent_command(agent_cmd: str, session_path: str) -> tuple[str, bool]:
    agent_lower = agent_cmd.lower().strip()

    if agent_lower.startswith("pi"):
        extra = agent_cmd[len("pi") :].strip() if len(agent_cmd) > 2 else ""
        cmd = f"pi -p --session {session_path} {extra} @BENCHMARK_SPEC.md"
        return cmd, False

    if "claude" in agent_lower:
        extra = agent_cmd.replace("claude", "", 1).strip()
        cmd = f"claude -p --dangerously-skip-permissions --verbose {extra}"
        return cmd, True

    return agent_cmd, True


def invoke_agent(
    agent_cmd: str,
    workspace: str,
    prompt: str,
    timeout: int,
    log_path: str,
    session_path: str | None = None,
) -> tuple[int, float, bool]:
    prompt_file = os.path.join(workspace, "BENCHMARK_SPEC.md")
    with open(prompt_file, "w") as f:
        f.write(prompt)

    _session = session_path or os.path.join(workspace, "session.jsonl")
    cmd_template, needs_prompt_arg = _build_agent_command(agent_cmd, _session)

    if needs_prompt_arg:
        full_cmd = (
            f'{cmd_template} "Read BENCHMARK_SPEC.md and follow all instructions.'
            f' Do not ask questions — execute everything."'
        )
    else:
        full_cmd = cmd_template

    print(f"[benchmark] Running: {full_cmd}")
    print(f"[benchmark] Timeout: {timeout}s")

    start = time.time()
    timed_out = False

    logged_cmd = f'{{ {full_cmd} ; }} > "{log_path}" 2>&1'

    try:
        import signal

        proc = subprocess.Popen(
            logged_cmd,
            shell=True,
            cwd=workspace,
            start_new_session=True,
        )
        exit_code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        import signal

        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        timed_out = True
        exit_code = -1

    duration = time.time() - start
    print(
        f"[benchmark] Agent finished: exit_code={exit_code} "
        f"duration={duration:.0f}s timed_out={timed_out}"
    )
    return exit_code, duration, timed_out


def record_result(
    ts: str,
    agent_cmd: str,
    exit_code: int,
    duration: float,
    timed_out: bool,
    milestones: dict,
    log_path: str,
    workspace: str = "",
) -> str:
    passed = sum(1 for m in milestones.values() if m.get("passed"))
    total = len(milestones)

    result = {
        "timestamp": _iso_now(),
        "agent_cmd": agent_cmd,
        "spec_version": SPEC_VERSION,
        "duration_seconds": round(duration, 1),
        "agent_exit_code": exit_code,
        "timed_out": timed_out,
        "milestones": milestones,
        "milestone_count": f"{passed}/{total}",
        "agent_log_file": os.path.basename(log_path),
        "workspace": workspace,
    }

    os.makedirs(RESULTS_DIR, exist_ok=True)
    result_path = os.path.join(RESULTS_DIR, f"{ts}.json")

    with open(result_path, "w") as f:
        json.dump(result, f, indent=2)
        f.write("\n")

    print(f"[benchmark] Result: {result_path}")
    print(f"[benchmark] Milestones: {passed}/{total}")
    return result_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run tournament benchmark")
    parser.add_argument(
        "--agent-cmd",
        default=os.getenv("AGENT_CMD", DEFAULT_AGENT_CMD),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("BENCHMARK_TIMEOUT", str(DEFAULT_TIMEOUT))),
    )
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--verify-only", default=None, metavar="DIR")
    args = parser.parse_args()

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

    ts = _timestamp()
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_path = os.path.join(LOGS_DIR, f"{ts}.log")
    session_path = os.path.join(LOGS_DIR, f"{ts}-session.jsonl")

    if args.verify_only:
        workspace = args.verify_only
        print(f"[benchmark] Verify-only mode: {workspace}")
        exit_code = 0
        duration = 0.0
        timed_out = False
    else:
        workspace = setup_workspace(repo_root, args.workspace)
        exit_code, duration, timed_out = invoke_agent(
            args.agent_cmd,
            workspace,
            AGENT_PROMPT,
            args.timeout,
            log_path,
            session_path=session_path,
        )

    print()
    print("[benchmark] Verifying milestones...")
    milestones = run_all(workspace)

    print()
    record_result(
        ts,
        args.agent_cmd,
        exit_code,
        duration,
        timed_out,
        milestones,
        log_path,
        workspace=workspace,
    )

    all_passed = all(m["passed"] for m in milestones.values())
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())

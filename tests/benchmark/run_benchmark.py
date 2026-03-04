"""Scaffold benchmark — orchestrates setup, agent invocation, verification, and recording.

Usage:
    python -m tests.benchmark.run_benchmark [--agent-cmd CMD] [--timeout SECS] [--workspace DIR]
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

from tests.benchmark.compare import compare_last_two
from tests.benchmark.evidence import collect_evidence
from tests.benchmark.spec import AGENT_PROMPT, SPEC_VERSION
from tests.benchmark.verify import run_all

EVIDENCE_LEVELS = ("fast", "standard", "full")

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")

DEFAULT_TIMEOUT = 450  # 7.5 minutes
DEFAULT_AGENT_CMD = "pi"


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d-%H-%M-%S")


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _teardown_existing(workspace: str) -> None:
    """Tear down any running containers from a previous workspace."""
    node_dir = os.path.join(workspace, "node")
    env_file = os.path.join(node_dir, ".local.env")
    if os.path.exists(env_file) and os.path.exists(
        os.path.join(node_dir, "docker-compose.yml")
    ):
        print(f"[benchmark] Tearing down containers in {node_dir}...")
        try:
            subprocess.run(
                "docker compose -f docker-compose.yml --env-file .local.env down -v --remove-orphans",
                shell=True,
                cwd=node_dir,
                capture_output=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            print("[benchmark] Warning: teardown timed out, continuing anyway")

    # Kill ALL crunch-node containers (from any previous benchmark or dev run)
    # to free ports. Previous runs with different CRUNCH_IDs won't be caught
    # by the compose-down above.
    for cleanup_cmd in [
        "docker rm -f $(docker ps -aq --filter name=crunch-node-) 2>/dev/null || true",
        "docker rm -f $(docker ps -aq --filter name=crunchdao-model-runner) 2>/dev/null || true",
        "docker network prune -f",
        "docker volume prune -f",
    ]:
        try:
            subprocess.run(cleanup_cmd, shell=True, capture_output=True, timeout=15)
        except subprocess.TimeoutExpired:
            pass


def _ignore_patterns(directory: str, contents: list[str]) -> set[str]:
    """Ignore .venv, __pycache__, .pyc files when copying scaffold."""
    ignored = set()
    for item in contents:
        if item in (".venv", "__pycache__", ".pytest_cache", ".ruff_cache"):
            ignored.add(item)
        elif item.endswith(".pyc"):
            ignored.add(item)
    return ignored


def setup_workspace(repo_root: str, target: str | None = None) -> str:
    """Copy scaffold/ to a fresh temp directory (excluding venvs and caches)."""
    scaffold_src = os.path.join(repo_root, "scaffold")
    if not os.path.isdir(scaffold_src):
        raise FileNotFoundError(f"scaffold/ not found at {scaffold_src}")

    if target:
        workspace = target
        if os.path.exists(workspace):
            _teardown_existing(workspace)
            shutil.rmtree(workspace)
    else:
        workspace = tempfile.mkdtemp(prefix="benchmark-scaffold-")

    shutil.copytree(
        scaffold_src, workspace, ignore=_ignore_patterns, dirs_exist_ok=True
    )

    # Copy local coordinator-node source so Docker builds use it
    _copy_local_coordinator(repo_root, workspace)

    # Copy docker-compose override for local development
    _copy_compose_override(repo_root, workspace)

    # Patch CRUNCH_ID to a unique value so Docker containers don't clash
    # with other benchmark runs or the real scaffold
    _patch_crunch_id(workspace)

    print(f"[benchmark] Workspace: {workspace}")
    return workspace


def _copy_local_coordinator(repo_root: str, workspace: str) -> None:
    """Copy local coordinator-node source into the workspace and patch the
    Dockerfile to install it over the PyPI version."""
    src_pkg = os.path.join(repo_root, "coordinator_node")
    src_toml = os.path.join(repo_root, "pyproject.toml")

    if not os.path.isdir(src_pkg) or not os.path.isfile(src_toml):
        print("[benchmark] Warning: local coordinator-node source not found, skipping")
        return

    dest = os.path.join(workspace, "coordinator_node_local")
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)

    shutil.copytree(
        src_pkg,
        os.path.join(dest, "coordinator_node"),
        ignore=_ignore_patterns,
    )
    shutil.copy2(src_toml, os.path.join(dest, "pyproject.toml"))

    _patch_dockerfile_local_coordinator(workspace)
    print("[benchmark] Copied local coordinator-node source into workspace")


def _copy_compose_override(repo_root: str, workspace: str) -> None:
    """Copy docker-compose override for local development into workspace.

    Adjusts volume mount paths from the repo layout (../../coordinator_node)
    to the benchmark workspace layout (../coordinator_node_local/coordinator_node).
    """
    src = os.path.join(repo_root, "tests", "benchmark", "docker-compose.local-dev.yml")
    dest = os.path.join(workspace, "node", "docker-compose.override.yml")

    if not os.path.isfile(src):
        print("[benchmark] Warning: docker-compose.local-dev.yml not found, skipping")
        return

    with open(src) as f:
        content = f.read()

    # Adjust paths for benchmark workspace layout
    content = content.replace(
        "../../coordinator_node:/app/coordinator_node:ro",
        "../coordinator_node_local/coordinator_node:/app/coordinator_node:ro",
    )

    with open(dest, "w") as f:
        f.write(content)

    print("[benchmark] Copied docker-compose override into workspace (paths adjusted)")


_LOCAL_COORDINATOR_DOCKERFILE_LINES = (
    "\n# Local coordinator-node overlay (injected by benchmark)\n"
    "COPY coordinator_node_local/ ./coordinator_node_local/\n"
    "RUN pip install --no-cache-dir --force-reinstall --no-deps "
    "./coordinator_node_local/\n"
)

_PYPI_INSTALL_MARKER = 'RUN pip install --no-cache-dir "coordinator-node>='


def _patch_dockerfile_local_coordinator(workspace: str) -> None:
    """Inject local coordinator-node install lines into the Dockerfile,
    right after the PyPI install."""
    dockerfile = os.path.join(workspace, "node", "Dockerfile")
    if not os.path.isfile(dockerfile):
        return

    with open(dockerfile) as f:
        lines = f.readlines()

    insert_idx = None
    for i, line in enumerate(lines):
        if line.startswith(_PYPI_INSTALL_MARKER):
            insert_idx = i + 1
            break

    if insert_idx is None:
        print("[benchmark] Warning: could not find PyPI install line in Dockerfile")
        return

    lines.insert(insert_idx, _LOCAL_COORDINATOR_DOCKERFILE_LINES)

    with open(dockerfile, "w") as f:
        f.writelines(lines)


def _patch_crunch_id(workspace: str) -> None:
    """Set a unique CRUNCH_ID in .local.env to avoid Docker name collisions."""
    env_file = os.path.join(workspace, "node", ".local.env")
    if not os.path.exists(env_file):
        return

    unique_id = f"bench-{_timestamp()}"

    with open(env_file) as f:
        content = f.read()

    import re

    content = re.sub(
        r"^CRUNCH_ID=.*$",
        f"CRUNCH_ID={unique_id}",
        content,
        flags=re.MULTILINE,
    )

    with open(env_file, "w") as f:
        f.write(content)

    print(f"[benchmark] Patched CRUNCH_ID={unique_id}")


def _build_agent_command(agent_cmd: str, session_path: str) -> tuple[str, bool]:
    """Return (shell_command, needs_prompt_as_arg).

    Known agents get optimized flags appended. Extra flags from the user's
    --agent-cmd are preserved (e.g., --provider, --model).
    """
    agent_lower = agent_cmd.lower().strip()

    if agent_lower.startswith("pi"):
        # Preserve extra flags (--provider, --model, etc.)
        extra = agent_cmd[len("pi") :].strip() if len(agent_cmd) > 2 else ""
        cmd = f"pi -p --session {session_path} {extra} @BENCHMARK_SPEC.md"
        return cmd, False

    if "claude" in agent_lower:
        extra = agent_cmd.replace("claude", "", 1).strip()
        cmd = f"claude -p --dangerously-skip-permissions --verbose {extra}"
        return cmd, True

    # Unknown agent — assume it accepts a prompt as argument
    return agent_cmd, True


def invoke_agent(
    agent_cmd: str,
    workspace: str,
    prompt: str,
    timeout: int,
    log_path: str,
    session_path: str | None = None,
) -> tuple[int, float, bool]:
    """Run the agent command in the workspace directory.

    Returns (exit_code, duration_seconds, timed_out).
    """
    # Write prompt to a file the agent can read
    prompt_file = os.path.join(workspace, "BENCHMARK_SPEC.md")
    with open(prompt_file, "w") as f:
        f.write(prompt)

    _session = session_path or os.path.join(workspace, "session.jsonl")
    cmd_template, needs_prompt_arg = _build_agent_command(agent_cmd, _session)

    if needs_prompt_arg:
        full_cmd = (
            f'{cmd_template} "Read BENCHMARK_SPEC.md and follow all instructions '
            f'in it. Do not ask questions — execute everything."'
        )
    else:
        # Agent reads the file directly (e.g., pi -p @BENCHMARK_SPEC.md)
        full_cmd = cmd_template

    print(f"[benchmark] Running: {full_cmd}")
    print(f"[benchmark] Timeout: {timeout}s")
    print(f"[benchmark] Log: {log_path}")

    start = time.time()
    timed_out = False

    # Redirect stdout/stderr to log file. Note: agent output may be
    # empty if the agent is killed before completing (pi -p buffers
    # all output until the end).
    logged_cmd = f'{{ {full_cmd} ; }} > "{log_path}" 2>&1'

    try:
        # Use process group so we can kill the agent and all its children
        proc = subprocess.Popen(
            logged_cmd,
            shell=True,
            cwd=workspace,
            start_new_session=True,
        )
        exit_code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Kill the entire process group (shell + pi + any children)
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
    evidence: dict | None = None,
) -> str:
    """Write result JSON and return the file path."""
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

    if evidence:
        result["evidence"] = evidence

        # Promote key session metrics to top level for easy comparison
        session = evidence.get("session", {})
        if isinstance(session, dict) and "error" not in session:
            tokens = session.get("tokens", {})
            result["tokens"] = tokens
            result["cost_usd"] = session.get("cost_usd", 0)
            result["turns"] = session.get("turns", 0)
            result["tool_calls"] = session.get("tool_calls", 0)
            result["compaction_count"] = session.get("compaction_count", 0)
            result["efficiency"] = session.get("efficiency", {})

    os.makedirs(RESULTS_DIR, exist_ok=True)
    result_path = os.path.join(RESULTS_DIR, f"{ts}.json")

    with open(result_path, "w") as f:
        json.dump(result, f, indent=2)
        f.write("\n")

    print(f"[benchmark] Result: {result_path}")
    print(f"[benchmark] Milestones: {passed}/{total}")
    return result_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scaffold benchmark")
    parser.add_argument(
        "--agent-cmd",
        default=os.getenv("AGENT_CMD", DEFAULT_AGENT_CMD),
        help=f"Agent CLI command (default: {DEFAULT_AGENT_CMD})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("BENCHMARK_TIMEOUT", str(DEFAULT_TIMEOUT))),
        help=f"Agent timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Use specific directory instead of temp (for debugging)",
    )
    parser.add_argument(
        "--verify-only",
        default=None,
        metavar="DIR",
        help="Skip agent invocation, just verify an existing workspace",
    )
    parser.add_argument(
        "--evidence",
        choices=EVIDENCE_LEVELS,
        default=os.getenv("BENCHMARK_EVIDENCE", "standard"),
        help="Evidence level: fast (milestones only), standard (+session), full (+screenshots)",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Just compare last two results (no run)",
    )
    args = parser.parse_args()

    if args.compare:
        return compare_last_two()

    # Resolve repo root (parent of tests/)
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

    ts = _timestamp()
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_path = os.path.join(LOGS_DIR, f"{ts}.log")

    session_path = os.path.join(LOGS_DIR, f"{ts}-session.jsonl")
    evidence_dir = os.path.join(LOGS_DIR, f"{ts}-evidence")

    if args.verify_only:
        # Skip setup and agent — just verify
        workspace = args.verify_only
        print(f"[benchmark] Verify-only mode: {workspace}")
        exit_code = 0
        duration = 0.0
        timed_out = False
    else:
        # Tear down any existing scaffold containers to free ports
        existing_scaffold = os.path.join(repo_root, "scaffold")
        _teardown_existing(existing_scaffold)

        # Full run
        workspace = setup_workspace(repo_root, args.workspace)
        exit_code, duration, timed_out = invoke_agent(
            args.agent_cmd,
            workspace,
            AGENT_PROMPT,
            args.timeout,
            log_path,
            session_path=session_path,
        )

    # Verify milestones
    print()
    print("[benchmark] Verifying milestones...")
    milestones = run_all(workspace)

    # Collect evidence BEFORE teardown (screenshots need running containers)
    print()
    print(f"[benchmark] Collecting evidence (level={args.evidence})...")
    # Session file may be in workspace if agent saved it there
    actual_session = session_path
    workspace_session = os.path.join(workspace, "session.jsonl")
    if not os.path.exists(actual_session) and os.path.exists(workspace_session):
        actual_session = workspace_session

    evidence = collect_evidence(
        level=args.evidence,
        workspace=workspace,
        evidence_dir=evidence_dir,
        session_path=actual_session if os.path.exists(actual_session) else None,
    )

    # Record
    print()
    result_path = record_result(
        ts,
        args.agent_cmd,
        exit_code,
        duration,
        timed_out,
        milestones,
        log_path,
        workspace=workspace,
        evidence=evidence,
    )

    # Cleanup containers AFTER evidence collection
    if not args.verify_only:
        print()
        print("[benchmark] Cleaning up containers...")
        _teardown_existing(workspace)

    # Compare with previous
    print()
    compare_last_two()

    # Return non-zero if any milestone failed
    all_passed = all(m["passed"] for m in milestones.values())
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())

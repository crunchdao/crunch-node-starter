"""Trading benchmark — validates the trading pack end-to-end.

Usage:
    python -m tests.benchmark_trading.run_benchmark [--agent-cmd CMD] [--timeout SECS]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime

from tests.benchmark_trading.spec import AGENT_PROMPT, SPEC_VERSION
from tests.benchmark_trading.verify import run_all

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")

DEFAULT_TIMEOUT = 600
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
    """Copy scaffold/, overlay packs/trading/, set up .local.env."""
    scaffold_src = os.path.join(repo_root, "scaffold")
    if not os.path.isdir(scaffold_src):
        raise FileNotFoundError(f"scaffold/ not found at {scaffold_src}")

    if target:
        workspace = target
        if os.path.exists(workspace):
            shutil.rmtree(workspace)
    else:
        workspace = tempfile.mkdtemp(prefix="benchmark-trading-")

    shutil.copytree(
        scaffold_src, workspace, ignore=_ignore_patterns, dirs_exist_ok=True
    )

    pack_dir = os.path.join(repo_root, "packs", "trading")
    if os.path.isdir(pack_dir):
        shutil.copytree(
            pack_dir, workspace, ignore=_ignore_patterns, dirs_exist_ok=True
        )
        print("[benchmark] Applied trading pack overlay")

    env_example = os.path.join(workspace, "node", ".local.env.example")
    env_target = os.path.join(workspace, "node", ".local.env")
    if os.path.exists(env_example) and not os.path.exists(env_target):
        shutil.copy2(env_example, env_target)
        print("[benchmark] Copied .local.env.example -> .local.env")

    _copy_local_coordinator(repo_root, workspace)
    _fix_local_coordinator_pyproject(workspace)
    _copy_compose_override(repo_root, workspace)
    _clone_webapp(workspace)
    _patch_crunch_id(workspace)

    print(f"[benchmark] Workspace: {workspace}")
    return workspace


def _copy_local_coordinator(repo_root: str, workspace: str) -> None:
    """Copy local crunch-node source into the workspace and patch the
    Dockerfile to install it over the PyPI version."""
    src_pkg = os.path.join(repo_root, "crunch_node")
    src_toml = os.path.join(repo_root, "pyproject.toml")

    if not os.path.isdir(src_pkg) or not os.path.isfile(src_toml):
        print("[benchmark] Warning: local crunch-node source not found, skipping")
        return

    dest = os.path.join(workspace, "crunch_node_local")
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)

    shutil.copytree(
        src_pkg,
        os.path.join(dest, "crunch_node"),
        ignore=_ignore_patterns,
    )
    shutil.copy2(src_toml, os.path.join(dest, "pyproject.toml"))

    _patch_dockerfile_local_coordinator(workspace)
    print("[benchmark] Copied local crunch-node source into workspace")


def _fix_local_coordinator_pyproject(workspace: str) -> None:
    """Ensure crunch_node_local can be pip-installed in Docker."""
    local_dir = os.path.join(workspace, "crunch_node_local")
    if not os.path.isdir(local_dir):
        return

    readme = os.path.join(local_dir, "README.md")
    if not os.path.exists(readme):
        with open(readme, "w") as f:
            f.write("# crunch-node (benchmark local)\n")

    toml_path = os.path.join(local_dir, "pyproject.toml")
    if not os.path.isfile(toml_path):
        return

    with open(toml_path) as f:
        content = f.read()

    for dirname in ("scaffold", "packs"):
        if dirname in content:
            target_dir = os.path.join(local_dir, dirname)
            if not os.path.exists(target_dir):
                os.makedirs(target_dir, exist_ok=True)
                with open(os.path.join(target_dir, ".gitkeep"), "w") as f:
                    pass


_LOCAL_COORDINATOR_DOCKERFILE_LINES = (
    "\n# Local crunch-node overlay (injected by benchmark)\n"
    "COPY crunch_node_local/ ./crunch_node_local/\n"
    "RUN pip install --no-cache-dir --force-reinstall --no-deps "
    "./crunch_node_local/\n"
)

_PYPI_INSTALL_MARKER = 'RUN pip install --no-cache-dir "crunch-node>='


def _patch_dockerfile_local_coordinator(workspace: str) -> None:
    """Inject local crunch-node install lines into the Dockerfile."""
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


def _copy_compose_override(repo_root: str, workspace: str) -> None:
    """Copy docker-compose override for local development into workspace."""
    src = os.path.join(repo_root, "tests", "benchmark", "docker-compose.local-dev.yml")
    dest = os.path.join(workspace, "node", "docker-compose.override.yml")

    if not os.path.isfile(src):
        print("[benchmark] Warning: docker-compose.local-dev.yml not found, skipping")
        return

    with open(src) as f:
        content = f.read()

    content = content.replace(
        "../../crunch_node:/app/crunch_node:ro",
        "../crunch_node_local/crunch_node:/app/crunch_node:ro",
    )

    with open(dest, "w") as f:
        f.write(content)

    print("[benchmark] Copied docker-compose override into workspace (paths adjusted)")


_WEBAPP_REPO_URL = "https://github.com/crunchdao/coordinator-webapp.git"


def _clone_webapp(workspace: str) -> None:
    """Clone coordinator-webapp into workspace/webapp (needed by report-ui)."""
    webapp_dir = os.path.join(workspace, "webapp")
    if os.path.isdir(webapp_dir):
        return

    print("[benchmark] Cloning coordinator-webapp...")
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", _WEBAPP_REPO_URL, "webapp"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        print("[benchmark] Cloned coordinator-webapp into workspace/webapp")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"[benchmark] Warning: failed to clone webapp: {exc}")
        print("[benchmark] report-ui service will fail to build")


def _patch_crunch_id(workspace: str) -> None:
    """Set a unique CRUNCH_ID in .local.env to avoid Docker name collisions."""
    env_file = os.path.join(workspace, "node", ".local.env")
    if not os.path.exists(env_file):
        return

    unique_id = f"bench-trading-{_timestamp()}"

    with open(env_file) as f:
        content = f.read()

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
    agent_lower = agent_cmd.lower().strip()

    if agent_lower.startswith("pi"):
        extra = agent_cmd[len("pi"):].strip() if len(agent_cmd) > 2 else ""
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
    parser = argparse.ArgumentParser(description="Run trading benchmark")
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

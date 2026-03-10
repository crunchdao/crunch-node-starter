"""Milestone verification for trading benchmark.

Each check_* function returns (passed: bool, details: str).

Milestones:
- M1: Types correct (InferenceOutput with action:str, amount:float)
- M2: Trading config present (TradingConfig with signal_mode="order")
- M3: Examples exist (tracker files with predict returning action/amount)
- M4: Tests pass (make test)
- M5: Deploy succeeded (Docker containers running)
- M6: E2E verified (make verify-e2e)
- M7: PnL non-zero (TradingEngine produces snapshots with non-zero net_pnl)
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess

from tests.benchmark_trading.spec import (
    EXPECTED_EXAMPLES,
    EXPECTED_OUTPUT_FIELDS,
)


def _run(cmd: str, cwd: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _node_dir(workspace: str) -> str:
    return os.path.join(workspace, "node")


def _find_class_fields(source: str, class_keywords: list[str]) -> dict[str, bool]:
    """Find annotated fields in classes matching keywords. Returns {field: has_default}."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    fields: dict[str, bool] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            name_lower = node.name.lower()
            if any(kw in name_lower for kw in class_keywords):
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(
                        item.target, ast.Name
                    ):
                        fields[item.target.id] = item.value is not None
    return fields


# --- M1: Types correct (InferenceOutput has action + amount) ---


def check_types(workspace: str) -> tuple[bool, str]:
    """Check InferenceOutput has action: str and amount: float."""
    config_path = os.path.join(workspace, "node", "config", "crunch_config.py")
    if not os.path.exists(config_path):
        return False, f"crunch_config.py not found at {config_path}"

    with open(config_path) as f:
        source = f.read()

    output_fields = _find_class_fields(source, ["output", "inference"])
    if not output_fields:
        if re.search(r"action\s*:\s*str", source) and re.search(
            r"amount\s*:\s*float", source
        ):
            return True, "action: str, amount: float (regex match)"
        return False, "No output type class found with action/amount fields"

    missing = [f for f in EXPECTED_OUTPUT_FIELDS if f not in output_fields]
    if missing:
        return (
            False,
            f"Missing output fields: {missing}. Found: {list(output_fields.keys())}",
        )

    return True, f"Output fields: {list(output_fields.keys())}"


# --- M2: Trading config present ---


def check_trading_config(workspace: str) -> tuple[bool, str]:
    """Check that TradingConfig is configured with signal_mode='order'."""
    config_path = os.path.join(workspace, "node", "config", "crunch_config.py")
    if not os.path.exists(config_path):
        return False, "crunch_config.py not found"

    with open(config_path) as f:
        source = f.read()

    if "TradingConfig" not in source:
        return False, "TradingConfig not referenced in crunch_config.py"

    if "order" not in source:
        return False, "signal_mode='order' not found"

    if "trading" not in source:
        return False, "trading field not set in CrunchConfig"

    return True, "TradingConfig with order mode configured"


# --- M3: Examples exist ---


def check_examples(workspace: str) -> tuple[bool, str]:
    """Check that expected example tracker files exist with predict()."""
    examples_dir = os.path.join(
        workspace, "challenge", "starter_challenge", "examples"
    )
    if not os.path.isdir(examples_dir):
        return False, "examples dir not found"

    found = []
    missing = []

    for filename in EXPECTED_EXAMPLES:
        filepath = os.path.join(examples_dir, filename)
        if not os.path.exists(filepath):
            missing.append(filename)
            continue

        with open(filepath) as f:
            source = f.read()

        if "def predict" not in source:
            missing.append(f"{filename} (no predict method)")
            continue

        if "action" not in source:
            missing.append(f"{filename} (no 'action' in output)")
            continue

        found.append(filename)

    if missing:
        return False, f"Missing/invalid: {missing}. Found: {found}"

    return True, f"{len(found)}/{len(EXPECTED_EXAMPLES)} found, all have correct shape"


# --- M5: Tests pass ---


def check_tests(workspace: str) -> tuple[bool, str]:
    """Run make test."""
    try:
        result = _run("make test", cwd=workspace, timeout=120)
    except subprocess.TimeoutExpired:
        return False, "make test timed out"

    if result.returncode == 0:
        return True, "exit_code=0"

    output = (result.stdout or "") + "\n" + (result.stderr or "")
    tail = "\n".join(output.strip().splitlines()[-20:])
    return False, f"exit_code={result.returncode}\n{tail}"


# --- M6: Deploy succeeded ---


def check_deploy(workspace: str) -> tuple[bool, str]:
    """Check docker containers are running."""
    node = _node_dir(workspace)
    env_file = os.path.join(node, ".local.env")

    if not os.path.exists(env_file):
        return False, f".local.env not found at {env_file}"

    try:
        result = _run(
            "docker compose -f docker-compose.yml --env-file .local.env ps --format json",
            cwd=node,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False, "docker compose ps timed out"

    if result.returncode != 0:
        return False, f"docker compose ps failed: {result.stderr[:200]}"

    output = result.stdout.strip()
    if not output:
        return False, "no containers found"

    running = 0
    total = 0
    not_running = []

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            container = json.loads(line)
            total += 1
            state = container.get("State", "").lower()
            if state == "running":
                running += 1
            else:
                name = container.get("Name", container.get("Service", "?"))
                not_running.append(f"{name}={state}")
        except json.JSONDecodeError:
            continue

    if total == 0:
        return False, "no containers parsed from docker compose ps"

    if not_running:
        return False, f"{running}/{total} running. Not running: {not_running}"

    return True, f"{running}/{total} containers running"


# --- M7: E2E verified ---


def _ensure_deploy(workspace: str) -> tuple[bool, str]:
    """Ensure containers are running. Skips if already healthy."""
    node = _node_dir(workspace)
    env_file = os.path.join(node, ".local.env")
    compose_file = os.path.join(node, "docker-compose.yml")

    if not os.path.exists(env_file) or not os.path.exists(compose_file):
        return False, "missing .local.env or docker-compose.yml"

    try:
        ps_result = _run(
            "docker compose -f docker-compose.yml --env-file .local.env ps -q",
            cwd=node,
            timeout=15,
        )
        running_count = len(
            [
                line
                for line in (ps_result.stdout or "").strip().splitlines()
                if line.strip()
            ]
        )
        if running_count >= 4:
            return True, f"already running ({running_count} containers)"
    except (subprocess.TimeoutExpired, Exception):
        pass

    try:
        result = _run(
            "docker compose -f docker-compose.yml --env-file .local.env up -d",
            cwd=node,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return False, "docker compose up timed out"

    if result.returncode != 0:
        stderr = (result.stderr or "")[-200:]
        return False, f"docker compose up failed: {stderr}"
    return True, "containers started"


def check_e2e(workspace: str) -> tuple[bool, str]:
    """Run make verify-e2e."""
    deploy_ok, deploy_detail = _ensure_deploy(workspace)
    if not deploy_ok:
        return False, f"could not complete deploy: {deploy_detail}"

    env = {**os.environ, "E2E_VERIFY_TIMEOUT_SECONDS": "300"}
    try:
        result = subprocess.run(
            "make verify-e2e",
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=360,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, "make verify-e2e timed out (360s)"

    output = (result.stdout or "") + "\n" + (result.stderr or "")

    if result.returncode == 0:
        summary_lines = []
        capture = False
        for line in output.splitlines():
            if "VERIFICATION SUMMARY" in line:
                capture = True
            if capture:
                summary_lines.append(line)
        summary = "\n".join(summary_lines[-30:]) if summary_lines else "passed"
        return True, summary

    tail = "\n".join(output.strip().splitlines()[-20:])
    return False, f"exit_code={result.returncode}\n{tail}"


# --- M8: PnL non-zero ---


def _read_report_api_port(workspace: str) -> str:
    env_file = os.path.join(_node_dir(workspace), ".local.env")
    if not os.path.exists(env_file):
        return "8000"
    with open(env_file) as f:
        for line in f:
            if line.startswith("REPORT_API_PORT="):
                return line.split("=", 1)[1].strip()
    return "8000"


def check_pnl(workspace: str) -> tuple[bool, str]:
    """Check that TradingEngine produced snapshots with non-zero net_pnl."""
    try:
        import requests
    except ImportError:
        return False, "requests not installed"

    port = _read_report_api_port(workspace)
    base_url = f"http://localhost:{port}"

    try:
        resp = requests.get(f"{base_url}/reports/snapshots", timeout=5)
        resp.raise_for_status()
        snapshots = resp.json()
    except Exception as exc:
        return False, f"failed to fetch snapshots: {exc}"

    if not snapshots:
        return False, "no snapshots found"

    nonzero = []
    for snap in snapshots:
        summary = snap.get("result_summary") or {}
        net_pnl = summary.get("net_pnl", 0.0)
        if net_pnl != 0.0:
            nonzero.append(
                f"{snap.get('model_id', '?')}: net_pnl={net_pnl:.4f}"
            )

    if not nonzero:
        return False, f"{len(snapshots)} snapshots but all have net_pnl=0.0"

    return True, f"{len(nonzero)}/{len(snapshots)} snapshots with non-zero PnL: {'; '.join(nonzero[:5])}"


# --- Run all milestones ---

MILESTONES = [
    ("types_correct", check_types),
    ("trading_config_present", check_trading_config),
    ("examples_exist", check_examples),
    ("tests_pass", check_tests),
    ("deploy_succeeded", check_deploy),
    ("e2e_verified", check_e2e),
    ("pnl_nonzero", check_pnl),
]


def run_all(workspace: str) -> dict[str, dict]:
    """Run all milestone checks, return results dict."""
    results = {}
    for name, check_fn in MILESTONES:
        try:
            passed, details = check_fn(workspace)
        except Exception as e:
            passed, details = False, f"Exception: {e}"
        results[name] = {"passed": passed, "details": details}
        status = "\u2705" if passed else "\u274c"
        print(f"  {status} {name}: {details[:120]}")
    return results

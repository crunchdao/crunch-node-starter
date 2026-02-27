"""Milestone verification — checks the workspace independently of the agent.

Each check_* function returns (passed: bool, details: str).
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import subprocess
import sys
import types

from tests.benchmark.spec import (
    EXPECTED_EXAMPLES,
    EXPECTED_OUTPUT_FIELDS,
    SCORING_TEST_CASES,
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


def _load_module_from_file(path: str, module_name: str) -> types.ModuleType:
    """Import a Python file as a module."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- M1: Types correct ---


def check_types(workspace: str) -> tuple[bool, str]:
    """Check InferenceOutput has direction:str and confidence:float."""
    config_path = os.path.join(workspace, "node", "config", "crunch_config.py")
    if not os.path.exists(config_path):
        return False, f"crunch_config.py not found at {config_path}"

    with open(config_path) as f:
        source = f.read()

    # Parse AST to find InferenceOutput class or assignment
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return False, f"SyntaxError in crunch_config.py: {e}"

    # Look for class definition with the expected fields
    found_fields: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            # Check class names that look like InferenceOutput or output types
            name_lower = node.name.lower()
            if "output" in name_lower or "inference" in name_lower:
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(
                        item.target, ast.Name
                    ):
                        field_name = item.target.id
                        # Extract annotation as string
                        ann_str = ast.dump(item.annotation)
                        found_fields[field_name] = ann_str

    if not found_fields:
        # Fallback: regex search
        direction_match = re.search(r"direction\s*:\s*(str|Literal)", source)
        confidence_match = re.search(r"confidence\s*:\s*float", source)
        if direction_match and confidence_match:
            return True, "direction: str, confidence: float (regex match)"
        return False, (
            f"InferenceOutput fields not found. "
            f"Searched for: {list(EXPECTED_OUTPUT_FIELDS.keys())}"
        )

    missing = []
    for field in EXPECTED_OUTPUT_FIELDS:
        if field not in found_fields:
            missing.append(field)

    if missing:
        return (
            False,
            f"Missing fields in output type: {missing}. Found: {list(found_fields.keys())}",
        )

    return True, f"Found fields: {list(found_fields.keys())}"


# --- M2: Scoring implemented ---


def check_scoring(workspace: str) -> tuple[bool, str]:
    """Import scoring.py, call with test cases, verify correct signs."""
    scoring_path = os.path.join(
        workspace, "challenge", "starter_challenge", "scoring.py"
    )
    if not os.path.exists(scoring_path):
        return False, f"scoring.py not found at {scoring_path}"

    try:
        mod = _load_module_from_file(scoring_path, "benchmark_scoring_check")
    except Exception as e:
        return False, f"Failed to import scoring.py: {e}"

    score_fn = getattr(mod, "score_prediction", None)
    if score_fn is None:
        return False, "score_prediction function not found in scoring.py"

    results = []
    for prediction, ground_truth, expected_sign in SCORING_TEST_CASES:
        try:
            result = score_fn(prediction, ground_truth)
        except Exception as e:
            return False, f"score_prediction raised: {e}"

        if not isinstance(result, dict):
            return False, f"score_prediction returned {type(result)}, expected dict"

        value = result.get("value")
        if value is None:
            return False, "score_prediction result missing 'value' key"

        if expected_sign == "positive" and value <= 0:
            results.append(f"FAIL: expected positive, got {value}")
        elif expected_sign == "negative" and value >= 0:
            results.append(f"FAIL: expected negative, got {value}")
        else:
            results.append(f"OK: {expected_sign}={value:.6f}")

    failures = [r for r in results if r.startswith("FAIL")]
    detail = "; ".join(results)

    if failures:
        return False, detail
    return True, detail


# --- M3: Examples exist ---


def check_examples(workspace: str) -> tuple[bool, str]:
    """Check that expected example tracker files exist and have predict()."""
    examples_dir = os.path.join(workspace, "challenge", "starter_challenge", "examples")
    if not os.path.isdir(examples_dir):
        return False, f"examples directory not found at {examples_dir}"

    found = []
    missing = []

    for filename in EXPECTED_EXAMPLES:
        filepath = os.path.join(examples_dir, filename)
        if not os.path.exists(filepath):
            missing.append(filename)
            continue

        with open(filepath) as f:
            source = f.read()

        # Check for predict method
        if "def predict" not in source:
            missing.append(f"{filename} (no predict method)")
            continue

        # Check output shape — look for "direction" in the return
        if "direction" not in source:
            missing.append(f"{filename} (no 'direction' in output)")
            continue

        found.append(filename)

    if missing:
        return False, f"Missing/invalid: {missing}. Found: {found}"

    return True, f"{len(found)}/{len(EXPECTED_EXAMPLES)} found, all have correct shape"


# --- M4: Tests pass ---


def check_tests(workspace: str) -> tuple[bool, str]:
    """Run make test and check exit code."""
    try:
        result = _run("make test", cwd=workspace, timeout=120)
    except subprocess.TimeoutExpired:
        return False, "make test timed out (120s)"

    if result.returncode == 0:
        return True, "exit_code=0"

    # Extract last 20 lines of output for context
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    tail = "\n".join(output.strip().splitlines()[-20:])
    return False, f"exit_code={result.returncode}\n{tail}"


# --- M5: Deploy succeeded ---


def check_deploy(workspace: str) -> tuple[bool, str]:
    """Check docker containers are running."""
    try:
        result = _run(
            "docker compose -f docker-compose.yml --env-file .local.env ps --format json",
            cwd=workspace,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False, "docker compose ps timed out"

    if result.returncode != 0:
        return False, f"docker compose ps failed: {result.stderr[:200]}"

    output = result.stdout.strip()
    if not output:
        return False, "no containers found"

    # Count running containers
    import json

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


# --- M6: E2E verified ---


def check_e2e(workspace: str) -> tuple[bool, str]:
    """Run make verify-e2e and check exit code."""
    try:
        result = _run("make verify-e2e", cwd=workspace, timeout=300)
    except subprocess.TimeoutExpired:
        return False, "make verify-e2e timed out (300s)"

    output = (result.stdout or "") + "\n" + (result.stderr or "")

    if result.returncode == 0:
        # Extract summary if present
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


# --- Run all milestones ---

MILESTONES = [
    ("types_correct", check_types),
    ("scoring_implemented", check_scoring),
    ("examples_exist", check_examples),
    ("tests_pass", check_tests),
    ("deploy_succeeded", check_deploy),
    ("e2e_verified", check_e2e),
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
        status = "✅" if passed else "❌"
        print(f"  {status} {name}: {details[:120]}")
    return results

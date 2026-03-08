"""Milestone verification — checks the workspace independently of the agent.

Each check_* function returns (passed: bool, details: str).
The workspace is the root scaffold directory (contains node/, challenge/, Makefile).

Milestones:
- M1: Types correct (InferenceOutput with direction:str, confidence:float)
- M1b: GroundTruth type (profit:float, direction_up:bool with defaults)
- M2: Scoring implemented (score_prediction function works)
- M3: Examples exist (tracker files with predict methods)
- M4: Tests pass (make test succeeds)
- M5: Deploy succeeded (Docker containers running)
- M6: E2E verified (make verify-e2e passes)
- M7: Metrics collection verified (/timing-metrics endpoint shows pipeline activity)
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import types
import urllib.error
import urllib.request

from tests.benchmark.spec import (
    EXPECTED_EXAMPLES,
    EXPECTED_GROUND_TRUTH_FIELDS,
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


def _node_dir(workspace: str) -> str:
    """Return the node/ subdirectory path."""
    return os.path.join(workspace, "node")


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


# --- M1b: GroundTruth type has profit + direction_up with defaults ---


def check_ground_truth_type(workspace: str) -> tuple[bool, str]:
    """Check that a GroundTruth type defines profit:float and direction_up:bool with defaults.

    The score worker dry-runs scoring at startup using GroundTruth() defaults.
    If the GroundTruth type doesn't have these fields (with defaults), the scoring
    function raises a KeyError and the worker crashes in a restart loop.
    """
    config_path = os.path.join(workspace, "node", "config", "crunch_config.py")
    if not os.path.exists(config_path):
        return False, f"crunch_config.py not found at {config_path}"

    with open(config_path) as f:
        source = f.read()

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return False, f"SyntaxError in crunch_config.py: {e}"

    # Find any class that looks like a ground truth type
    gt_fields: dict[str, bool] = {}  # field_name → has_default

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            name_lower = node.name.lower()
            if "ground" in name_lower or "truth" in name_lower or "gt" == name_lower:
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(
                        item.target, ast.Name
                    ):
                        has_default = item.value is not None
                        gt_fields[item.target.id] = has_default

    if not gt_fields:
        # Fallback: regex — look for profit and direction_up field definitions
        profit_match = re.search(r"profit\s*:\s*float\s*=", source)
        dir_match = re.search(r"direction_up\s*:\s*bool\s*=", source)
        if profit_match and dir_match:
            return True, "profit: float, direction_up: bool with defaults (regex match)"
        return False, (
            "No GroundTruth class found with profit/direction_up fields. "
            "The score worker dry-runs scoring at startup using GroundTruth() defaults — "
            "without these fields the scoring function will KeyError and crash."
        )

    missing = []
    no_default = []
    for field in EXPECTED_GROUND_TRUTH_FIELDS:
        if field not in gt_fields:
            missing.append(field)
        elif not gt_fields[field]:
            no_default.append(field)

    if missing:
        return (
            False,
            f"Missing fields in GroundTruth: {missing}. Found: {list(gt_fields.keys())}",
        )
    if no_default:
        return (
            False,
            f"Fields without defaults in GroundTruth: {no_default}. "
            f"The score worker constructs GroundTruth() at startup for dry-run validation — "
            f"all fields need defaults.",
        )

    return True, f"Found fields with defaults: {list(gt_fields.keys())}"


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

    # Create test-specific types that match the SCORING_TEST_CASES format
    # This ensures the coerced objects have the exact fields the test expects
    try:
        from pydantic import BaseModel, ConfigDict

        class TestInferenceOutput(BaseModel):
            model_config = ConfigDict(extra="allow")
            direction: str = "hold"
            confidence: float = 0.0

        class TestGroundTruth(BaseModel):
            model_config = ConfigDict(extra="allow")
            profit: float = 0.0
            direction_up: bool = True

        output_type = TestInferenceOutput
        ground_truth_type = TestGroundTruth
    except Exception as e:
        return False, f"Failed to create test types: {e}"

    # Use the same coercion logic that production ScoreService uses

    def coerce_output(raw_dict):
        """Same logic as ScoreService._coerce_output"""
        try:
            return output_type.model_validate(raw_dict)
        except Exception:
            try:
                return output_type.model_construct(**raw_dict)
            except Exception:
                return output_type()

    def coerce_ground_truth(raw_dict):
        """Same logic as ScoreService._coerce_ground_truth"""
        try:
            return ground_truth_type.model_validate(raw_dict)
        except Exception:
            try:
                return ground_truth_type.model_construct(**raw_dict)
            except Exception:
                return ground_truth_type()

    results = []
    for prediction, ground_truth, expected_sign in SCORING_TEST_CASES:
        # Use the same Pydantic model coercion that production uses
        pred_obj = coerce_output(prediction)
        gt_obj = coerce_ground_truth(ground_truth)

        try:
            result = score_fn(pred_obj, gt_obj)
        except Exception as e:
            return False, f"score_prediction raised: {e}"

        if not isinstance(result, dict):
            # Handle Pydantic model returns
            if hasattr(result, "model_dump"):
                result = result.model_dump()
            elif hasattr(result, "__dict__"):
                result = vars(result)
            else:
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
    """Run make test (from workspace root, proxies to challenge/)."""
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
    """Check docker containers are running (docker-compose is in node/)."""
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


def _ensure_deploy(workspace: str) -> tuple[bool, str]:
    """Ensure containers are running. Skips if already healthy.

    First checks if key services are already up (agent may have deployed).
    Only runs `docker compose up -d` if not enough containers are found.
    """
    node = _node_dir(workspace)
    env_file = os.path.join(node, ".local.env")
    compose_file = os.path.join(node, "docker-compose.yml")

    if not os.path.exists(env_file) or not os.path.exists(compose_file):
        return False, "missing .local.env or docker-compose.yml"

    # Check if containers are already running
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

    # Not enough containers — try to bring them up
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
    """Run make verify-e2e (from workspace root, proxies to node/).

    If the agent was killed mid-deploy, attempts to complete deployment first.
    Uses a generous timeout — the pipeline needs time to bootstrap
    (build models, start feeds, accumulate predictions, score them).
    """
    # Ensure deployment is complete before verifying
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


# --- M7: Metrics collection verified ---


def check_metrics_collection(workspace: str) -> tuple[bool, str]:
    """Verify timing metrics are collected during tournament operation.

    Checks that:
    1. /timing-metrics endpoint is accessible
    2. Metrics collection is enabled
    3. Pipeline activity has generated timing records
    4. Key pipeline stages are instrumented
    5. Recent timing samples are available
    """
    # Determine API URL (use default from docker-compose)
    api_url = "http://localhost:8000"

    # 1. Check if timing metrics endpoint is accessible
    try:
        req = urllib.request.Request(f"{api_url}/timing-metrics")
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 404:
                return (
                    False,
                    "Timing endpoint disabled (HTTP 404) — set timing_endpoint_enabled=True in PerformanceConfig",
                )
            elif response.status != 200:
                return False, f"Timing endpoint error: HTTP {response.status}"

            response_data = response.read().decode("utf-8")

    except urllib.error.URLError as e:
        return False, f"Cannot reach /timing-metrics: {e}"
    except Exception as e:
        return False, f"Request failed: {e}"

    # 2. Parse response
    try:
        data = json.loads(response_data)
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON response: {e}"

    # 3. Check if metrics collection is enabled
    if not data.get("enabled", False):
        return (
            False,
            "Timing metrics collection disabled — set timing_enabled=True in PerformanceConfig",
        )

    # 4. Check if any records were collected
    total_records = data.get("total_records", 0)
    if total_records == 0:
        return False, "No timing records collected — pipeline may not be active yet"

    # 5. Check for key pipeline stages (stage_latencies is a list of dicts)
    stage_latencies = data.get("stage_latencies", [])
    expected_stages = ["feed_ingestion", "model_execution", "prediction_persistence"]

    # Convert list to dict for easier lookup
    stages_by_name = {s.get("name"): s for s in stage_latencies if isinstance(s, dict)}

    missing_stages = [stage for stage in expected_stages if stage not in stages_by_name]

    # Allow partial coverage initially - some stages may not be hit yet
    if len(stages_by_name) == 0:
        return False, "No pipeline stages instrumented"

    if missing_stages and len(stages_by_name) < 2:
        return (
            False,
            f"Too few pipeline stages: {list(stages_by_name.keys())}. Expected some of: {expected_stages}",
        )

    # 6. Basic sanity checks on timing data
    issues = []
    valid_stages = 0
    for stage, stats in stages_by_name.items():
        count = stats.get("count", 0)
        mean_us = stats.get("mean_us", 0)

        if count == 0:
            issues.append(f"{stage}=0_records")
        elif mean_us is None or mean_us <= 0:
            issues.append(f"{stage}=invalid_mean")
        elif mean_us > 30_000_000:  # 30 seconds - very generous threshold
            issues.append(f"{stage}=suspicious_latency({mean_us:.0f}μs)")
        else:
            valid_stages += 1

    if valid_stages == 0:
        return False, f"No valid timing data. Issues: {'; '.join(issues)}"

    # 7. Check recent samples exist
    recent_samples = data.get("recent_samples", [])
    if len(recent_samples) == 0:
        return False, "No recent timing samples available"

    # Success - format summary
    buffer_size = data.get("buffer_size", 0)
    stage_summary = ", ".join(
        [
            f"{s.get('name')}({s.get('count', 0)})"
            for s in stage_latencies
            if isinstance(s, dict)
        ]
    )

    return (
        True,
        f"Metrics: {total_records}/{buffer_size} records, stages: {stage_summary}, {len(recent_samples)} recent samples",
    )


# --- Run all milestones ---

MILESTONES = [
    ("types_correct", check_types),
    ("ground_truth_type", check_ground_truth_type),
    ("scoring_implemented", check_scoring),
    ("examples_exist", check_examples),
    ("tests_pass", check_tests),
    ("deploy_succeeded", check_deploy),
    ("e2e_verified", check_e2e),
    ("metrics_collection_verified", check_metrics_collection),
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

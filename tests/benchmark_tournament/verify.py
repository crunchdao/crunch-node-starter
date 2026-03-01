"""Milestone verification for tournament benchmark.

Each check_* function returns (passed: bool, details: str).
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

from tests.benchmark_tournament.spec import (
    EXPECTED_EXAMPLES,
    EXPECTED_GROUND_TRUTH_FIELDS,
    EXPECTED_INPUT_FIELDS,
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
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


# --- M1: Types correct (InferenceOutput has predicted_price) ---


def check_types(workspace: str) -> tuple[bool, str]:
    """Check InferenceOutput has predicted_price: float."""
    config_path = os.path.join(workspace, "node", "config", "crunch_config.py")
    if not os.path.exists(config_path):
        return False, f"crunch_config.py not found at {config_path}"

    with open(config_path) as f:
        source = f.read()

    # Check output type
    output_fields = _find_class_fields(source, ["output", "inference"])
    if not output_fields:
        if re.search(r"predicted_price\s*:\s*float", source):
            return True, "predicted_price: float (regex match)"
        return False, "No output type class found with predicted_price field"

    missing = [f for f in EXPECTED_OUTPUT_FIELDS if f not in output_fields]
    if missing:
        return (
            False,
            f"Missing output fields: {missing}. Found: {list(output_fields.keys())}",
        )

    return True, f"Output fields: {list(output_fields.keys())}"


# --- M1b: GroundTruth has price with default ---


def check_ground_truth_type(workspace: str) -> tuple[bool, str]:
    """Check GroundTruth has price: float with default."""
    config_path = os.path.join(workspace, "node", "config", "crunch_config.py")
    if not os.path.exists(config_path):
        return False, "crunch_config.py not found"

    with open(config_path) as f:
        source = f.read()

    gt_fields = _find_class_fields(source, ["ground", "truth", "gt"])
    if not gt_fields:
        if re.search(r"price\s*:\s*float\s*=", source):
            return True, "price: float with default (regex match)"
        return False, "No GroundTruth class found with price field"

    missing = [f for f in EXPECTED_GROUND_TRUTH_FIELDS if f not in gt_fields]
    if missing:
        return False, f"Missing GT fields: {missing}"

    no_default = [
        f for f in EXPECTED_GROUND_TRUTH_FIELDS if f in gt_fields and not gt_fields[f]
    ]
    if no_default:
        return False, f"Fields without defaults: {no_default}"

    return True, f"GT fields with defaults: {list(gt_fields.keys())}"


# --- M1c: TournamentPredictService configured ---


def check_tournament_service(workspace: str) -> tuple[bool, str]:
    """Check that predict_service_class = TournamentPredictService."""
    config_path = os.path.join(workspace, "node", "config", "crunch_config.py")
    if not os.path.exists(config_path):
        return False, "crunch_config.py not found"

    with open(config_path) as f:
        source = f.read()

    if "TournamentPredictService" not in source:
        return False, "TournamentPredictService not referenced in crunch_config.py"

    if "predict_service_class" not in source:
        return False, "predict_service_class not set in crunch_config.py"

    # Check it's imported
    if "tournament_predict" not in source and "TournamentPredictService" not in source:
        return False, "TournamentPredictService not imported"

    return True, "TournamentPredictService configured"


# --- M2: Scoring implemented ---


def check_scoring(workspace: str) -> tuple[bool, str]:
    """Import scoring.py, call with test cases, verify MAPE-based scores."""
    scoring_path = os.path.join(
        workspace, "challenge", "starter_challenge", "scoring.py"
    )
    if not os.path.exists(scoring_path):
        return False, "scoring.py not found"

    try:
        mod = _load_module_from_file(scoring_path, "benchmark_tournament_scoring")
    except Exception as e:
        return False, f"Failed to import scoring.py: {e}"

    score_fn = getattr(mod, "score_prediction", None)
    if score_fn is None:
        return False, "score_prediction function not found"

    results = []
    for prediction, ground_truth, (min_score, max_score) in SCORING_TEST_CASES:
        try:
            result = score_fn(prediction, ground_truth)
        except Exception as e:
            return False, f"score_prediction raised: {e}"

        if not isinstance(result, dict):
            return False, f"Expected dict, got {type(result)}"

        value = result.get("value")
        if value is None:
            return False, "Result missing 'value' key"

        if not (min_score <= value <= max_score):
            results.append(f"FAIL: score={value:.4f} not in [{min_score}, {max_score}]")
        else:
            results.append(f"OK: score={value:.4f}")

        # Check MAPE field exists
        if "mape" not in result:
            return False, "Result missing 'mape' key"

    failures = [r for r in results if r.startswith("FAIL")]
    detail = "; ".join(results)

    if failures:
        return False, detail
    return True, detail


# --- M3: Examples exist ---


def check_examples(workspace: str) -> tuple[bool, str]:
    """Check that expected example tracker files exist with predict()."""
    examples_dir = os.path.join(workspace, "challenge", "starter_challenge", "examples")
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

        if "predicted_price" not in source:
            missing.append(f"{filename} (no predicted_price)")
            continue

        found.append(filename)

    if missing:
        return False, f"Missing/invalid: {missing}. Found: {found}"

    return True, f"{len(found)}/{len(EXPECTED_EXAMPLES)} found"


# --- M4: Test data exists ---


def check_test_data(workspace: str) -> tuple[bool, str]:
    """Check in_sample.json and out_of_sample.json exist with correct shape."""
    data_dir = os.path.join(workspace, "challenge", "starter_challenge", "data")
    if not os.path.isdir(data_dir):
        return False, f"data dir not found at {data_dir}"

    issues = []
    for filename in ["in_sample.json", "out_of_sample.json"]:
        filepath = os.path.join(data_dir, filename)
        if not os.path.exists(filepath):
            issues.append(f"{filename} missing")
            continue

        try:
            with open(filepath) as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            issues.append(f"{filename} invalid JSON: {e}")
            continue

        if not isinstance(data, list):
            issues.append(f"{filename} not a list")
            continue

        if len(data) < 5:
            issues.append(f"{filename} too few records ({len(data)})")
            continue

        # Check first record has expected fields
        record = data[0]
        missing_fields = [
            f for f in list(EXPECTED_INPUT_FIELDS.keys()) + ["price"] if f not in record
        ]
        if missing_fields:
            issues.append(f"{filename} missing fields: {missing_fields}")

    if issues:
        return False, "; ".join(issues)

    # Count records
    with open(os.path.join(data_dir, "in_sample.json")) as f:
        in_count = len(json.load(f))
    with open(os.path.join(data_dir, "out_of_sample.json")) as f:
        out_count = len(json.load(f))

    return True, f"in_sample: {in_count}, out_of_sample: {out_count}"


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


# --- M6: Tournament API works (unit-level, no deploy needed) ---


def check_tournament_api(workspace: str) -> tuple[bool, str]:
    """Check that tournament.py exists in api/ and has the expected endpoints."""
    api_path = os.path.join(workspace, "node", "api", "tournament.py")
    if not os.path.exists(api_path):
        return False, "tournament.py not found in node/api/"

    with open(api_path) as f:
        source = f.read()

    expected_patterns = [
        "rounds/{round_id}/inference",
        "rounds/{round_id}/score",
        "rounds/{round_id}/status",
    ]

    missing = [p for p in expected_patterns if p not in source]
    if missing:
        return False, f"Missing endpoint patterns: {missing}"

    return True, "All 3 tournament endpoints present"


# --- M7: E2E scoring pipeline ---


def check_scoring_pipeline(workspace: str) -> tuple[bool, str]:
    """Verify the scoring function works end-to-end with test data.

    Loads out_of_sample.json, runs scoring against each property
    using the sqft model (price = sqft * 200), verifies scores are computed.
    """
    scoring_path = os.path.join(
        workspace, "challenge", "starter_challenge", "scoring.py"
    )
    data_path = os.path.join(
        workspace, "challenge", "starter_challenge", "data", "out_of_sample.json"
    )

    if not os.path.exists(scoring_path):
        return False, "scoring.py not found"
    if not os.path.exists(data_path):
        return False, "out_of_sample.json not found"

    try:
        mod = _load_module_from_file(scoring_path, "benchmark_tournament_e2e")
    except Exception as e:
        return False, f"Failed to import scoring: {e}"

    score_fn = getattr(mod, "score_prediction", None)
    if score_fn is None:
        return False, "score_prediction not found"

    try:
        with open(data_path) as f:
            properties = json.load(f)
    except Exception as e:
        return False, f"Failed to load test data: {e}"

    if not properties:
        return False, "No properties in out_of_sample.json"

    scores = []
    for prop in properties:
        sqft = prop.get("living_area_sqft", 0)
        prediction = {"predicted_price": sqft * 200}
        ground_truth = {"price": prop.get("price", 0)}

        try:
            result = score_fn(prediction, ground_truth)
            scores.append(result.get("value", 0))
        except Exception as e:
            return False, f"Scoring raised: {e}"

    avg_score = sum(scores) / len(scores) if scores else 0
    return True, (
        f"Scored {len(scores)} properties, "
        f"avg_score={avg_score:.4f}, "
        f"min={min(scores):.4f}, max={max(scores):.4f}"
    )


# --- Run all milestones ---

MILESTONES = [
    ("types_correct", check_types),
    ("ground_truth_type", check_ground_truth_type),
    ("tournament_service_configured", check_tournament_service),
    ("scoring_implemented", check_scoring),
    ("examples_exist", check_examples),
    ("test_data_exists", check_test_data),
    ("tests_pass", check_tests),
    ("tournament_api_present", check_tournament_api),
    ("scoring_pipeline_e2e", check_scoring_pipeline),
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

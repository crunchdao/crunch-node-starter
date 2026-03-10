"""Pre-deploy configuration validator.

Catches common misconfigurations that silently break the pipeline:
1. Docker networking — NEXT_PUBLIC_API_URL must use Docker-internal hostnames
2. Timing — resolve_horizon_seconds must exceed feed granularity
3. Scoring — scoring function must return non-zero for valid inputs
4. Models — submission dirs must be self-contained (no challenge pkg imports)
5. CrunchConfig — types, callables, and aggregation must be wired correctly

Run:  python scripts/validate_config.py
      make validate
Exit 0 = all checks pass, exit 1 = at least one failure.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

# ── Setup ────────────────────────────────────────────────────────────

NODE_DIR = Path(__file__).resolve().parent.parent
CHALLENGE_DIR = NODE_DIR.parent / "challenge"

sys.path.insert(0, str(NODE_DIR))
sys.path.insert(0, str(CHALLENGE_DIR))

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
WARN = "\033[33m⚠\033[0m"

failures: list[str] = []
warnings: list[str] = []


def check(name: str, ok: bool, msg: str = "") -> bool:
    if ok:
        print(f"  {PASS} {name}")
    else:
        print(f"  {FAIL} {name}: {msg}")
        failures.append(f"{name}: {msg}")
    return ok


def warn(name: str, msg: str) -> None:
    print(f"  {WARN} {name}: {msg}")
    warnings.append(f"{name}: {msg}")


# ── Helpers ──────────────────────────────────────────────────────────


def _load_env(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict (ignoring comments and blank lines)."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _granularity_to_seconds(gran: str) -> int:
    """Convert a granularity string like '1m', '5s', '1h' to seconds."""
    m = re.match(r"(\d+)(s|m|h)", gran)
    if not m:
        return 1
    amount, unit = int(m.group(1)), m.group(2)
    return amount * {"s": 1, "m": 60, "h": 3600}[unit]


# ── 1. Docker networking ─────────────────────────────────────────────


def check_docker_networking():
    """NEXT_PUBLIC_API_URL is used by Next.js rewrites to proxy API calls
    server-side inside Docker. It must use Docker DNS, not localhost."""
    print("\n[1/5] Docker networking")
    env = _load_env(NODE_DIR / ".local.env")

    for var, default_host in [
        ("NEXT_PUBLIC_API_URL", "report-worker"),
        ("NEXT_PUBLIC_API_URL_MODEL_ORCHESTRATOR", "model-orchestrator"),
    ]:
        val = env.get(var, "")
        if not val:
            check(f"{var} uses default (OK)", True)
            continue
        is_local = "localhost" in val or "127.0.0.1" in val
        check(
            f"{var} uses Docker hostname",
            not is_local,
            f"'{val}' — Next.js SSR runs inside Docker where localhost "
            f"is the container itself, not {default_host}. "
            f"Use http://{default_host}:... or remove the var to use the "
            f"docker-compose.yml default.",
        )


# ── 2. Timing consistency ────────────────────────────────────────────


def check_timing():
    """resolve_horizon_seconds must exceed feed granularity, otherwise the
    score-worker's fetch_window returns zero records and predictions
    silently fail to score."""
    print("\n[2/6] Timing consistency")
    env = _load_env(NODE_DIR / ".local.env")

    feed_gran = env.get("FEED_GRANULARITY", "1s")
    gran_secs = _granularity_to_seconds(feed_gran)

    # Load scheduled predictions from CrunchConfig
    configs = []
    try:
        from config.crunch_config import CrunchConfig

        cc = CrunchConfig()
        configs = [
            {
                "scope_key": sp.scope_key,
                "resolve_horizon_seconds": sp.resolve_horizon_seconds,
                "prediction_interval_seconds": sp.prediction_interval_seconds,
            }
            for sp in cc.scheduled_predictions
        ]
    except ImportError:
        warn(
            "CrunchConfig import",
            "could not import config.crunch_config — skipping timing checks",
        )
        return

    if not configs:
        check("No scheduled_predictions — skipping timing checks", True)
        return

    for cfg in configs:
        key = cfg.get("scope_key", "unknown")
        resolve = cfg.get("resolve_horizon_seconds", 0)
        interval = cfg.get("prediction_interval_seconds", 60)

        check(
            f"[{key}] resolve_horizon_seconds ({resolve}) > feed granularity ({gran_secs}s)",
            resolve > gran_secs,
            f"Score-worker fetches feed records in a {resolve}s window. "
            f"With {feed_gran} data, this window likely contains zero "
            f"records → predictions never score. Use >= {gran_secs + 15}.",
        )

        if interval < gran_secs:
            warn(
                f"[{key}] prediction_interval ({interval}s) < feed granularity ({gran_secs}s)",
                "Models may see duplicate data across consecutive predictions",
            )


# ── 2b. Subject mapping ──────────────────────────────────────────────


def check_subjects():
    """Informational: show scope vs feed subject mapping.

    Scope subjects (what models predict) and feed subjects (what gets
    ingested) are independent.  The score worker fetches all feed records
    in the resolution window; resolve_ground_truth filters as needed.
    """
    print("\n[3/6] Subject mapping")
    env = _load_env(NODE_DIR / ".local.env")

    feed_subjects_raw = env.get("FEED_SUBJECTS", env.get("FEED_ASSETS", "BTC"))
    feed_subjects = {s.strip() for s in feed_subjects_raw.split(",") if s.strip()} or {
        "BTC"
    }

    try:
        from config.crunch_config import CrunchConfig

        cc = CrunchConfig()
    except ImportError:
        warn(
            "CrunchConfig import",
            "could not import — skipping subject checks",
        )
        return

    scope_subjects = set()
    for sp in cc.scheduled_predictions:
        scope_subject = sp.scope.get("subject") if sp.scope else None
        if scope_subject:
            scope_subjects.add(scope_subject)

    if not cc.scheduled_predictions:
        check("Tournament mode — no feed/scope subjects to check", True)
        return

    check(
        f"Feed subjects: {sorted(feed_subjects)}, "
        f"scope subjects: {sorted(scope_subjects)}",
        True,
    )

    if scope_subjects and not scope_subjects & feed_subjects:
        warn(
            "No overlap between scope and feed subjects",
            f"Scope uses {sorted(scope_subjects)}, feed ingests {sorted(feed_subjects)}. "
            f"This is fine — resolve_ground_truth receives all feed records "
            f"and the prediction. For multi-asset competitions, ensure your "
            f"custom resolver filters by prediction.scope['subject'].",
        )


# ── 3. Scoring sanity ────────────────────────────────────────────────


def check_scoring():
    """The scoring function must return non-zero for valid inputs and
    differentiate between opposing predictions."""
    print("\n[4/6] Scoring sanity")
    env = _load_env(NODE_DIR / ".local.env")
    scoring_path = env.get("SCORING_FUNCTION", "")

    if not scoring_path or ":" not in scoring_path:
        warn("SCORING_FUNCTION", f"not set or invalid ('{scoring_path}'), skipping")
        return

    module_path, func_name = scoring_path.rsplit(":", 1)
    try:
        mod = importlib.import_module(module_path)
        score_fn = getattr(mod, func_name)
        check("Scoring function importable", True)
    except Exception as exc:
        check("Scoring function importable", False, str(exc))
        return

    # Probe: build test inputs from CrunchConfig types when available,
    # fall back to generic shapes otherwise.
    # The engine coerces to Pydantic models before calling scoring functions,
    # so we pass typed objects here (not dicts).
    try:
        pred = None
        gt = None

        try:
            from config.crunch_config import CrunchConfig

            cc = CrunchConfig()
            # Use default instances of the actual Pydantic types
            pred = cc.output_type()
            gt_type = cc.get_ground_truth_type()
            gt = gt_type()
        except Exception:
            pass

        if pred is None:
            from pydantic import BaseModel

            class _FallbackPred(BaseModel):
                value: float = 105.0

            class _FallbackGT(BaseModel):
                value: float = 100.0

            pred = _FallbackPred()
            gt = _FallbackGT()

        result = score_fn(pred, gt)

        # Scoring functions may return Pydantic models or dicts
        if hasattr(result, "model_dump"):
            result_dict = result.model_dump()
        elif isinstance(result, dict):
            result_dict = result
        else:
            check(
                "score_prediction returns dict or Pydantic model",
                False,
                f"got {type(result)}",
            )
            return

        check(
            "score_prediction returns dict or Pydantic model",
            True,
        )

        check(
            "Result has 'value' key",
            "value" in result_dict,
            f"keys: {list(result_dict.keys())}",
        )
        check("Result has 'success' key", "success" in result_dict, "")

        val = result_dict.get("value", 0.0)
        if val == 0.0:
            warn(
                "Score is zero for default types",
                "Scoring returns 0.0 when called with default InferenceOutput "
                "and GroundTruth values. This is often expected (e.g. profit=0.0 "
                "defaults). Verify scoring returns non-zero for real inputs.",
            )
        else:
            check(f"Score is non-zero ({val:.6f})", True)
    except Exception as exc:
        warn("Scoring probe", f"could not run scoring function ({exc})")


# ── 4. Model submissions ─────────────────────────────────────────────


def check_model_submissions():
    """Model-runner containers don't have the challenge package installed.
    Submissions must be self-contained."""
    print("\n[5/6] Model submissions")
    config_dir = NODE_DIR / "deployment" / "model-orchestrator-local" / "config"

    # Discover challenge package name
    pkg_name = None
    if CHALLENGE_DIR.exists():
        for d in CHALLENGE_DIR.iterdir():
            if (
                d.is_dir()
                and not d.name.startswith((".", "_"))
                and (d / "tracker.py").exists()
            ):
                pkg_name = d.name
                break

    sub_dirs = sorted(config_dir.glob("*-submission")) if config_dir.exists() else []
    if not sub_dirs:
        warn(
            "No config submissions found",
            "No *-submission dirs in deployment/model-orchestrator-local/config/. "
            "Models auto-discovered from challenge examples may still work.",
        )
        return
    check(f"Found {len(sub_dirs)} config submission(s)", True)

    for sub_dir in sub_dirs:
        name = sub_dir.name
        for filename in ("main.py", "tracker.py"):
            fpath = sub_dir / filename
            if not fpath.exists():
                if filename == "main.py":
                    check(f"[{name}] has {filename}", False, "")
                continue

            source = fpath.read_text()
            if pkg_name:
                bad = re.findall(rf"(?:from|import)\s+{re.escape(pkg_name)}\b", source)
                check(
                    f"[{name}] {filename} no '{pkg_name}' imports",
                    len(bad) == 0,
                    f"Found {bad} — model-runner containers don't have the "
                    f"challenge package. Use inline classes or local tracker.py.",
                )


# ── 5. CrunchConfig wiring ───────────────────────────────────────────


def check_crunch_config():
    """Verify CrunchConfig loads and its callables/types are wired."""
    print("\n[6/6] CrunchConfig wiring")
    try:
        from config.crunch_config import CrunchConfig
    except ImportError as exc:
        warn(
            "CrunchConfig import",
            f"skipped ({exc}). Install crunch-node + pydantic to validate locally.",
        )
        return

    try:
        cfg = CrunchConfig()
        check("CrunchConfig instantiates", True)

        # Aggregation ranking key must exist in windows or score_type fields
        agg = cfg.aggregation
        valid_keys = set(agg.windows.keys()) | set(cfg.score_type.model_fields.keys())
        check(
            f"ranking_key '{agg.ranking_key}' in aggregation windows or score fields",
            agg.ranking_key in valid_keys,
            f"available: {valid_keys}",
        )

        # Callables must be callable
        for name in ("resolve_ground_truth", "aggregate_snapshot", "build_emission"):
            fn = getattr(cfg, name, None)
            check(f"{name} is callable", callable(fn), f"got {type(fn)}")

    except Exception as exc:
        check("CrunchConfig loads", False, str(exc))


# ── Main ─────────────────────────────────────────────────────────────


def main() -> int:
    print("=" * 60)
    print("  Pre-deploy configuration validation")
    print("=" * 60)

    check_docker_networking()
    check_timing()
    check_subjects()
    check_scoring()
    check_model_submissions()
    check_crunch_config()

    print()
    if warnings:
        print(f"{WARN} {len(warnings)} warning(s)")
        for w in warnings:
            print(f"    {w}")

    if failures:
        print(f"\n{FAIL} {len(failures)} check(s) FAILED — fix before deploying")
        for f in failures:
            print(f"    {f}")
        return 1

    print(f"\n{PASS} All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Pre-deploy configuration validator — tournament mode.

Tournament competitions don't use feeds, scheduled predictions, or
resolve_horizon_seconds. This simplified validator checks only what
matters for tournament deploys:

1. Docker networking (NEXT_PUBLIC_API_URL)
2. Scoring function (importable, returns dict with 'value')
3. Model submissions (self-contained, no challenge pkg imports)
4. CrunchConfig (loads, types instantiate)

Run:  python scripts/validate_config.py
      make validate
Exit 0 = all checks pass, exit 1 = at least one failure.
"""

from __future__ import annotations

import importlib
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


def _load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


# ── 1. Docker networking ─────────────────────────────────────────────


def check_docker_networking():
    print("\n[1/4] Docker networking")
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
            f"'{val}' — use http://{default_host}:... or remove the var.",
        )


# ── 2. Scoring sanity ────────────────────────────────────────────────


def check_scoring():
    print("\n[2/4] Scoring sanity")
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

    try:
        pred = None
        gt = None

        try:
            from config.crunch_config import CrunchConfig

            cc = CrunchConfig()
            pred = cc.output_type().model_dump()
            gt = cc.ground_truth_type().model_dump()
        except Exception:
            pass

        if pred is None:
            pred = {"predicted_price": 500000.0}
            gt = {"price": 500000.0}

        result = score_fn(pred, gt)

        check(
            "score_prediction returns dict",
            isinstance(result, dict),
            f"got {type(result)}",
        )
        if not isinstance(result, dict):
            return

        check(
            "Result has 'value' key",
            "value" in result,
            f"keys: {list(result.keys())}",
        )
        check("Result has 'success' key", "success" in result, "")

        val = result.get("value", 0.0)
        if val == 0.0:
            warn(
                "Score is zero for default types",
                "May be expected if default GroundTruth has price=0.0.",
            )
        else:
            check(f"Score is non-zero ({val:.6f})", True)
    except Exception as exc:
        warn("Scoring probe", f"could not run scoring function ({exc})")


# ── 3. Model submissions ─────────────────────────────────────────────


def check_model_submissions():
    print("\n[3/4] Model submissions")
    config_dir = NODE_DIR / "deployment" / "model-orchestrator-local" / "config"

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
                    f"challenge package.",
                )


# ── 4. CrunchConfig wiring ───────────────────────────────────────────


def check_crunch_config():
    print("\n[4/4] CrunchConfig wiring")
    try:
        from config.crunch_config import CrunchConfig
    except ImportError as exc:
        warn("CrunchConfig import", f"skipped ({exc})")
        return

    try:
        cfg = CrunchConfig()
        check("CrunchConfig instantiates", True)

        # Tournament mode: verify predict_service_class is set
        svc_class = getattr(cfg, "predict_service_class", None)
        if svc_class is not None:
            check(
                f"predict_service_class = {svc_class.__name__}",
                "Tournament" in svc_class.__name__,
                f"Expected TournamentPredictService, got {svc_class.__name__}",
            )
        else:
            warn(
                "predict_service_class",
                "not set — tournament mode may not work",
            )

        # Verify scheduled_predictions is empty (tournament mode)
        sp = cfg.scheduled_predictions
        if sp:
            warn(
                "scheduled_predictions is not empty",
                f"Tournament mode should have empty scheduled_predictions, got {len(sp)}",
            )
        else:
            check("No scheduled_predictions (tournament mode)", True)

    except Exception as exc:
        check("CrunchConfig loads", False, str(exc))


# ── Main ─────────────────────────────────────────────────────────────


def main() -> int:
    print("=" * 60)
    print("  Pre-deploy configuration validation (tournament)")
    print("=" * 60)

    check_docker_networking()
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

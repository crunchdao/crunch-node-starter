#!/usr/bin/env python3
"""Auto-discover challenge examples and register them as orchestrator submissions.

Scans /app/challenge for a Python package that contains both ``cruncher.py``
(the participant base class) and an ``examples/`` directory.  Each example
file becomes a standalone orchestrator submission with rewritten imports so
it runs without the challenge package installed.

Operator submissions placed in ``/app/config/*-submission/`` are also synced
and merged into the generated model registry.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

CHALLENGE_DIR = Path("/app/challenge")
CONFIG_DIR = Path("/app/config")
DATA_DIR = Path("/app/data")
SUBMISSIONS_DIR = DATA_DIR / "submissions"
MODELS_FILE = DATA_DIR / "models.dev.yml"
CRUNCH_ID = os.environ.get("CRUNCH_ID", "starter-challenge")


def find_challenge_package() -> tuple[Path, str] | None:
    """Find the challenge package (directory with cruncher.py + examples/)."""
    if not CHALLENGE_DIR.is_dir():
        return None
    for pkg_dir in sorted(CHALLENGE_DIR.iterdir()):
        if not pkg_dir.is_dir() or pkg_dir.name.startswith((".", "_")):
            continue
        if (pkg_dir / "cruncher.py").exists() and (pkg_dir / "examples").is_dir():
            return pkg_dir, pkg_dir.name
    return None


def find_examples(examples_dir: Path) -> list[Path]:
    """Find example model files (Python files, excluding __init__.py)."""
    return sorted(
        p
        for p in examples_dir.glob("*.py")
        if p.name != "__init__.py" and not p.name.startswith("_")
    )


def rewrite_imports(source: str, package_name: str) -> str:
    """Rewrite challenge package imports to local imports.

    ``from starter_challenge.cruncher import BaseModelClass``
    → ``from cruncher import BaseModelClass``
    """
    # from <package>.cruncher import X → from cruncher import X
    source = re.sub(
        rf"from\s+{re.escape(package_name)}\.cruncher\s+import",
        "from cruncher import",
        source,
    )
    # from <package> import X → from cruncher import X
    source = re.sub(
        rf"from\s+{re.escape(package_name)}\s+import",
        "from cruncher import",
        source,
    )
    return source


def submission_id_from_filename(filename: str) -> str:
    """mean_reversion_tracker.py → mean-reversion-tracker"""
    return filename.removesuffix(".py").replace("_", "-")


def create_submission(
    example_path: Path, cruncher_path: Path, package_name: str
) -> str:
    """Create a standalone submission directory from a challenge example."""
    sub_id = submission_id_from_filename(example_path.name)
    sub_dir = SUBMISSIONS_DIR / sub_id
    sub_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(cruncher_path, sub_dir / "cruncher.py")

    # Copy and rewrite example as main.py
    source = example_path.read_text()
    (sub_dir / "main.py").write_text(rewrite_imports(source, package_name))

    # Empty requirements.txt
    reqs = sub_dir / "requirements.txt"
    if not reqs.exists():
        reqs.write_text("# Auto-generated from challenge example.\n")

    return sub_id


def sync_config_submissions() -> list[str]:
    """Sync operator submissions from config/*-submission/ → data/submissions/."""
    synced = []
    for template_dir in sorted(CONFIG_DIR.glob("*-submission")):
        if not template_dir.is_dir():
            continue
        sub_id = template_dir.name
        sub_dir = SUBMISSIONS_DIR / sub_id
        sub_dir.mkdir(parents=True, exist_ok=True)
        for f in template_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, sub_dir / f.name)
        synced.append(sub_id)
        print(f"  Synced config submission: {sub_id}")
    return synced


def generate_models_yml(submission_ids: list[str]) -> None:
    """Generate models.dev.yml for the orchestrator."""
    lines = ["models:"]
    for idx, sub_id in enumerate(submission_ids, start=1):
        model_name = sub_id.removesuffix("-tracker")
        lines.extend(
            [
                f'  - id: "{idx}"',
                f"    submission_id: {sub_id}",
                f"    crunch_id: {CRUNCH_ID}",
                "    desired_state: RUNNING",
                f"    model_name: {model_name}",
                "    cruncher_name: local-dev",
                f"    cruncher_id: local-{idx:04d}",
                "",
            ]
        )

    MODELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODELS_FILE.write_text("\n".join(lines) + "\n")
    print(f"  Generated {MODELS_FILE} with {len(submission_ids)} model(s)")


def main() -> None:
    print("=== Syncing model submissions ===")

    all_submissions: list[str] = []

    # 1. Discover challenge examples
    result = find_challenge_package()
    if result:
        pkg_dir, pkg_name = result
        cruncher_path = pkg_dir / "cruncher.py"
        examples = find_examples(pkg_dir / "examples")

        if examples:
            print(f"  Found challenge package: {pkg_name} ({len(examples)} example(s))")
            for example in examples:
                sub_id = create_submission(example, cruncher_path, pkg_name)
                all_submissions.append(sub_id)
                print(f"    {example.name} → {sub_id}")
        else:
            print(f"  Challenge package '{pkg_name}' has no examples")
    else:
        print("  No challenge package found (looking for cruncher.py + examples/)")

    # 2. Sync operator submissions from config/
    config_subs = sync_config_submissions()
    all_submissions.extend(config_subs)

    # 3. Generate model registry
    if all_submissions:
        generate_models_yml(all_submissions)
    else:
        print("  WARNING: No submissions found — orchestrator will have no models")
        generate_models_yml([])

    print("=== Done ===")


if __name__ == "__main__":
    main()

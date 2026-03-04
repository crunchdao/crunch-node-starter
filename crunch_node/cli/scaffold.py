"""Scaffold workspace creation — copies template, applies pack overlay, renames.

The scaffold logic:
1. Copies ``scaffold/`` to a new directory named after the competition
2. If a pack is specified, overlays ``packs/<pack>/`` on top (overwriting matches)
3. Replaces ``starter-challenge`` → ``<name>`` and ``starter_challenge`` → ``<module>``
   in all text file contents AND in directory/file names
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# ── Skip patterns ──────────────────────────────────────────────────────
_SKIP_NAMES = {
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".DS_Store",
    ".ruff_cache",
}

# File extensions that should have text replacement applied.
_TEXT_EXTENSIONS = {
    ".py",
    ".toml",
    ".yml",
    ".yaml",
    ".md",
    ".json",
    ".env",
    ".sh",
    ".txt",
    ".cfg",
    ".ini",
    ".lock",
    ".example",
    ".disabled",
}

# Files with no dot-extension that should still be treated as text.
_TEXT_FILENAMES = {
    "Makefile",
    "Dockerfile",
    ".gitignore",
    ".gitkeep",
    ".dockerignore",
    ".prettierrc",
    ".env",
    "README",
    "LICENSE",
}


# ── Helpers ────────────────────────────────────────────────────────────


def _find_templates_dir() -> Path:
    """Locate the templates directory.

    Priority:
    1. Bundled inside the installed package (``crunch_node/_templates/``)
    2. Repo root (development mode — ``scaffold/`` and ``packs/`` at repo root)
    """
    pkg_dir = Path(__file__).resolve().parent.parent  # crunch_node/
    bundled = pkg_dir / "_templates"
    if (bundled / "scaffold").is_dir():
        return bundled

    # Development mode — repo root
    repo_root = pkg_dir.parent
    if (repo_root / "scaffold").is_dir():
        return repo_root

    raise FileNotFoundError(
        "Cannot find scaffold templates. "
        "Install crunch-node from PyPI or run from a repo checkout."
    )


def _should_skip(name: str) -> bool:
    return name in _SKIP_NAMES or name.endswith(".pyc")


def _is_text_file(path: Path) -> bool:
    """Determine whether *path* should have text replacement applied."""
    if path.suffix in _TEXT_EXTENSIONS:
        return True
    if path.name in _TEXT_FILENAMES:
        return True
    return False


def _to_snake_case(name: str) -> str:
    """Convert kebab-case to snake_case: ``my-btc-challenge`` → ``my_btc_challenge``."""
    return name.replace("-", "_")


def _copy_tree(
    src: Path,
    dst: Path,
    replacements: dict[str, str],
) -> None:
    """Recursively copy *src* to *dst*, applying text replacements and renames."""
    dst.mkdir(parents=True, exist_ok=True)

    for item in sorted(src.iterdir()):
        if _should_skip(item.name):
            continue

        # Apply name replacements (directory and file names)
        new_name = item.name
        for old, new in replacements.items():
            new_name = new_name.replace(old, new)

        dst_item = dst / new_name

        if item.is_dir():
            _copy_tree(item, dst_item, replacements)
        elif item.is_file():
            if _is_text_file(item):
                try:
                    content = item.read_text(encoding="utf-8")
                    for old, new in replacements.items():
                        content = content.replace(old, new)
                    dst_item.write_text(content, encoding="utf-8")
                except UnicodeDecodeError:
                    # Binary file mis-identified — copy as-is
                    shutil.copy2(item, dst_item)
            else:
                shutil.copy2(item, dst_item)

            # Preserve executable bit
            if os.access(item, os.X_OK):
                dst_item.chmod(dst_item.stat().st_mode | 0o111)


# ── Public API ─────────────────────────────────────────────────────────


def scaffold_workspace(
    name: str,
    pack: str | None = None,
    output_dir: str = ".",
) -> Path:
    """Create a new competition workspace.

    Args:
        name: Competition name in kebab-case (e.g. ``my-btc-challenge``).
        pack: Optional pack overlay (prediction, trading, tournament).
        output_dir: Parent directory for the workspace.

    Returns:
        Path to the created workspace directory.

    Raises:
        FileExistsError: If the destination directory already exists.
        FileNotFoundError: If scaffold templates cannot be found.
        ValueError: If the specified pack does not exist.
    """
    templates_dir = _find_templates_dir()
    scaffold_dir = templates_dir / "scaffold"
    packs_dir = templates_dir / "packs"

    module_name = _to_snake_case(name)
    dest = Path(output_dir).resolve() / name

    if dest.exists():
        raise FileExistsError(f"Directory already exists: {dest}")

    # Validate pack before copying anything.
    pack_dir: Path | None = None
    if pack:
        pack_dir = packs_dir / pack
        if not pack_dir.is_dir():
            available = [
                p.name
                for p in packs_dir.iterdir()
                if p.is_dir() and not p.name.startswith(".")
            ]
            raise ValueError(
                f"Unknown pack '{pack}'. Available: {', '.join(sorted(available))}"
            )

    # Text replacements — longer patterns first to avoid partial matches.
    replacements = {
        "starter-challenge": name,
        "starter_challenge": module_name,
    }

    # Copy scaffold base, then pack overlay.  Clean up on any failure.
    try:
        print(f"Creating workspace '{name}' from scaffold template...")
        _copy_tree(scaffold_dir, dest, replacements)

        if pack_dir:
            print(f"Applying '{pack}' pack overlay...")
            _copy_tree(pack_dir, dest, replacements)
    except BaseException:
        if dest.exists():
            shutil.rmtree(dest)
        raise

    # Summary
    print(f"\n✓ Workspace created at: {dest}")
    print(f"  CRUNCH_ID: {name}")
    print(f"  Module:    {module_name}")
    if pack:
        print(f"  Pack:      {pack}")
    print()
    print("Next steps:")
    print(f"  cd {name}")
    print("  # Edit node/config/crunch_config.py")
    print("  make deploy")
    print("  make verify-e2e")

    return dest


def list_packs() -> None:
    """Print available packs with descriptions."""
    templates_dir = _find_templates_dir()
    packs_dir = templates_dir / "packs"

    print("Available packs:\n")
    for pack_dir in sorted(packs_dir.iterdir()):
        if not pack_dir.is_dir() or pack_dir.name.startswith("."):
            continue

        # Try to extract a one-line description from crunch_config.py
        config = pack_dir / "node" / "config" / "crunch_config.py"
        desc = ""
        if config.exists():
            for line in config.read_text().splitlines()[:10]:
                stripped = line.strip()
                if stripped.startswith('"""') or stripped.startswith("#"):
                    desc = stripped.strip("#\"'- ").strip()
                    if desc:
                        break

        print(f"  {pack_dir.name:15s} {desc}")

    print()
    print("Usage: crunch-node init my-challenge --pack <pack>")

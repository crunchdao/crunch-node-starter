"""Auto-discover and mount FastAPI routers from a directory.

The report worker calls `mount_api_routers(app)` at startup. Any `.py` file
in the configured directory that exposes a `router` attribute (an `APIRouter`)
gets included in the FastAPI app automatically.

Scan directory is controlled by:
  - `API_ROUTES_DIR` env var (default: `api/`)
  - `API_ROUTES` env var — comma-separated dotted paths for explicit imports
    (e.g. `my_module.routes:router,another:router`)

Both mechanisms can be used together.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from pathlib import Path

from fastapi import APIRouter, FastAPI

logger = logging.getLogger(__name__)

DEFAULT_API_DIR = "api"


def mount_api_routers(app: FastAPI) -> int:
    """Discover and mount all API routers. Returns count of routers mounted."""
    count = 0

    # 1. Directory-based discovery
    api_dir = os.getenv("API_ROUTES_DIR", DEFAULT_API_DIR)
    count += _mount_from_directory(app, api_dir)

    # 2. Explicit dotted-path imports
    explicit = os.getenv("API_ROUTES", "")
    if explicit.strip():
        for path in explicit.split(","):
            path = path.strip()
            if path:
                count += _mount_from_path(app, path)

    if count:
        logger.info("Mounted %d custom API router(s)", count)

    return count


def _mount_from_directory(app: FastAPI, directory: str) -> int:
    """Scan a directory for .py files with a `router` attribute."""
    dir_path = Path(directory).resolve()
    if not dir_path.is_dir():
        return 0

    # Ensure the directory's parent is on sys.path so `import api.xxx` works
    parent = str(dir_path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)

    dir_name = dir_path.name

    count = 0
    for py_file in sorted(dir_path.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        module_name = f"{dir_name}.{py_file.stem}"

        # Use importlib.util to load from the exact file path,
        # avoiding conflicts with other modules named 'api'
        spec = importlib.util.spec_from_file_location(module_name, str(py_file))
        if spec is None or spec.loader is None:
            logger.warning("Cannot create import spec for %s", py_file)
            continue

        try:
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as exc:
            logger.warning("Failed to import %s: %s", py_file.name, exc)
            sys.modules.pop(module_name, None)
            continue

        router = getattr(module, "router", None)
        if router is not None and isinstance(router, APIRouter):
            app.include_router(router)
            count += 1
            logger.info(
                "Mounted router from %s (%d routes)", py_file.name, len(router.routes)
            )
        else:
            logger.debug("No router found in %s, skipping", py_file.name)

    return count


def _mount_from_path(app: FastAPI, path: str) -> int:
    """Import a specific `module:attribute` path and mount it."""
    try:
        if ":" in path:
            module_name, attr_name = path.rsplit(":", 1)
        else:
            module_name = path
            attr_name = "router"

        module = importlib.import_module(module_name)
        router = getattr(module, attr_name)

        if not isinstance(router, APIRouter):
            logger.warning("%s is not an APIRouter, skipping", path)
            return 0

        app.include_router(router)
        logger.info("Mounted router from %s (%d routes)", path, len(router.routes))
        return 1
    except Exception as exc:
        logger.warning("Failed to mount router from %s: %s", path, exc)
        return 0

"""Config loader — resolve the operator's CrunchConfig at startup.

Workers call `load_config()` instead of `CrunchConfig()`. This tries
to import the operator's customized config from `config/`,
falling back to the engine default.

Resolution order:
1. `CRUNCH_CONFIG_MODULE` env var (e.g. `my_package.config:MyConfig`)
2. `config.crunch_config:CrunchConfig` (operator override)
3. `crunch_node.crunch_config:CrunchConfig` (engine default)
"""

from __future__ import annotations

import importlib
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_cached_config: Any = None


def load_config() -> Any:
    """Load and cache the CrunchConfig instance.

    Returns a CrunchConfig (or subclass) from the first successful source.
    """
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    config = _resolve_config()
    _cached_config = config
    return config


def _resolve_config() -> Any:
    """Try each config source in priority order."""

    # 1. Explicit env var
    explicit = os.getenv("CRUNCH_CONFIG_MODULE", "").strip()
    if explicit:
        config, found = _try_load(explicit)
        if config is not None:
            logger.info("Loaded config from CRUNCH_CONFIG_MODULE=%s", explicit)
            return config
        logger.warning(
            "CRUNCH_CONFIG_MODULE=%s failed to load, trying fallbacks", explicit
        )

    # 2. Operator's config directory
    config, found = _try_load("config.crunch_config:CrunchConfig")
    if config is not None:
        logger.info("Loaded config from config.crunch_config:CrunchConfig")
        return config

    # 3. Engine default
    from crunch_node.crunch_config import CrunchConfig

    if found:
        logger.warning(
            "Operator config found at config.crunch_config but failed to "
            "instantiate — falling back to engine default CrunchConfig. "
            "Fix the validation errors above.",
        )
    else:
        logger.info("Using default CrunchConfig (no operator override found)")

    return CrunchConfig()


def _try_load(path: str) -> tuple[Any, bool]:
    """Try to import a config from a dotted path.

    Returns ``(config, found)`` where *found* is True when the module and
    attribute exist but instantiation failed (validation error, etc.).
    This lets the caller distinguish "not installed" from "broken config".

    Supports two forms:
    - ``module.path:ClassName`` — instantiates the class
    - ``module.path:INSTANCE`` — uses the object directly
    """
    try:
        if ":" in path:
            module_name, attr_name = path.rsplit(":", 1)
        else:
            module_name = path
            attr_name = "CrunchConfig"

        module = importlib.import_module(module_name)
        target = getattr(module, attr_name)

        # If it's a class, instantiate it
        if isinstance(target, type):
            return target(), True

        # If it's already an instance, use it directly
        return target, True

    except (ImportError, AttributeError):
        return None, False
    except Exception as exc:
        # Surface validation errors (e.g. Pydantic) loudly — they indicate
        # a real misconfiguration that must not be silently swallowed.
        logger.warning(
            "Failed to load config from %s: %s: %s",
            path,
            type(exc).__name__,
            exc,
        )
        return None, True


def reset_cache() -> None:
    """Clear the cached config (for testing)."""
    global _cached_config
    _cached_config = None

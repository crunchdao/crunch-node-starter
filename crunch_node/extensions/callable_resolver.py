from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable


def resolve_callable(
    path: str, required_params: tuple[str, ...] | None = None
) -> Callable:
    if ":" not in path:
        raise ValueError(
            f"Invalid callable path '{path}'. Expected '<module>:<callable>'."
        )

    module_name, attr_name = path.split(":", maxsplit=1)
    if not module_name or not attr_name:
        raise ValueError(
            f"Invalid callable path '{path}'. Expected '<module>:<callable>'."
        )

    module = importlib.import_module(module_name)
    target = getattr(module, attr_name)

    if not callable(target):
        raise ValueError(f"Resolved object '{path}' is not callable.")

    if required_params:
        _validate_signature(path=path, target=target, required_params=required_params)

    return target


def _validate_signature(
    path: str, target: Callable, required_params: tuple[str, ...]
) -> None:
    signature = inspect.signature(target)
    param_names = tuple(signature.parameters.keys())

    if len(param_names) < len(required_params):
        raise ValueError(
            f"Callable '{path}' does not define required parameters {required_params}. "
            f"Found parameters {param_names}."
        )

    if tuple(param_names[: len(required_params)]) != required_params:
        raise ValueError(
            f"Callable '{path}' must start with parameters {required_params}. "
            f"Found parameters {param_names}."
        )

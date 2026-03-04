"""Coordinator Node - Pipeline orchestration for prediction tournaments."""

try:
    from importlib.metadata import version

    __version__ = version("coordinator-node")
except Exception:
    __version__ = "0.0.0-dev"

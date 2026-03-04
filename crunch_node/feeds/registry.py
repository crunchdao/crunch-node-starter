from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from crunch_node.feeds.base import DataFeed


@dataclass(frozen=True)
class FeedSettings:
    provider: str
    options: dict[str, str] = field(default_factory=dict)


FeedFactory = Callable[[FeedSettings], DataFeed]


class DataFeedRegistry:
    """Registry/factory skeleton for runtime feed providers."""

    def __init__(self) -> None:
        self._factories: dict[str, FeedFactory] = {}

    def register(
        self, provider: str, factory: FeedFactory, *, replace: bool = False
    ) -> None:
        key = _normalize_provider(provider)
        if not replace and key in self._factories:
            raise ValueError(f"Feed provider '{key}' already registered")
        self._factories[key] = factory

    def providers(self) -> list[str]:
        return sorted(self._factories.keys())

    def create(
        self, provider: str, options: Mapping[str, str] | None = None
    ) -> DataFeed:
        key = _normalize_provider(provider)
        factory = self._factories.get(key)
        if factory is None:
            allowed = ", ".join(self.providers()) or "<none>"
            raise ValueError(
                f"Unknown feed provider '{key}'. Allowed providers: {allowed}"
            )

        settings = FeedSettings(provider=key, options=dict(options or {}))
        return factory(settings)

    def create_from_env(
        self,
        environ: Mapping[str, str] | None = None,
        *,
        default_provider: str = "pyth",
    ) -> DataFeed:
        env = dict(environ or os.environ)
        provider = _normalize_provider(env.get("FEED_PROVIDER", default_provider))
        options = _extract_feed_options(env)
        return self.create(provider, options)


def _normalize_provider(value: str | None) -> str:
    key = str(value or "").strip().lower()
    if not key:
        raise ValueError("Feed provider cannot be empty")
    return key


def _extract_feed_options(environ: Mapping[str, str]) -> dict[str, str]:
    options: dict[str, str] = {}
    for key, value in environ.items():
        if not key.startswith("FEED_OPT_"):
            continue
        option_name = key[len("FEED_OPT_") :].strip().lower()
        if not option_name:
            continue
        options[option_name] = value
    return options


def create_default_registry() -> DataFeedRegistry:
    from crunch_node.feeds.providers import (
        build_binance_feed,
        build_mongodb_feed,
        build_pyth_feed,
    )

    registry = DataFeedRegistry()
    registry.register("pyth", build_pyth_feed)
    registry.register("binance", build_binance_feed)
    registry.register("mongodb", build_mongodb_feed)
    return registry

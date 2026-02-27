from coordinator_node.feeds.providers.binance import BinanceFeed, build_binance_feed
from coordinator_node.feeds.providers.pyth import PythFeed, build_pyth_feed

# MongoDB provider is an optional dependency (pymongo). Import lazily to avoid
# forcing the module load on users who don't use it.
# Use create_default_registry() or import directly from the mongodb module.


def __getattr__(name: str):
    if name in ("MongoDBFeed", "build_mongodb_feed"):
        from coordinator_node.feeds.providers.mongodb import (
            MongoDBFeed,
            build_mongodb_feed,
        )

        _lazy = {"MongoDBFeed": MongoDBFeed, "build_mongodb_feed": build_mongodb_feed}
        # Cache on the module so __getattr__ is only called once
        globals().update(_lazy)
        return _lazy[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BinanceFeed",
    "MongoDBFeed",
    "PythFeed",
    "build_binance_feed",
    "build_mongodb_feed",
    "build_pyth_feed",
]

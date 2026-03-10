"""Challenge configuration — baked in at package build time.

Competitors don't need to know or change these values.
Override via environment variables if needed (e.g. local dev).
"""

import os

# Coordinator URL — set by the challenge maintainer when publishing the package.
# Competitors can override via COORDINATOR_URL env var if needed.
COORDINATOR_URL = os.getenv(
    "COORDINATOR_URL",
    "http://coordinator:8000",  # Replace with actual URL when publishing
)

# Default feed dimensions for this challenge.
# Read FEED_* env vars (matching .local.env) with BACKTEST_* as fallback.
DEFAULT_SOURCE = os.getenv("FEED_SOURCE", os.getenv("BACKTEST_SOURCE", "binance"))
DEFAULT_SUBJECT = os.getenv("FEED_SUBJECTS", os.getenv("BACKTEST_SUBJECT", "BTCUSDT"))
DEFAULT_KIND = os.getenv("FEED_KIND", os.getenv("BACKTEST_KIND", "tick"))
DEFAULT_GRANULARITY = os.getenv(
    "FEED_GRANULARITY", os.getenv("BACKTEST_GRANULARITY", "1s")
)

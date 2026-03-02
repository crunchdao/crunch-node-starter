"""Tests for the challenge package backtest harness."""

from __future__ import annotations

import os

# Add scaffold/challenge to sys.path so we can import starter_challenge
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "base", "challenge"))

from starter_challenge.backtest import (
    BacktestClient,
    BacktestResult,
    BacktestRunner,
    _compute_metrics,
    _parse_date,
)


def _write_test_parquet(
    path: Path,
    start: datetime,
    count: int = 60,
    interval_seconds: int = 60,
    base_price: float = 100.0,
) -> None:
    """Write a test parquet file with synthetic candle data."""
    from coordinator_node.services.parquet_sink import get_schema

    SCHEMA = get_schema()

    ts_events = []
    opens = []
    highs = []
    lows = []
    closes = []
    volumes = []

    for i in range(count):
        ts = start + timedelta(seconds=i * interval_seconds)
        price = base_price + i * 0.1  # Slight uptrend
        ts_events.append(ts)
        opens.append(price)
        highs.append(price + 0.5)
        lows.append(price - 0.5)
        closes.append(price + 0.05)
        volumes.append(100.0)

    table = pa.table(
        {
            "ts_event": pa.array(ts_events, type=pa.timestamp("us", tz="UTC")),
            "source": pa.array(["binance"] * count, type=pa.string()),
            "subject": pa.array(["BTC"] * count, type=pa.string()),
            "kind": pa.array(["candle"] * count, type=pa.string()),
            "granularity": pa.array(["1m"] * count, type=pa.string()),
            "open": pa.array(opens, type=pa.float64()),
            "high": pa.array(highs, type=pa.float64()),
            "low": pa.array(lows, type=pa.float64()),
            "close": pa.array(closes, type=pa.float64()),
            "volume": pa.array(volumes, type=pa.float64()),
            "meta": pa.array(["{}"] * count, type=pa.string()),
        },
        schema=SCHEMA,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


class DummyTracker:
    """Minimal model for testing."""

    def __init__(self):
        self.tick_count = 0
        self.predict_count = 0
        self._latest_data = None

    def tick(self, data: dict) -> None:
        self._latest_data = data
        self.tick_count += 1

    def predict(self, **kwargs):
        self.predict_count += 1
        # Simple: predict positive if last close > first close in window
        candles = (self._latest_data or {}).get("candles_1m", [])
        if len(candles) >= 2:
            direction = candles[-1]["close"] - candles[0]["close"]
            return {"value": 1.0 if direction > 0 else -1.0}
        return {"value": 0.0}


class TestBacktestClient(unittest.TestCase):
    def setUp(self):
        self.cache_dir = tempfile.mkdtemp()

    @patch("requests.get")
    def test_pull_downloads_matching_files(self, mock_get):
        """BacktestClient.pull() fetches index, downloads matching files."""
        # Mock index response
        index_resp = MagicMock()
        index_resp.json.return_value = [
            {
                "path": "binance/BTC/candle/1m/2026-01-15.parquet",
                "records": 1440,
                "size_bytes": 5000,
                "date": "2026-01-15",
            },
            {
                "path": "binance/BTC/candle/1m/2026-01-16.parquet",
                "records": 1440,
                "size_bytes": 5000,
                "date": "2026-01-16",
            },
            {
                "path": "binance/ETH/candle/1m/2026-01-15.parquet",
                "records": 1440,
                "size_bytes": 5000,
                "date": "2026-01-15",
            },
        ]
        index_resp.raise_for_status = MagicMock()

        # Mock file download response
        file_resp = MagicMock()
        file_resp.content = b"fake parquet data"
        file_resp.raise_for_status = MagicMock()

        mock_get.side_effect = [index_resp, file_resp, file_resp]

        client = BacktestClient("http://test:8000", cache_dir=self.cache_dir)
        paths = client.pull(
            source="binance",
            subject="BTC",
            kind="candle",
            granularity="1m",
            start="2026-01-15",
            end="2026-01-16",
        )

        # Should download 2 BTC files, not the ETH one
        self.assertEqual(len(paths), 2)
        self.assertEqual(mock_get.call_count, 3)  # 1 index + 2 downloads

    @patch("requests.get")
    def test_pull_uses_cache_on_second_call(self, mock_get):
        """BacktestClient.pull() skips download for cached files."""
        index_resp = MagicMock()
        index_resp.json.return_value = [
            {
                "path": "binance/BTC/candle/1m/2026-01-15.parquet",
                "records": 10,
                "size_bytes": 100,
                "date": "2026-01-15",
            },
        ]
        index_resp.raise_for_status = MagicMock()

        file_resp = MagicMock()
        file_resp.content = b"parquet data"
        file_resp.raise_for_status = MagicMock()

        mock_get.side_effect = [index_resp, file_resp, index_resp]

        client = BacktestClient("http://test:8000", cache_dir=self.cache_dir)

        # First pull — downloads
        paths1 = client.pull(
            source="binance",
            subject="BTC",
            kind="candle",
            granularity="1m",
            start="2026-01-15",
            end="2026-01-15",
        )
        self.assertEqual(len(paths1), 1)
        self.assertEqual(mock_get.call_count, 2)  # index + download

        # Second pull — uses cache
        paths2 = client.pull(
            source="binance",
            subject="BTC",
            kind="candle",
            granularity="1m",
            start="2026-01-15",
            end="2026-01-15",
        )
        self.assertEqual(len(paths2), 1)
        self.assertEqual(mock_get.call_count, 3)  # only index, no download

    def test_list_cached_empty(self):
        client = BacktestClient("http://test:8000", cache_dir=self.cache_dir)
        self.assertEqual(client.list_cached(), [])

    def test_list_cached_finds_files(self):
        # Create a cached file using explicit dimensions
        cached_path = (
            Path(self.cache_dir)
            / "binance"
            / "BTC"
            / "candle"
            / "1m"
            / "2026-01-15.parquet"
        )
        cached_path.parent.mkdir(parents=True, exist_ok=True)
        cached_path.write_bytes(b"fake")

        client = BacktestClient("http://test:8000", cache_dir=self.cache_dir)
        files = client.list_cached(
            source="binance", subject="BTC", kind="candle", granularity="1m"
        )
        self.assertEqual(len(files), 1)


class TestBacktestRunner(unittest.TestCase):
    # Test data uses binance/BTC/candle/1m — pass explicitly so tests don't
    # depend on config.py defaults (which may differ per scaffold).
    _FEED_DIMS = dict(source="binance", kind="candle", granularity="1m")

    def setUp(self):
        self.cache_dir = tempfile.mkdtemp()
        # Write test data: 2 days, 60 records each (1 per minute)
        for day in [15, 16]:
            start = datetime(2026, 1, day, 0, 0, 0, tzinfo=UTC)
            path = (
                Path(self.cache_dir)
                / "binance"
                / "BTC"
                / "candle"
                / "1m"
                / f"2026-01-{day:02d}.parquet"
            )
            _write_test_parquet(
                path, start, count=60, base_price=100.0 + (day - 15) * 6.0
            )

    def test_run_calls_tick_and_predict(self):
        model = DummyTracker()
        runner = BacktestRunner(model=model, cache_dir=self.cache_dir)
        result = runner.run(
            subject="BTC",
            start="2026-01-15",
            end="2026-01-16",
            window_size=10,
            prediction_interval_seconds=60,
            resolve_horizon_seconds=60,
            **self._FEED_DIMS,
        )

        self.assertGreater(model.tick_count, 0)
        self.assertGreater(model.predict_count, 0)
        self.assertIsInstance(result, BacktestResult)

    def test_run_produces_predictions(self):
        model = DummyTracker()
        runner = BacktestRunner(model=model, cache_dir=self.cache_dir)
        result = runner.run(
            subject="BTC",
            start="2026-01-15",
            end="2026-01-16",
            window_size=10,
            prediction_interval_seconds=60,
            resolve_horizon_seconds=60,
            **self._FEED_DIMS,
        )

        self.assertGreater(len(result._predictions), 0)
        for pred in result._predictions:
            self.assertIn("ts", pred)
            self.assertIn("output", pred)

    def test_run_computes_metrics(self):
        model = DummyTracker()
        runner = BacktestRunner(model=model, cache_dir=self.cache_dir)
        result = runner.run(
            subject="BTC",
            start="2026-01-15",
            end="2026-01-16",
            window_size=10,
            prediction_interval_seconds=60,
            resolve_horizon_seconds=60,
            **self._FEED_DIMS,
        )

        self.assertIn("score_recent", result.metrics)
        self.assertIn("score_steady", result.metrics)
        self.assertIn("score_anchor", result.metrics)

    @patch("requests.get")
    def test_run_raises_on_missing_data(self, mock_get):
        """Raises FileNotFoundError when no data available (even after auto-pull attempt)."""
        # Mock index returns empty — no data on coordinator
        index_resp = MagicMock()
        index_resp.json.return_value = []
        index_resp.raise_for_status = MagicMock()
        mock_get.return_value = index_resp

        model = DummyTracker()
        runner = BacktestRunner(model=model, cache_dir=self.cache_dir)
        with self.assertRaises(FileNotFoundError):
            runner.run(
                subject="ETH", start="2026-01-15", end="2026-01-16", **self._FEED_DIMS
            )

    def test_predictions_df_returns_dataframe(self):
        model = DummyTracker()
        runner = BacktestRunner(model=model, cache_dir=self.cache_dir)
        result = runner.run(
            subject="BTC",
            start="2026-01-15",
            end="2026-01-16",
            window_size=10,
            prediction_interval_seconds=60,
            resolve_horizon_seconds=60,
            **self._FEED_DIMS,
        )

        df = result.predictions_df
        self.assertIsInstance(df, pd.DataFrame)
        self.assertGreater(len(df), 0)
        self.assertIn("ts", df.columns)
        self.assertIn("output", df.columns)
        self.assertIn("score", df.columns)

    def test_custom_scoring_function(self):
        """BacktestRunner uses custom scoring function when provided."""

        def custom_score(prediction, ground_truth):
            return {"value": 42.0, "success": True}

        model = DummyTracker()
        runner = BacktestRunner(
            model=model, scoring_fn=custom_score, cache_dir=self.cache_dir
        )
        result = runner.run(
            subject="BTC",
            start="2026-01-15",
            end="2026-01-16",
            window_size=10,
            prediction_interval_seconds=60,
            resolve_horizon_seconds=60,
            **self._FEED_DIMS,
        )

        scored = [p for p in result._predictions if p["score"] is not None]
        if scored:
            self.assertEqual(scored[0]["score"], 42.0)

    def test_model_code_unchanged(self):
        """The same model class works in both backtest and 'production' (tick/predict loop)."""
        model = DummyTracker()

        # Simulate production: direct tick/predict
        model.tick(
            {
                "symbol": "BTC",
                "asof_ts": 1000,
                "candles_1m": [
                    {
                        "ts": 900,
                        "open": 100,
                        "high": 101,
                        "low": 99,
                        "close": 100,
                        "volume": 10,
                    },
                    {
                        "ts": 960,
                        "open": 100,
                        "high": 102,
                        "low": 99,
                        "close": 101,
                        "volume": 10,
                    },
                ],
            }
        )
        prod_result = model.predict(
            subject="BTC", resolve_horizon_seconds=60, step_seconds=60
        )

        # Same model in backtest
        model2 = DummyTracker()
        runner = BacktestRunner(model=model2, cache_dir=self.cache_dir)
        result = runner.run(
            subject="BTC",
            start="2026-01-15",
            end="2026-01-16",
            window_size=10,
            prediction_interval_seconds=60,
            resolve_horizon_seconds=60,
            **self._FEED_DIMS,
        )

        # Both should produce valid outputs with "value" key
        self.assertIn("value", prod_result)
        for pred in result._predictions:
            self.assertIn("value", pred["output"])


class TestBacktestResult(unittest.TestCase):
    def test_summary_output(self):
        result = BacktestResult(
            predictions=[
                {
                    "ts": datetime(2026, 1, 15, tzinfo=UTC),
                    "output": {"value": 1.0},
                    "actual": {"profit": 0.01},
                    "score": 1.0,
                    "score_success": True,
                },
            ],
            metrics={"score_recent": 0.5, "score_steady": 0.4, "score_anchor": 0.3},
            config={"subject": "BTC", "start": "2026-01-15", "end": "2026-01-16"},
        )
        text = result.summary()
        self.assertIn("BTC", text)
        self.assertIn("score_recent", text)

    def test_repr_html(self):
        result = BacktestResult(
            predictions=[{"score": 1.0, "score_success": True}],
            metrics={"score_recent": 0.5},
            config={"subject": "BTC"},
        )
        html = result._repr_html_()
        self.assertIn("score_recent", html)
        self.assertIn("BTC", html)

    def test_repr(self):
        result = BacktestResult(predictions=[{}, {}], metrics={"x": 1.0}, config={})
        self.assertIn("predictions=2", repr(result))


class TestHelpers(unittest.TestCase):
    def test_parse_date_string(self):
        dt = _parse_date("2026-01-15")
        self.assertEqual(dt, datetime(2026, 1, 15, tzinfo=UTC))

    def test_parse_date_iso(self):
        dt = _parse_date("2026-01-15T10:30:00")
        self.assertEqual(dt, datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC))

    def test_parse_date_passthrough(self):
        original = datetime(2026, 1, 15, tzinfo=UTC)
        self.assertEqual(_parse_date(original), original)

    def test_parse_date_adds_utc_to_naive(self):
        dt = _parse_date(datetime(2026, 1, 15))
        self.assertIsNotNone(dt.tzinfo)

    def test_compute_metrics_empty(self):
        metrics = _compute_metrics([])
        self.assertEqual(metrics["score_recent"], 0.0)

    def test_compute_metrics_with_scores(self):
        now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        predictions = [
            {"ts": now - timedelta(hours=1), "score": 1.0, "score_success": True},
            {"ts": now, "score": -1.0, "score_success": True},
        ]
        metrics = _compute_metrics(predictions)
        self.assertAlmostEqual(metrics["score_recent"], 0.0)  # avg of 1.0 and -1.0


if __name__ == "__main__":
    unittest.main()

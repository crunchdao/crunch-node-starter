"""Backtest harness for challenge models.

Usage in a notebook or script:

    from starter_challenge.backtest import BacktestRunner
    from my_model import MyTracker

    result = BacktestRunner(model=MyTracker()).run(
        start="2026-01-01", end="2026-02-01"
    )
    result.predictions_df   # DataFrame in notebook
    result.metrics           # rolling window aggregates
    result.summary()         # formatted output

The coordinator URL is baked into the challenge package. Data is
automatically fetched and cached on first run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── BacktestResult ──


class BacktestResult:
    """Container for backtest output. Notebook-friendly."""

    def __init__(
        self,
        predictions: list[dict[str, Any]],
        metrics: dict[str, float],
        config: dict[str, Any],
    ):
        self._predictions = predictions
        self.metrics = metrics
        self.config = config

    @property
    def predictions_df(self):
        """Return predictions as a pandas DataFrame."""
        import pandas as pd

        return pd.DataFrame(self._predictions)

    def _repr_html_(self) -> str:
        """Rich HTML display for Jupyter notebooks."""
        rows = ""
        for key, value in self.metrics.items():
            rows += f"<tr><td><b>{key}</b></td><td>{value:+.6f}</td></tr>"

        pred_count = len(self._predictions)
        scored = sum(1 for p in self._predictions if p.get("score") is not None)

        return f"""
        <div style="font-family: monospace; padding: 10px;">
            <h3>Backtest Result</h3>
            <p>Subject: <b>{self.config.get("subject", "N/A")}</b> |
               Period: {self.config.get("start", "?")} → {self.config.get("end", "?")} |
               Predictions: {pred_count} | Scored: {scored}</p>
            <table border="1" cellpadding="5" style="border-collapse: collapse;">
                <tr><th>Metric</th><th>Value</th></tr>
                {rows}
            </table>
        </div>
        """

    @property
    def diversity(self) -> dict[str, Any] | None:
        """Fetch diversity feedback from the coordinator for this model.

        Returns None if the coordinator is unreachable or the model has no
        production predictions yet. Only works after the model has been
        submitted and scored in production.
        """
        model_id = self.config.get("model_id")
        if not model_id:
            return None
        try:
            import requests

            from starter_challenge.config import COORDINATOR_URL

            url = f"{COORDINATOR_URL.rstrip('/')}/reports/models/{model_id}/diversity"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    def summary(self, show_diversity: bool = True) -> str:
        """Return a formatted summary string.

        If show_diversity=True and a model_id is configured, attempts to
        fetch diversity feedback from the coordinator.
        """
        lines = [
            "═" * 60,
            "  BACKTEST SUMMARY",
            "═" * 60,
            f"  Subject:     {self.config.get('subject', 'N/A')}",
            f"  Period:      {self.config.get('start', '?')} → {self.config.get('end', '?')}",
            f"  Predictions: {len(self._predictions)}",
            "─" * 60,
            "  METRICS:",
        ]

        # Group metrics
        window_keys = {"score_recent", "score_steady", "score_anchor"}
        diversity_keys = {
            "model_correlation",
            "ensemble_correlation",
            "contribution",
            "fnc",
        }

        for key, value in self.metrics.items():
            if key not in diversity_keys:
                lines.append(f"    {key:20s} {value:+.6f}")

        # Show diversity-related metrics separately if present
        diversity_metrics = {
            k: v for k, v in self.metrics.items() if k in diversity_keys
        }
        if diversity_metrics:
            lines.append("─" * 60)
            lines.append("  DIVERSITY (vs. production ensemble):")
            for key, value in diversity_metrics.items():
                lines.append(f"    {key:20s} {value:+.6f}")

        # Try to fetch live diversity feedback
        if show_diversity:
            div = self.diversity
            if div:
                lines.append("─" * 60)
                lines.append("  DIVERSITY FEEDBACK:")
                ds = div.get("diversity_score")
                if ds is not None:
                    lines.append(f"    {'diversity_score':20s} {ds:.4f}")
                rank = div.get("rank")
                if rank is not None:
                    lines.append(f"    {'rank':20s} #{rank}")
                for g in div.get("guidance", []):
                    lines.append(f"    → {g}")

        lines.append("═" * 60)
        text = "\n".join(lines)
        print(text)
        return text

    def __repr__(self) -> str:
        return f"BacktestResult(predictions={len(self._predictions)}, metrics={self.metrics})"


# ── BacktestClient ──


class BacktestClient:
    """Fetches backfill parquet files from a coordinator and caches locally.

    The coordinator URL defaults to the value baked into the challenge package.
    Override via constructor arg or COORDINATOR_URL env var.
    """

    def __init__(
        self,
        coordinator_url: str | None = None,
        cache_dir: str = ".cache/backtest",
    ):
        if coordinator_url is None:
            from starter_challenge.config import COORDINATOR_URL

            coordinator_url = COORDINATOR_URL
        self.coordinator_url = coordinator_url.rstrip("/")
        self.cache_dir = Path(cache_dir)

    def pull(
        self,
        source: str | None = None,
        subject: str | None = None,
        kind: str | None = None,
        granularity: str | None = None,
        start: str | datetime = "2026-01-01",
        end: str | datetime = "2026-02-01",
        refresh: bool = False,
    ) -> list[Path]:
        """Download matching parquet files from coordinator, cache locally.

        Returns list of cached file paths. Feed dimensions default to the
        challenge config if not specified.
        """
        import requests

        from starter_challenge.config import (
            DEFAULT_GRANULARITY,
            DEFAULT_KIND,
            DEFAULT_SOURCE,
            DEFAULT_SUBJECT,
        )

        source = source or DEFAULT_SOURCE
        subject = subject or DEFAULT_SUBJECT
        kind = kind or DEFAULT_KIND
        granularity = granularity or DEFAULT_GRANULARITY

        start_dt = _parse_date(start)
        end_dt = _parse_date(end)

        # Get index
        resp = requests.get(f"{self.coordinator_url}/data/backfill/index", timeout=30)
        resp.raise_for_status()
        manifest = resp.json()

        # Filter to matching files
        prefix = f"{source}/{subject}/{kind}/{granularity}/"
        matching = []
        for entry in manifest:
            path = entry["path"]
            if not path.startswith(prefix):
                continue
            date_str = entry.get("date", "")
            try:
                file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
            except ValueError:
                continue
            if start_dt <= file_date <= end_dt:
                matching.append(entry)

        # Download files
        downloaded: list[Path] = []
        for entry in matching:
            rel_path = entry["path"]
            local_path = self.cache_dir / rel_path

            if local_path.exists() and not refresh:
                logger.debug("cached: %s", local_path)
                downloaded.append(local_path)
                continue

            url = f"{self.coordinator_url}/data/backfill/{rel_path}"
            logger.info("downloading: %s", url)
            file_resp = requests.get(url, timeout=120)
            file_resp.raise_for_status()

            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(file_resp.content)
            downloaded.append(local_path)

        logger.info(
            "pulled %d files (%d from cache)",
            len(downloaded),
            sum(1 for p in downloaded if p.exists()),
        )
        return downloaded

    def list_cached(
        self,
        source: str | None = None,
        subject: str | None = None,
        kind: str | None = None,
        granularity: str | None = None,
    ) -> list[Path]:
        """Return cached parquet file paths for given dimensions."""
        from starter_challenge.config import (
            DEFAULT_GRANULARITY,
            DEFAULT_KIND,
            DEFAULT_SOURCE,
            DEFAULT_SUBJECT,
        )

        source = source or DEFAULT_SOURCE
        subject = subject or DEFAULT_SUBJECT
        kind = kind or DEFAULT_KIND
        granularity = granularity or DEFAULT_GRANULARITY
        target_dir = self.cache_dir / source / subject / kind / granularity
        if not target_dir.exists():
            return []
        return sorted(target_dir.glob("*.parquet"))


# ── BacktestRunner ──


class BacktestRunner:
    """Replays historical data through a ModelBaseClass model, scores predictions."""

    def __init__(
        self,
        model,
        scoring_fn: Callable[[dict, dict], dict] | None = None,
        cache_dir: str = ".cache/backtest",
        output_type: type[Any] | None = None,
    ):
        self.model = model
        self.scoring_fn = scoring_fn or _default_scoring_fn()
        self.cache_dir = Path(cache_dir)
        self._output_type = output_type or self._load_output_type()

    @staticmethod
    def _load_output_type() -> type[Any]:
        """Try to load InferenceOutput from the coordinator config."""
        try:
            from crunch_node.crunch_config import InferenceOutput

            return InferenceOutput
        except ImportError:
            return None

    def _coerce_output(self, output: Any) -> dict[str, Any]:
        """Coerce model output to a dict matching InferenceOutput schema.

        Handles raw scalars, dicts, and Pydantic model instances.
        Uses InferenceOutput field names instead of hardcoding {"value": ...}.
        """
        if isinstance(output, dict):
            return output

        # Try to use InferenceOutput schema to determine the first field name
        if self._output_type is not None and hasattr(self._output_type, "model_fields"):
            fields = list(self._output_type.model_fields.keys())
            if fields:
                return {fields[0]: output}

        # Fallback for when no schema is available
        return {"value": output}

    def _coerce_error_output(self, exc: Exception) -> dict[str, Any]:
        """Build a default output dict for a failed prediction.

        Uses InferenceOutput default values instead of hardcoding {"value": 0.0}.
        """
        defaults: dict[str, Any] = {}
        if self._output_type is not None and hasattr(self._output_type, "model_fields"):
            try:
                defaults = self._output_type().model_dump()
            except Exception:
                defaults = {"value": 0.0}
        else:
            defaults = {"value": 0.0}

        defaults["_error"] = str(exc)
        return defaults

    def run(
        self,
        source: str | None = None,
        subject: str | None = None,
        kind: str | None = None,
        granularity: str | None = None,
        start: str | datetime = "2026-01-01",
        end: str | datetime = "2026-02-01",
        window_size: int = 120,
        prediction_interval_seconds: int = 60,
        resolve_horizon_seconds: int = 60,
    ) -> BacktestResult:
        """Replay cached data through the model, score predictions.

        Data is automatically fetched from the coordinator and cached on first
        run. Feed dimensions default to the challenge config if not specified.

        Returns a BacktestResult with predictions DataFrame and metrics.
        """
        import pandas as pd

        from starter_challenge.config import (
            DEFAULT_GRANULARITY,
            DEFAULT_KIND,
            DEFAULT_SOURCE,
            DEFAULT_SUBJECT,
        )

        source = source or DEFAULT_SOURCE
        subject = subject or DEFAULT_SUBJECT
        kind = kind or DEFAULT_KIND
        granularity = granularity or DEFAULT_GRANULARITY

        start_dt = _parse_date(start)
        end_dt = _parse_date(end)

        # Load cached parquet data — auto-pull if not cached
        data_dir = self.cache_dir / source / subject / kind / granularity
        if not data_dir.exists() or not list(data_dir.glob("*.parquet")):
            logger.info("No cached data found, pulling from coordinator...")
            client = BacktestClient(cache_dir=str(self.cache_dir))
            client.pull(
                source=source,
                subject=subject,
                kind=kind,
                granularity=granularity,
                start=start,
                end=end,
            )

        # Read and concat all matching parquet files
        frames = []
        for parquet_file in sorted(data_dir.glob("*.parquet")):
            date_str = parquet_file.stem
            try:
                file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
            except ValueError:
                continue
            if start_dt <= file_date <= end_dt:
                frames.append(pd.read_parquet(parquet_file))

        if not frames:
            raise FileNotFoundError(
                f"No data available for {subject} from {start} to {end}. "
                f"The coordinator may not have backfill data for this range yet."
            )

        df = (
            pd.concat(frames, ignore_index=True)
            .sort_values("ts_event")
            .reset_index(drop=True)
        )

        # Convert ts_event to datetime if needed
        if not pd.api.types.is_datetime64_any_dtype(df["ts_event"]):
            df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True)

        # Replay loop
        predictions: list[dict[str, Any]] = []
        last_predict_ts: datetime | None = None
        interval = timedelta(seconds=prediction_interval_seconds)
        horizon = timedelta(seconds=resolve_horizon_seconds)

        for i in range(window_size, len(df)):
            current_ts = df.iloc[i]["ts_event"]
            if hasattr(current_ts, "to_pydatetime"):
                current_ts = current_ts.to_pydatetime()
            if current_ts.tzinfo is None:
                current_ts = current_ts.replace(tzinfo=UTC)

            # Build window for feed_update()
            window_df = df.iloc[max(0, i - window_size + 1) : i + 1]
            feed_data = _df_to_feed_update_data(window_df, subject)
            self.model.feed_update(feed_data)

            # Predict at intervals
            if last_predict_ts is None or (current_ts - last_predict_ts) >= interval:
                try:
                    output = self.model.predict(
                        subject=subject,
                        resolve_horizon_seconds=resolve_horizon_seconds,
                        step_seconds=prediction_interval_seconds,
                    )
                    output = self._coerce_output(output)
                except Exception as exc:
                    output = self._coerce_error_output(exc)

                # Find actual outcome at horizon
                resolve_ts = current_ts + horizon
                actual = self._find_actual(df, i, current_ts, resolve_ts)

                # Score
                score_result = None
                if actual is not None:
                    try:
                        score_result = self.scoring_fn(output, actual)
                    except Exception as exc:
                        score_result = {
                            "value": 0.0,
                            "success": False,
                            "failed_reason": str(exc),
                        }

                predictions.append(
                    {
                        "ts": current_ts,
                        "output": output,
                        "actual": actual,
                        "score": score_result.get("value") if score_result else None,
                        "score_success": score_result.get("success", True)
                        if score_result
                        else None,
                    }
                )

                last_predict_ts = current_ts

        # Compute rolling window metrics
        metrics = _compute_metrics(predictions)

        config = {
            "source": source,
            "subject": subject,
            "kind": kind,
            "granularity": granularity,
            "start": str(start),
            "end": str(end),
            "window_size": window_size,
            "prediction_interval_seconds": prediction_interval_seconds,
            "resolve_horizon_seconds": resolve_horizon_seconds,
        }

        return BacktestResult(predictions=predictions, metrics=metrics, config=config)

    def _find_actual(
        self,
        df,
        current_idx: int,
        current_ts: datetime,
        resolve_ts: datetime,
    ) -> dict[str, Any] | None:
        """Find the actual outcome at the horizon timestamp."""
        import pandas as pd

        # Find the closest record at or after resolve_ts
        future_df = df.iloc[current_idx:]
        future_ts = future_df["ts_event"]

        # Convert resolve_ts for comparison
        if hasattr(resolve_ts, "timestamp"):
            resolve_pd = pd.Timestamp(resolve_ts)
        else:
            resolve_pd = resolve_ts

        mask = future_ts >= resolve_pd
        if not mask.any():
            return None

        resolve_row = future_df.loc[mask.idxmax()]

        entry_price = _safe_float(df.iloc[current_idx].get("close"))
        resolved_price = _safe_float(resolve_row.get("close"))

        if entry_price is None or resolved_price is None:
            return None

        return {
            "entry_price": entry_price,
            "resolved_price": resolved_price,
            "profit": (resolved_price - entry_price) / max(abs(entry_price), 1e-9),
            "direction_up": resolved_price > entry_price,
        }


def _df_to_feed_update_data(window_df, subject: str) -> dict[str, Any]:
    """Convert a DataFrame window to the feed update data format models expect."""
    candles = []
    for _, row in window_df.iterrows():
        ts = row["ts_event"]
        if hasattr(ts, "timestamp"):
            ts_int = int(ts.timestamp())
        else:
            ts_int = int(ts)

        candles.append(
            {
                "ts": ts_int,
                "open": _safe_float(row.get("open")) or 0.0,
                "high": _safe_float(row.get("high")) or 0.0,
                "low": _safe_float(row.get("low")) or 0.0,
                "close": _safe_float(row.get("close")) or 0.0,
                "volume": _safe_float(row.get("volume")) or 0.0,
            }
        )

    asof_ts = candles[-1]["ts"] if candles else 0
    return {
        "symbol": subject,
        "asof_ts": asof_ts,
        "candles_1m": candles,
    }


def _compute_metrics(predictions: list[dict[str, Any]]) -> dict[str, float]:
    """Compute rolling window metrics + multi-metric enrichment.

    Rolling windows match production aggregation (score_recent, score_steady, score_anchor).
    Multi-metrics (IC, hit rate, etc.) computed using the same registry as the coordinator.
    """
    scored = [
        p
        for p in predictions
        if p.get("score") is not None and p.get("score_success", True)
    ]

    if not scored:
        return {"score_recent": 0.0, "score_steady": 0.0, "score_anchor": 0.0}

    # Use the last prediction's timestamp as "now"
    now = scored[-1]["ts"]
    if hasattr(now, "timestamp"):
        pass  # already datetime
    else:
        now = datetime.fromtimestamp(now, tz=UTC)

    windows = {
        "score_recent": timedelta(hours=24),
        "score_steady": timedelta(hours=72),
        "score_anchor": timedelta(hours=168),
    }

    metrics: dict[str, float] = {}
    for name, window in windows.items():
        cutoff = now - window
        window_scores = [p["score"] for p in scored if _ts_ge(p["ts"], cutoff)]
        metrics[name] = (
            sum(window_scores) / len(window_scores) if window_scores else 0.0
        )

    # Multi-metric enrichment (best-effort — crunch_node may not be installed)
    try:
        from crunch_node.metrics.context import MetricsContext
        from crunch_node.metrics.registry import get_default_registry

        registry = get_default_registry()
        active_metrics = [
            "ic",
            "ic_sharpe",
            "hit_rate",
            "mean_return",
            "max_drawdown",
            "sortino_ratio",
            "turnover",
        ]

        # Convert backtest predictions to the format the registry expects
        pred_dicts = [
            {"inference_output": p.get("output", {"value": 0.0})} for p in scored
        ]
        score_dicts = [
            {
                "result": {
                    "value": p["score"],
                    "actual_return": (p.get("actual") or {}).get("profit", 0.0),
                }
            }
            for p in scored
        ]
        ctx = MetricsContext(model_id="backtest")

        multi = registry.compute(active_metrics, pred_dicts, score_dicts, ctx)
        metrics.update(multi)
    except ImportError:
        pass  # crunch_node not installed, skip multi-metrics

    return metrics


def _ts_ge(ts, cutoff: datetime) -> bool:
    """Check if timestamp >= cutoff, handling mixed types."""
    if hasattr(ts, "timestamp"):
        return ts >= cutoff
    return datetime.fromtimestamp(ts, tz=UTC) >= cutoff


def _default_scoring_fn() -> Callable[[dict, dict], dict]:
    """Try to import the challenge's scoring function, fall back to basic."""
    try:
        from starter_challenge.scoring import score_prediction

        return score_prediction
    except ImportError:
        pass

    def _basic_score(prediction: dict, ground_truth: dict) -> dict:
        pred_val = float(prediction.get("value", 0.0))
        actual_return = float(ground_truth.get("profit", 0.0))
        # Simple directional score: +1 if prediction direction matches, -1 otherwise
        correct = (
            (pred_val > 0 and actual_return > 0)
            or (pred_val < 0 and actual_return < 0)
            or (pred_val == 0)
        )
        return {"value": 1.0 if correct else -1.0, "success": True}

    return _basic_score


def _parse_date(value) -> datetime:
    """Parse a date string or pass through datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=UTC)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {value!r}")


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

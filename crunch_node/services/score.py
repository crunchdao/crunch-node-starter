"""Score service: resolve actuals on inputs → score predictions → leaderboard."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from crunch_node.crunch_config import CrunchConfig, ScoringFunction
from crunch_node.db.repositories import (
    DBCheckpointRepository,
    DBInputRepository,
    DBLeaderboardRepository,
    DBMerkleCycleRepository,
    DBMerkleNodeRepository,
    DBModelRepository,
    DBPredictionRepository,
    DBScoreRepository,
    DBSnapshotRepository,
)
from crunch_node.entities.prediction import (
    PredictionStatus,
    ScoreRecord,
    SnapshotRecord,
)
from crunch_node.merkle.service import MerkleService
from crunch_node.services.checkpoint import CheckpointService
from crunch_node.services.feed_reader import FeedReader


class ScoreService:
    def __init__(
        self,
        checkpoint_interval_seconds: int,
        scoring_function: ScoringFunction | Callable,
        feed_reader: FeedReader | None = None,
        input_repository: DBInputRepository | None = None,
        prediction_repository: DBPredictionRepository | None = None,
        score_repository: DBScoreRepository | None = None,
        snapshot_repository: DBSnapshotRepository | None = None,
        model_repository: DBModelRepository | None = None,
        leaderboard_repository: DBLeaderboardRepository | None = None,
        merkle_cycle_repository: DBMerkleCycleRepository | None = None,
        merkle_node_repository: DBMerkleNodeRepository | None = None,
        checkpoint_repository: DBCheckpointRepository | None = None,
        config: CrunchConfig | None = None,
        contract: CrunchConfig | None = None,
        score_interval_seconds: int | None = None,
        **kwargs: Any,
    ):
        self.checkpoint_interval_seconds = checkpoint_interval_seconds
        self.score_interval_seconds = score_interval_seconds or min(
            60, checkpoint_interval_seconds
        )
        self.scoring_function = scoring_function
        self.feed_reader = feed_reader
        self.input_repository = input_repository
        self.prediction_repository = prediction_repository
        self.score_repository = score_repository
        self.snapshot_repository = snapshot_repository
        self.model_repository = model_repository
        self.leaderboard_repository = leaderboard_repository
        if config is not None and contract is not None and config is not contract:
            raise ValueError("Provide only one of config= or contract=")
        self.config = config or contract or CrunchConfig()

        # Merkle tamper evidence
        if merkle_cycle_repository and merkle_node_repository:
            self.merkle_service: MerkleService | None = MerkleService(
                merkle_cycle_repository=merkle_cycle_repository,
                merkle_node_repository=merkle_node_repository,
            )
        else:
            self.merkle_service = None

        # Checkpoint service (composed, not a separate container)
        if checkpoint_repository and snapshot_repository and model_repository:
            self._checkpoint_service: CheckpointService | None = CheckpointService(
                snapshot_repository=snapshot_repository,
                checkpoint_repository=checkpoint_repository,
                model_repository=model_repository,
                config=self.config,
                interval_seconds=checkpoint_interval_seconds,
                merkle_service=self.merkle_service,
            )
        else:
            self._checkpoint_service = None
        self._last_checkpoint_at: datetime | None = None

        self.logger = logging.getLogger(__name__)
        self.stop_event = asyncio.Event()

    @property
    def contract(self) -> CrunchConfig:
        """Backward-compatible alias for ``config``."""
        return self.config

    @contract.setter
    def contract(self, value: CrunchConfig) -> None:
        self.config = value

    # ── scoring stub detection ──

    @staticmethod
    def detect_scoring_stub(
        scoring_function: ScoringFunction | Callable,
    ) -> tuple[bool, str]:
        """Probe the scoring function with varied inputs to detect stubs.

        Returns (is_stub, reason). A function that returns identical scores
        for significantly different inputs is likely a placeholder.
        Uses raw dicts for probing since concrete types aren't known here.
        """
        test_cases = [
            (
                {"value": 1.0},
                {
                    "entry_price": 40000,
                    "resolved_price": 40100,
                    "profit": 0.0025,
                    "direction_up": True,
                },
            ),
            (
                {"value": -1.0},
                {
                    "entry_price": 40000,
                    "resolved_price": 39900,
                    "profit": -0.0025,
                    "direction_up": False,
                },
            ),
            (
                {"value": 0.5},
                {
                    "entry_price": 40000,
                    "resolved_price": 40500,
                    "profit": 0.0125,
                    "direction_up": True,
                },
            ),
        ]

        results = []
        for pred, gt in test_cases:
            try:
                result = scoring_function(pred, gt)
                if isinstance(result, BaseModel):
                    results.append(getattr(result, "value", 0.0))
                else:
                    results.append(result.get("value", 0.0))
            except Exception:
                return False, "scoring function raised an exception during probe"

        if len(set(results)) <= 1:
            return True, (
                f"Scoring function returns identical value ({results[0]}) for all "
                f"test inputs. This looks like a stub — implement real scoring logic."
            )

        return False, "ok"

    # ── typed coercion ──

    def _coerce_output(self, raw: dict[str, Any]) -> BaseModel:
        """Parse a raw inference_output dict into a typed ``output_type`` instance.

        Returns a Pydantic model instance (not a dict). Extra keys from the
        model that are not part of ``output_type`` are preserved via extra="allow"
        or model_config on the type.
        """
        try:
            return self.config.output_type.model_validate(raw)
        except Exception as exc:
            self.logger.warning(
                "output_type coercion failed (%s), wrapping raw dict",
                exc,
            )
            # Fallback: construct with extra fields allowed
            try:
                return self.config.output_type.model_construct(**raw)
            except Exception:
                return self.config.output_type()

    def _coerce_ground_truth(self, raw: dict[str, Any]) -> BaseModel:
        """Parse a raw ground truth dict into a typed ``ground_truth_type`` instance."""
        gt_type = self.config.get_ground_truth_type()
        try:
            return gt_type.model_validate(raw)
        except Exception as exc:
            self.logger.warning(
                "ground_truth_type coercion failed (%s), wrapping raw dict",
                exc,
            )
            try:
                return gt_type.model_construct(**raw)
            except Exception:
                return gt_type()

    def validate_scoring_io(self) -> None:
        """Dry-run the scoring function with default config types at startup.

        Catches field-name mismatches (e.g. scoring reads ``prediction.order_type``
        but ``output_type`` only defines ``value``) before any real predictions
        are scored.  Raises on hard errors; logs warnings on soft issues.
        """
        output_type = self.config.output_type
        ground_truth_type = self.config.get_ground_truth_type()

        # Build a sample prediction from output_type defaults
        try:
            sample_output = output_type()
        except Exception as exc:
            raise RuntimeError(
                f"Cannot construct a default {output_type.__name__}: {exc}. "
                f"Ensure all fields have defaults or the model_config allows it."
            ) from exc

        # Build a sample ground truth
        try:
            sample_gt = ground_truth_type()
        except Exception as exc:
            self.logger.warning(
                "Cannot construct a default GroundTruth (%s): %s — "
                "scoring dry-run skipped (ground truth requires runtime data)",
                ground_truth_type.__name__,
                exc,
            )
            return

        # Dry-run the scoring function with typed objects
        try:
            result = self.scoring_function(sample_output, sample_gt)
        except KeyError as exc:
            raise RuntimeError(
                f"Scoring function raised KeyError({exc}) when called with default "
                f"{output_type.__name__} and default {ground_truth_type.__name__}. "
                f"Ensure the scoring function reads attributes defined on these types."
            ) from exc
        except AttributeError as exc:
            raise RuntimeError(
                f"Scoring function raised AttributeError({exc}) — it may be "
                f"using dict access (.get/[]) instead of attribute access. "
                f"Scoring functions now receive typed Pydantic objects."
            ) from exc
        except Exception as exc:
            self.logger.warning(
                "Scoring dry-run raised %s: %s — this may be OK if the function "
                "requires real data, but check field names match output_type",
                type(exc).__name__,
                exc,
            )
            return

        # Validate the result
        result_dict = result.model_dump() if isinstance(result, BaseModel) else result
        try:
            self.config.score_type.model_validate(result_dict)
        except Exception as exc:
            raise RuntimeError(
                f"Scoring function returned {result!r} which does not match "
                f"{self.config.score_type.__name__}: {exc}"
            ) from exc

        self.logger.info(
            "Scoring IO validation passed: %s → scoring → %s",
            output_type.__name__,
            self.config.score_type.__name__,
        )

    async def run(self) -> None:
        self.logger.info(
            "score service started (score_interval=%ds, checkpoint_interval=%ds)",
            self.score_interval_seconds,
            self.checkpoint_interval_seconds,
        )
        while not self.stop_event.is_set():
            try:
                self.score_and_snapshot()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.exception("score loop error: %s", exc)
                self._rollback_repositories()
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(), timeout=self.score_interval_seconds
                )
            except TimeoutError:
                pass

    def score_and_snapshot(self) -> bool:
        now = datetime.now(UTC)

        # 1. score predictions past their resolve horizon
        scored = self._score_predictions(now)
        if not scored:
            self.logger.info("No predictions scored this cycle")
            return False

        # 2. write snapshots (per-model period summary + multi-metric enrichment)
        cycle_snapshots = self._write_snapshots(scored, now)

        # 3. compute ensembles (if configured)
        self._compute_ensembles(scored, now)

        # 4. rebuild leaderboard from snapshots
        self._rebuild_leaderboard()

        # 5. create checkpoint if interval elapsed
        self._maybe_checkpoint(now)

        return True

    async def shutdown(self) -> None:
        self.stop_event.set()

    # ── 1. score predictions ──

    def _resolve_actuals(self, prediction: PredictionRecord) -> dict[str, Any] | None:
        """Resolve ground truth for a single prediction.

        - resolve_horizon_seconds=0 (resolvable_at == performed_at): immediate
          resolution with empty actuals (live trading).
        - Otherwise: fetch feed records in the horizon window and call
          resolve_ground_truth.
        """
        if prediction.resolvable_at is None:
            return None

        # Immediate resolution (resolve_horizon_seconds=0):
        # Use the prediction's own input as ground truth — the feed data
        # (prices, candles, etc.) is already captured in raw_data.
        if prediction.resolvable_at <= prediction.performed_at:
            if self.input_repository is None:
                raise RuntimeError(
                    "resolve_horizon_seconds=0 requires an input_repository "
                    "to look up ground truth from the prediction's input"
                )
            inp = self.input_repository.get(prediction.input_id)
            if inp is None:
                self.logger.warning(
                    "Input %s not found for prediction %s — skipping",
                    prediction.input_id,
                    prediction.id,
                )
                return None
            return inp.raw_data

        if self.feed_reader is None:
            return None

        scope = prediction.scope or {}
        records = self.feed_reader.fetch_window(
            start=prediction.performed_at,
            end=prediction.resolvable_at,
            source=scope.get("source"),
            subject=scope.get("subject"),
            kind=scope.get("kind"),
            granularity=scope.get("granularity"),
        )

        if not records:
            return None

        # Check that resolved data is near the horizon, not stale
        staleness_fraction = self.config.max_ground_truth_staleness_fraction
        if staleness_fraction > 0:
            last_record = records[-1]
            horizon_ts = prediction.resolvable_at.timestamp()
            performed_ts = prediction.performed_at.timestamp()
            resolved_ts = last_record.ts_event.timestamp()

            horizon_seconds = horizon_ts - performed_ts
            staleness_seconds = horizon_ts - resolved_ts
            max_staleness = horizon_seconds * staleness_fraction

            if staleness_seconds > max_staleness:
                self.logger.warning(
                    "Ground truth too stale for prediction %s: "
                    "last record is %.1fs before horizon (max allowed: %.1fs = %.0f%% of horizon)",
                    prediction.id,
                    staleness_seconds,
                    max_staleness,
                    staleness_fraction * 100,
                )
                return None

        actuals = self.config.resolve_ground_truth(records, prediction)
        if actuals is None:
            return None
        # Coerce to dict if resolve_ground_truth returned a BaseModel
        if isinstance(actuals, BaseModel):
            actuals = actuals.model_dump()
        return actuals

    def _score_predictions(self, now: datetime) -> list[ScoreRecord]:
        predictions = self.prediction_repository.find(
            status=PredictionStatus.PENDING,
            resolvable_before=now,
        )
        if not predictions:
            return []

        scored: list[ScoreRecord] = []
        for prediction in predictions:
            actuals_dict = self._resolve_actuals(prediction)
            if actuals_dict is None:
                continue  # ground truth not yet available

            typed_output = self._coerce_output(prediction.inference_output)
            typed_gt = self._coerce_ground_truth(actuals_dict)

            # Inject prediction metadata so scoring functions can identify
            # the model (e.g. for stateful per-model position tracking).
            if hasattr(typed_output, "model_config"):
                # Set extra attrs on the model for scoring context
                typed_output.__dict__["model_id"] = prediction.model_id
                typed_output.__dict__["prediction_id"] = prediction.id

            result = self.scoring_function(typed_output, typed_gt)
            result_dict = (
                result.model_dump() if isinstance(result, BaseModel) else result
            )
            validated = self.config.score_type.model_validate(result_dict)

            score = ScoreRecord(
                id=f"SCR_{prediction.id}",
                prediction_id=prediction.id,
                result=validated.model_dump(),
                success=True,
                scored_at=now,
            )

            if self.score_repository is not None:
                self.score_repository.save(score)

            prediction.status = PredictionStatus.SCORED
            self.prediction_repository.save(prediction)
            scored.append(score)

        if scored:
            self.logger.info("Scored %d predictions", len(scored))
        return scored

    # ── 3. snapshots (with multi-metric enrichment) ──

    def _write_snapshots(
        self, scored: list[ScoreRecord], now: datetime
    ) -> list[SnapshotRecord]:
        if self.snapshot_repository is None:
            return []

        # Group scores and predictions by model
        pred_map: dict[str, str] = {}  # prediction_id → model_id
        pred_by_id: dict[str, Any] = {}  # prediction_id → prediction
        predictions = self.prediction_repository.find(status=PredictionStatus.SCORED)
        for p in predictions:
            pred_map[p.id] = p.model_id
            pred_by_id[p.id] = p

        by_model_scores: dict[str, list[dict[str, Any]]] = {}
        by_model_preds: dict[str, list[dict[str, Any]]] = {}
        by_model_score_dicts: dict[str, list[dict[str, Any]]] = {}

        for score in scored:
            model_id = pred_map.get(score.prediction_id)
            if not model_id:
                continue
            by_model_scores.setdefault(model_id, []).append(score.result)

            pred = pred_by_id.get(score.prediction_id)
            if pred:
                by_model_preds.setdefault(model_id, []).append(
                    {
                        "inference_output": pred.inference_output,
                        "performed_at": pred.performed_at,
                        "scope": pred.scope,
                    }
                )
            by_model_score_dicts.setdefault(model_id, []).append(
                {
                    "result": score.result,
                    "scored_at": score.scored_at,
                }
            )

        # Build MetricsContext (shared across all model evaluations)
        from crunch_node.metrics.context import MetricsContext

        metrics_context_base = MetricsContext(
            model_id="",  # set per-model below
            window_start=min((s.scored_at for s in scored), default=now),
            window_end=now,
            all_model_predictions=by_model_preds,
        )

        written_snapshots: list[SnapshotRecord] = []

        for model_id, results in by_model_scores.items():
            # Baseline aggregation
            summary = self.config.aggregate_snapshot(results)

            # Multi-metric enrichment
            if self.config.metrics:
                ctx = MetricsContext(
                    model_id=model_id,
                    window_start=metrics_context_base.window_start,
                    window_end=metrics_context_base.window_end,
                    all_model_predictions=metrics_context_base.all_model_predictions,
                    ensemble_predictions=metrics_context_base.ensemble_predictions,
                )
                metric_results = self.config.compute_metrics(
                    self.config.metrics,
                    by_model_preds.get(model_id, []),
                    by_model_score_dicts.get(model_id, []),
                    ctx,
                )
                summary.update(metric_results)

            snapshot = SnapshotRecord(
                id=f"SNAP_{model_id}_{now.strftime('%Y%m%d_%H%M%S')}",
                model_id=model_id,
                period_start=min(
                    s.scored_at
                    for s in scored
                    if pred_map.get(s.prediction_id) == model_id
                ),
                period_end=now,
                prediction_count=len(results),
                result_summary=summary,
                created_at=now,
            )
            self.snapshot_repository.save(snapshot)
            written_snapshots.append(snapshot)

        self.logger.info("Wrote %d snapshots", len(by_model_scores))

        # Merkle tamper evidence: commit cycle
        if self.merkle_service and written_snapshots:
            try:
                self.merkle_service.commit_cycle(written_snapshots, now)
            except Exception as exc:
                self.logger.warning("Merkle cycle commit failed: %s", exc)

        return written_snapshots

    # ── 4. ensemble computation ──

    def _compute_ensembles(self, scored: list[ScoreRecord], now: datetime) -> None:
        """Compute ensemble predictions for all enabled ensemble configs."""
        if not self.config.ensembles:
            return

        from crunch_node.metrics.context import MetricsContext
        from crunch_node.services.ensemble import (
            apply_model_filter,
            build_ensemble_predictions,
            ensemble_model_id,
            is_ensemble_model,
        )

        # Gather current model predictions and metrics from latest snapshots
        predictions = self.prediction_repository.find(status=PredictionStatus.SCORED)
        pred_map: dict[str, str] = {}
        for p in predictions:
            pred_map[p.id] = p.model_id

        by_model_preds: dict[str, list[dict[str, Any]]] = {}
        for p in predictions:
            if is_ensemble_model(p.model_id):
                continue
            by_model_preds.setdefault(p.model_id, []).append(
                {
                    "inference_output": p.inference_output,
                    "performed_at": p.performed_at,
                    "scope": p.scope,
                    "input_id": p.input_id,
                    "scope_key": p.scope_key,
                }
            )

        # Get metrics from latest snapshots
        all_snapshots = (
            self.snapshot_repository.find() if self.snapshot_repository else []
        )
        model_metrics: dict[str, dict[str, float]] = {}
        for snap in all_snapshots:
            if not is_ensemble_model(snap.model_id):
                model_metrics[snap.model_id] = {
                    k: float(v)
                    for k, v in snap.result_summary.items()
                    if isinstance(v, (int, float))
                }

        ensemble_predictions_map: dict[str, list[dict[str, Any]]] = {}

        for ens_config in self.config.ensembles:
            if not ens_config.enabled:
                continue

            # Filter models
            filtered_preds = apply_model_filter(
                ens_config.model_filter,
                model_metrics,
                by_model_preds,
            )

            if not filtered_preds:
                self.logger.info(
                    "Ensemble %r: no models after filtering", ens_config.name
                )
                continue

            # Compute weights
            strategy = ens_config.strategy
            if strategy is None:
                from crunch_node.services.ensemble import inverse_variance

                strategy = inverse_variance

            weights = strategy(model_metrics, filtered_preds)

            # Build ensemble predictions
            ens_preds = build_ensemble_predictions(
                ens_config.name,
                weights,
                filtered_preds,
                now,
            )

            if not ens_preds:
                continue

            # Save ensemble predictions
            for ep in ens_preds:
                self.prediction_repository.save(ep)

            # Score ensemble predictions against actuals
            ens_scored: list[ScoreRecord] = []
            for ep in ens_preds:
                actuals_dict = self._resolve_actuals(ep)
                if actuals_dict is not None:
                    typed_output = self._coerce_output(ep.inference_output)
                    typed_gt = self._coerce_ground_truth(actuals_dict)
                    result = self.scoring_function(typed_output, typed_gt)
                    result_dict = (
                        result.model_dump() if isinstance(result, BaseModel) else result
                    )
                    validated = self.config.score_type.model_validate(result_dict)
                    score = ScoreRecord(
                        id=f"SCR_{ep.id}",
                        prediction_id=ep.id,
                        result=validated.model_dump(),
                        success=True,
                        scored_at=now,
                    )
                    if self.score_repository is not None:
                        self.score_repository.save(score)
                    ens_scored.append(score)

            # Store ensemble prediction dicts for metrics context
            ens_pred_dicts = [
                {
                    "inference_output": ep.inference_output,
                    "performed_at": ep.performed_at,
                    "scope": ep.scope,
                    "input_id": ep.input_id,
                    "scope_key": ep.scope_key,
                }
                for ep in ens_preds
            ]
            ensemble_predictions_map[ens_config.name] = ens_pred_dicts

            # Write ensemble snapshots
            if ens_scored and self.snapshot_repository:
                ens_model_id = ensemble_model_id(ens_config.name)
                results = [s.result for s in ens_scored]
                summary = self.config.aggregate_snapshot(results)

                # Compute metrics for the ensemble too
                if self.config.metrics:
                    ctx = MetricsContext(
                        model_id=ens_model_id,
                        window_start=min(
                            (s.scored_at for s in ens_scored), default=now
                        ),
                        window_end=now,
                        all_model_predictions=by_model_preds,
                        ensemble_predictions=ensemble_predictions_map,
                    )
                    ens_score_dicts = [
                        {"result": s.result, "scored_at": s.scored_at}
                        for s in ens_scored
                    ]
                    metric_results = self.config.compute_metrics(
                        self.config.metrics,
                        ens_pred_dicts,
                        ens_score_dicts,
                        ctx,
                    )
                    summary.update(metric_results)

                snapshot = SnapshotRecord(
                    id=f"SNAP_{ens_model_id}_{now.strftime('%Y%m%d_%H%M%S')}",
                    model_id=ens_model_id,
                    period_start=min(s.scored_at for s in ens_scored),
                    period_end=now,
                    prediction_count=len(ens_scored),
                    result_summary=summary,
                    created_at=now,
                )
                self.snapshot_repository.save(snapshot)

            self.logger.info(
                "Ensemble %r: %d models, %d predictions, weights=%s",
                ens_config.name,
                len(weights),
                len(ens_preds),
                {m: round(w, 3) for m, w in weights.items()},
            )

    # ── 5. checkpoint ──

    def _maybe_checkpoint(self, now: datetime) -> None:
        """Create a checkpoint if the checkpoint interval has elapsed."""
        if self._checkpoint_service is None:
            return

        if self._last_checkpoint_at is None:
            # On first run, check if there's an existing checkpoint
            latest = self._checkpoint_service.checkpoint_repository.get_latest()
            self._last_checkpoint_at = (
                latest.period_end if latest else datetime.min.replace(tzinfo=UTC)
            )

        elapsed = (now - self._last_checkpoint_at).total_seconds()
        if elapsed < self.checkpoint_interval_seconds:
            return

        try:
            checkpoint = self._checkpoint_service.create_checkpoint()
            if checkpoint is not None:
                self._last_checkpoint_at = now
        except Exception as exc:
            self.logger.exception("Checkpoint creation failed: %s", exc)

    # ── 6. leaderboard ──

    def _rebuild_leaderboard(self) -> None:
        models = self.model_repository.fetch_all()
        snapshots = self.snapshot_repository.find() if self.snapshot_repository else []

        aggregated = self._aggregate_from_snapshots(snapshots, models)
        ranked = self._rank(aggregated)

        self.leaderboard_repository.save(
            ranked,
            meta={"generated_by": "crunch_node.score_service"},
        )

    def _aggregate_from_snapshots(
        self, snapshots: list[SnapshotRecord], models: dict
    ) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        aggregation = self.config.aggregation

        # Group snapshots by model
        by_model: dict[str, list[SnapshotRecord]] = {}
        for snap in snapshots:
            by_model.setdefault(snap.model_id, []).append(snap)

        entries: list[dict[str, Any]] = []
        for model_id, model_snapshots in by_model.items():
            metrics: dict[str, float] = {}

            # Windowed aggregation: average the value_field per window
            for window_name, window in aggregation.windows.items():
                cutoff = now - timedelta(hours=window.hours)
                window_snaps = [
                    s
                    for s in model_snapshots
                    if self._ensure_utc(s.period_end) >= cutoff
                ]
                if window_snaps:
                    vals = [
                        float(s.result_summary.get(aggregation.value_field, 0))
                        for s in window_snaps
                    ]
                    metrics[window_name] = sum(vals) / len(vals)
                else:
                    metrics[window_name] = 0.0

            # Include ALL numeric fields from the latest snapshot so custom
            # score_type fields (net_pnl, drawdown_pct, etc.) appear in the
            # leaderboard and report endpoints.
            latest_snap = max(
                model_snapshots, key=lambda s: self._ensure_utc(s.period_end)
            )
            for key, value in latest_snap.result_summary.items():
                if key not in metrics:
                    try:
                        metrics[key] = float(value)
                    except (ValueError, TypeError):
                        pass

            model = models.get(model_id)
            entry: dict[str, Any] = {
                "model_id": model_id,
                "score": {
                    "metrics": metrics,
                    "ranking": {
                        "key": aggregation.ranking_key,
                        "value": metrics.get(aggregation.ranking_key, 0.0),
                        "direction": aggregation.ranking_direction,
                    },
                },
            }
            if model:
                entry["model_name"] = model.name
                entry["cruncher_name"] = model.player_name
            entries.append(entry)

        return entries

    def _rank(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        key = self.config.aggregation.ranking_key
        reverse = self.config.aggregation.ranking_direction == "desc"

        def sort_key(e: dict[str, Any]) -> float:
            score = e.get("score")
            if not isinstance(score, dict):
                return float("-inf")
            try:
                return float((score.get("metrics") or {}).get(key, 0.0))
            except Exception:
                return float("-inf")

        ranked = sorted(entries, key=sort_key, reverse=reverse)
        for idx, entry in enumerate(ranked, start=1):
            entry["rank"] = idx
        return ranked

    _rank_leaderboard = _rank

    @staticmethod
    def _ensure_utc(dt: datetime) -> datetime:
        """Ensure a datetime is timezone-aware (assume UTC if naive)."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt

    def _rollback_repositories(self) -> None:
        for name, repo in [
            ("input", self.input_repository),
            ("prediction", self.prediction_repository),
            ("score", self.score_repository),
            ("snapshot", self.snapshot_repository),
            ("model", self.model_repository),
            ("leaderboard", self.leaderboard_repository),
        ]:
            rollback = getattr(repo, "rollback", None)
            if callable(rollback):
                try:
                    rollback()
                except Exception as exc:
                    self.logger.warning("Rollback failed for %s: %s", name, exc)

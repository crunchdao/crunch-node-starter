"""Realtime predict service: event-driven loop, config-based scheduling."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from crunch_node.entities.prediction import (
    InputRecord,
    PredictionRecord,
    PredictionStatus,
)
from crunch_node.schemas import ScheduleEnvelope
from crunch_node.services.predict import PredictService


class RealtimePredictService(PredictService):
    def __init__(
        self,
        checkpoint_interval_seconds: int = 60 * 60,
        post_predict_hook: (
            Callable[
                [list[PredictionRecord], InputRecord, datetime],
                list[PredictionRecord],
            ]
            | None
        ) = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.checkpoint_interval_seconds = checkpoint_interval_seconds
        self.post_predict_hook = post_predict_hook
        self._next_run: dict[str, datetime] = {}

    @staticmethod
    def validate_prediction_configs(
        configs: list[dict[str, Any]],
        feed_poll_seconds: float,
    ) -> None:
        """Validate prediction configs against feed timing constraints.

        Raises ValueError if any active config has resolve_horizon_seconds
        that is too low to accumulate feed data for ground truth resolution.
        resolve_horizon_seconds=0 is valid (immediate resolution, e.g. live trading).
        Must be called at startup to fail fast.
        """
        for config in configs:
            if not config.get("active", True):
                continue

            schedule = config.get("schedule") or {}
            resolve_horizon = schedule.get("resolve_horizon_seconds", 0)
            scope_key = config.get("scope_key", "<unknown>")

            # 0 means immediate resolution (live trading) - skip feed timing check
            if resolve_horizon == 0:
                continue

            if resolve_horizon < feed_poll_seconds:
                raise ValueError(
                    f"Config '{scope_key}': resolve_horizon_seconds={resolve_horizon} "
                    f"is less than feed_poll_seconds={feed_poll_seconds}. "
                    f"Predictions will never accumulate enough feed data to "
                    f"resolve ground truth. All scores will be 0. "
                    f"Set resolve_horizon_seconds >= {feed_poll_seconds} "
                    f"or use 0 for immediate resolution (live trading)."
                )

    # ── main loop ──

    async def run(self) -> None:
        self.logger.info("realtime predict service started")
        while not self.stop_event.is_set():
            try:
                # Wait for data and capture notification time + payload
                payload = await self._wait_for_data()
                notify_received_us = time.time_ns() // 1000

                # Extract feed timing from notify payload for cross-process timing
                feed_timing: dict[str, int] = {}
                if payload:
                    try:
                        import json

                        feed_timing = json.loads(payload)
                    except (ValueError, json.JSONDecodeError):
                        pass

                await self.run_once(
                    notify_received_us=notify_received_us,
                    feed_timing=feed_timing,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.exception("predict loop error: %s", exc)

    async def run_once(
        self,
        raw_input: dict[str, Any] | None = None,
        now: datetime | None = None,
        notify_received_us: int | None = None,
        feed_timing: dict[str, int] | None = None,
    ) -> bool:
        now = now or datetime.now(UTC)
        await self.init_runner()

        # 1. get data → tick models
        if raw_input is not None:
            validated = self.contract.raw_input_type.model_validate(raw_input)
            data = validated.model_dump()
            inp = InputRecord(
                id=f"INP_{now.strftime('%Y%m%d_%H%M%S.%f')[:-3]}",
                raw_data=data,
                received_at=now,
            )
        else:
            inp = self.get_data(now)

        data_loaded_us = time.time_ns() // 1000

        # Use feed timing from notify payload for accurate cross-process measurement
        # This overwrites any stale timing from old feed records in the DB
        if feed_timing:
            if "feed_received_us" in feed_timing:
                inp._timing["feed_received_us"] = feed_timing["feed_received_us"]
            if "feed_normalized_us" in feed_timing:
                inp._timing["feed_normalized_us"] = feed_timing["feed_normalized_us"]
            if "feed_persisted_us" in feed_timing:
                inp._timing["feed_persisted_us"] = feed_timing["feed_persisted_us"]
                inp._timing["notify_sent_us"] = feed_timing["feed_persisted_us"]
        if notify_received_us is not None:
            inp._timing["notify_received_us"] = notify_received_us
        inp._timing["data_loaded_us"] = data_loaded_us

        await self._tick_models(inp.raw_data)

        # 2. run configs → build records → save
        predictions = await self._predict_all_configs(inp, now)

        # Add callback timing (store in meta["timing"] for persistence)
        if self.post_predict_hook is not None:
            callback_started_us = time.time_ns() // 1000
            # Copy timing to all predictions
            for prediction in predictions:
                pred_timing = prediction.meta.setdefault("timing", {})
                pred_timing.update(inp._timing)
                pred_timing["callback_started_us"] = callback_started_us

            predictions = self.post_predict_hook(predictions, inp, now)

            callback_completed_us = time.time_ns() // 1000
            # Update callback completion timing
            for prediction in predictions:
                prediction.meta.setdefault("timing", {})["callback_completed_us"] = (
                    callback_completed_us
                )
        else:
            # No callback - copy timing to predictions and mark callback as skipped
            callback_completed_us = time.time_ns() // 1000
            for prediction in predictions:
                pred_timing = prediction.meta.setdefault("timing", {})
                pred_timing.update(inp._timing)
                pred_timing["callback_started_us"] = callback_completed_us
                pred_timing["callback_completed_us"] = callback_completed_us

        self._save(predictions)
        return len(predictions) > 0

    def _save(self, predictions: list[PredictionRecord]) -> None:
        """Override parent _save to add timing data before persistence."""
        if not predictions:
            return

        # Add persistence timing to meta (will be persisted to DB)
        persistence_completed_us = time.time_ns() // 1000

        for prediction in predictions:
            prediction.meta.setdefault("timing", {})["persistence_completed_us"] = (
                persistence_completed_us
            )

        # Call parent save method
        super()._save(predictions)

    # ── predict across configs ──

    async def _predict_all_configs(
        self, inp: InputRecord, now: datetime
    ) -> list[PredictionRecord]:
        configs = self._fetch_active_configs()
        if not configs:
            self.logger.info("No active prediction configs found")
            return []

        all_predictions: list[PredictionRecord] = []

        for config in configs:
            if not config.get("active", True):
                continue

            schedule = ScheduleEnvelope.model_validate(config.get("schedule") or {})
            config_id = str(config.get("id") or self._config_key(config))

            if now < self._next_run.get(config_id, now):
                continue

            # scope + timing
            resolve_seconds = int(schedule.resolve_horizon_seconds or 0)
            scope = {
                "scope_key": str(config.get("scope_key") or "default-scope"),
                **self.contract.scope.model_dump(),
                **(config.get("scope_template") or {}),
                "resolve_horizon_seconds": resolve_seconds,
            }
            scope_key = scope["scope_key"]
            resolvable_at = now + timedelta(seconds=max(0, resolve_seconds))

            # Include feed dimensions in scope so score worker can query matching records
            if self.feed_reader is not None:
                scope.setdefault("source", self.feed_reader.source)
                scope.setdefault("subject", self.feed_reader.subject)
                scope.setdefault("kind", self.feed_reader.kind)
                scope.setdefault("granularity", self.feed_reader.granularity)

            # Save input once (dumb log)
            if self.input_repository is not None:
                self.input_repository.save(inp)

            # call models
            models_dispatched_us = time.time_ns() // 1000
            responses = await self._call_models(scope)
            models_completed_us = time.time_ns() // 1000
            seen: set[str] = set()

            for model_run, result in responses.items():
                model = self._to_model(model_run)
                self.register_model(model)
                seen.add(model.id)

                raw_status = getattr(result, "status", "UNKNOWN")
                runner_status = (
                    str(raw_status.value)
                    if hasattr(raw_status, "value")
                    else str(raw_status)
                )

                output = getattr(result, "result", {})
                output = output if isinstance(output, dict) else {"result": output}

                validation_error = self.validate_output(output)
                if validation_error:
                    status = PredictionStatus.FAILED
                    output = {
                        "_validation_error": validation_error,
                        "raw_output": output,
                    }
                elif runner_status == "SUCCESS":
                    status = PredictionStatus.PENDING
                else:
                    status = (
                        PredictionStatus(runner_status)
                        if runner_status in PredictionStatus.__members__
                        else PredictionStatus.FAILED
                    )

                # Create timing data for this prediction
                prediction_timing = inp._timing.copy()
                prediction_timing["models_dispatched_us"] = models_dispatched_us
                prediction_timing["models_completed_us"] = models_completed_us

                all_predictions.append(
                    self._build_record(
                        model_id=model.id,
                        input_id=inp.id,
                        scope_key=scope_key,
                        scope=scope,
                        status=status,
                        output=output,
                        now=now,
                        resolvable_at=resolvable_at,
                        exec_time_ms=float(getattr(result, "exec_time_us", 0.0)),
                        config_id=config_id,
                        timing_data=prediction_timing,
                    )
                )

            # absent models
            for model_id in self._known_models:
                if model_id not in seen:
                    # Create timing data for absent model
                    absent_timing = inp._timing.copy()
                    absent_timing["models_dispatched_us"] = models_dispatched_us
                    absent_timing["models_completed_us"] = models_completed_us

                    all_predictions.append(
                        self._build_record(
                            model_id=model_id,
                            input_id=inp.id,
                            scope_key=scope_key,
                            scope=scope,
                            status=PredictionStatus.ABSENT,
                            output={},
                            now=now,
                            resolvable_at=resolvable_at,
                            config_id=config_id,
                            timing_data=absent_timing,
                        )
                    )

            self._next_run[config_id] = now + timedelta(
                seconds=int(schedule.prediction_interval_seconds)
            )

        return all_predictions

    # ── event-driven wait ──

    async def _wait_for_data(self) -> str:
        """Wait for pg NOTIFY or fall back to polling timeout.

        Returns the notification payload (feed_persisted_us as string) or empty string.
        """
        timeout = float(self.checkpoint_interval_seconds)
        try:
            from crunch_node.db.pg_notify import wait_for_notify

            result = await self._race_stop(
                wait_for_notify("new_feed_data", timeout=timeout)
            )
            if result is not None:
                notified, payload = result
                return payload if notified else ""
            return ""
        except Exception:
            await self._race_stop(asyncio.sleep(timeout))
            return ""

    async def _race_stop(self, coro: Any) -> Any:
        """Run coro until it completes or stop_event fires. Returns coro result or None."""
        task = asyncio.create_task(coro)
        stop = asyncio.create_task(self.stop_event.wait())
        done, pending = await asyncio.wait(
            {task, stop}, return_when=asyncio.FIRST_COMPLETED
        )
        for p in pending:
            p.cancel()
            try:
                await p
            except (asyncio.CancelledError, Exception):
                pass

        if task in done:
            return task.result()
        return None

    # ── helpers ──

    def _fetch_active_configs(self) -> list[dict[str, Any]]:
        if hasattr(self.prediction_repository, "fetch_active_configs"):
            return list(self.prediction_repository.fetch_active_configs())
        return []

    @staticmethod
    def _config_key(config: dict[str, Any]) -> str:
        scope_key = str(config.get("scope_key") or "default-scope")
        interval = (config.get("schedule") or {}).get("prediction_interval_seconds")
        return f"{scope_key}-{interval}"

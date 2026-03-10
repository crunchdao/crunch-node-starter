from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal

from crunch_node.entities.prediction import InputRecord, PredictionRecord
from crunch_node.feeds.contracts import FeedDataRecord
from crunch_node.services.trading.config import TradingConfig
from crunch_node.services.trading.simulator import TradingEngine

logger = logging.getLogger(__name__)


class SimulatorSink:
    def __init__(
        self,
        simulator: TradingEngine,
        state_repository: Any,
        trading_config: TradingConfig,
        model_ids: list[str] | None = None,
        signal_mode: Literal["delta", "target", "order"] = "delta",
    ) -> None:
        self._simulator = simulator
        self._state_repository = state_repository
        self._model_ids: set[str] = set(model_ids) if model_ids else set()
        self._signal_mode = signal_mode
        self._trading_config = trading_config
        self._last_price: dict[str, float] = {}

    async def on_record(self, record: FeedDataRecord) -> None:
        price = self.extract_price(record)
        if price is None:
            return
        self._last_price[record.subject] = price
        ts = datetime.fromtimestamp(record.ts_event / 1000, tz=UTC)
        self._simulator.mark_to_market(record.subject, price, ts)

    @staticmethod
    def extract_price(record: FeedDataRecord) -> float | None:
        price = record.values.get("close")
        if price is not None:
            return float(price)
        price = record.values.get("price")
        if price is not None:
            return float(price)
        return None

    def on_predictions(
        self,
        predictions: list[PredictionRecord],
        input_record: InputRecord,
        now: Any,
    ) -> list[PredictionRecord]:
        """post_predict_hook: forward model signals as orders to the simulator."""
        ts = now if isinstance(now, datetime) else datetime.now(UTC)

        dirty_model_ids: set[str] = set()

        for pred in predictions:
            asset = pred.scope.get("subject")
            if not asset:
                continue

            # Map asset name to trading pair subject for price lookup
            trading_pair = self._trading_config.asset_price_mapping.get(asset, asset)
            price = self._last_price.get(trading_pair)
            if price is None:
                logger.warning(
                    "No price for asset %s (trading pair %s), skipping order for %s",
                    asset,
                    trading_pair,
                    pred.model_id,
                )
                continue
            if "_validation_error" in pred.inference_output:
                continue
            try:
                self.apply_signal(
                    pred.model_id,
                    asset,
                    pred.inference_output,
                    price=price,
                    timestamp=ts,
                )
            except ValueError as exc:
                logger.warning(
                    "Skipping order for %s/%s: %s",
                    pred.model_id,
                    asset,
                    exc,
                )
                continue
            dirty_model_ids.add(pred.model_id)
            self._model_ids.add(pred.model_id)

        self._persist_state(dirty_model_ids)
        return predictions

    def apply_signal(
        self,
        model_id: str,
        subject: str,
        inference_output: dict[str, Any],
        *,
        price: float,
        timestamp: datetime,
    ) -> None:
        if self._signal_mode == "delta":
            direction = inference_output.get("direction")
            size = inference_output.get("leverage")
            if direction is None or size is None:
                raise ValueError(
                    "Delta mode requires 'direction' and 'leverage' in inference_output, "
                    f"got: {list(inference_output.keys())}"
                )
            self._simulator.apply_order(
                model_id,
                subject,
                direction,
                float(size),
                price=price,
                timestamp=timestamp,
            )
            return

        if self._signal_mode == "order":
            action = inference_output.get("action")
            amount = inference_output.get("amount")
            if action is None or amount is None:
                raise ValueError(
                    "Order mode requires 'action' and 'amount' in inference_output, "
                    f"got: {list(inference_output.keys())}"
                )
            amount = float(amount)
            if amount <= 0:
                return
            direction = "long" if action == "buy" else "short"
            self._simulator.apply_order(
                model_id,
                subject,
                direction,
                amount,
                price=price,
                timestamp=timestamp,
            )
            return

        signal = inference_output.get("signal")
        if signal is None:
            logger.warning(
                "Target mode inference_output missing 'signal' for %s/%s, skipping",
                model_id,
                subject,
            )
            return
        signal = float(signal)

        target_direction = "long" if signal > 0 else "short"
        target_size = abs(signal)

        current = self._simulator.get_position(model_id, subject)

        if current is None:
            if target_size > 0:
                self._simulator.apply_order(
                    model_id,
                    subject,
                    target_direction,
                    target_size,
                    price=price,
                    timestamp=timestamp,
                )
            return

        if signal == 0:
            opposite = "short" if current.direction == "long" else "long"
            self._simulator.apply_order(
                model_id,
                subject,
                opposite,
                current.size,
                price=price,
                timestamp=timestamp,
            )
            return

        if current.direction == target_direction:
            delta = target_size - current.size
            if delta > 0:
                self._simulator.apply_order(
                    model_id,
                    subject,
                    target_direction,
                    delta,
                    price=price,
                    timestamp=timestamp,
                )
            elif delta < 0:
                opposite = "short" if target_direction == "long" else "long"
                self._simulator.apply_order(
                    model_id,
                    subject,
                    opposite,
                    abs(delta),
                    price=price,
                    timestamp=timestamp,
                )
        else:
            self._simulator.apply_order(
                model_id,
                subject,
                target_direction,
                current.size + target_size,
                price=price,
                timestamp=timestamp,
            )

    def _persist_state(self, model_ids: set[str] | None = None) -> None:
        for model_id in model_ids if model_ids is not None else self._model_ids:
            state = self._simulator.get_full_state(model_id)
            self._state_repository.save_state(
                model_id,
                state["positions"],
                state["trades"],
                portfolio_fees=state["portfolio_fees"],
                closed_carry=state["closed_carry"],
            )

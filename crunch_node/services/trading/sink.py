from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from crunch_node.entities.prediction import InputRecord, PredictionRecord
from crunch_node.feeds.contracts import FeedDataRecord
from crunch_node.services.trading.simulator import TradingSimulator

logger = logging.getLogger(__name__)


class SimulatorSink:
    def __init__(
        self,
        simulator: TradingSimulator,
        snapshot_repository: Any,
        model_ids: list[str] | None = None,
    ) -> None:
        self._simulator = simulator
        self._snapshot_repository = snapshot_repository
        self._model_ids = model_ids or []

    async def on_record(self, record: FeedDataRecord) -> None:
        price = self.extract_price(record)
        if price is None:
            return
        ts = datetime.fromtimestamp(record.ts_event / 1000, tz=UTC)
        self._simulator.mark_to_market(record.subject, price, ts)
        self._write_snapshots(ts)

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
        price = input_record.raw_data.get("close") or input_record.raw_data.get("price")
        if price is None:
            logger.warning("No price in input_record, skipping order forwarding")
            return predictions

        price = float(price)
        ts = now if isinstance(now, datetime) else datetime.now(UTC)

        for pred in predictions:
            subject = pred.scope.get("subject")
            if not subject:
                continue
            output = pred.inference_output
            direction = output.get("direction")
            leverage = output.get("leverage")
            if direction and leverage:
                self._simulator.apply_order(
                    pred.model_id, subject, direction, float(leverage),
                    price=price, timestamp=ts,
                )
                if pred.model_id not in self._model_ids:
                    self._model_ids.append(pred.model_id)

        return predictions

    def _write_snapshots(self, timestamp: datetime) -> None:
        from crunch_node.entities.prediction import SnapshotRecord

        for model_id in self._model_ids:
            snapshot_data = self._simulator.get_portfolio_snapshot(model_id, timestamp)
            snapshot = SnapshotRecord(
                id=str(uuid.uuid4()),
                model_id=model_id,
                period_start=timestamp,
                period_end=timestamp,
                prediction_count=len(snapshot_data.get("positions", [])),
                result_summary={
                    "net_pnl": snapshot_data["net_pnl"],
                    "total_unrealized_pnl": snapshot_data["total_unrealized_pnl"],
                    "total_fees": snapshot_data["total_fees"],
                },
            )
            self._snapshot_repository.save(snapshot)

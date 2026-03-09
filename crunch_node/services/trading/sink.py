from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

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

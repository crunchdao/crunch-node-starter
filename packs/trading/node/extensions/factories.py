from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from crunch_node.entities.prediction import SnapshotRecord
from crunch_node.id_prefixes import SNAPSHOT_PREFIX
from extensions.config import TradingConfig
from extensions.simulator import TradingEngine
from extensions.sink import SimulatorSink
from extensions.state_repository import TradingStateRepository

logger = logging.getLogger(__name__)


def build_prediction_sink(*, session, config) -> SimulatorSink:
    trading: TradingConfig = config.trading

    simulator = TradingEngine(
        cost_model=trading.cost_model,
        max_position_size=trading.max_position_size,
        max_portfolio_size=trading.max_portfolio_size,
    )
    state_repo = TradingStateRepository(session)

    model_ids = state_repo.get_all_model_ids()
    for model_id in model_ids:
        state = state_repo.load_state(model_id)
        if state is not None:
            simulator.load_state(model_id, state)
            logger.info("Restored trading state for model %s", model_id)

    sink = SimulatorSink(
        simulator=simulator,
        state_repository=state_repo,
        trading_config=trading,
        model_ids=model_ids,
        signal_mode=trading.signal_mode,
    )
    logger.info("Trading engine enabled: %s", trading)
    return sink


class TradingStrategy:
    def __init__(self, state_repository, snapshot_repository):
        self._state_repo = state_repository
        self._snapshot_repo = snapshot_repository

    def produce_snapshots(self, now: datetime) -> list[SnapshotRecord]:
        model_ids = self._state_repo.get_all_model_ids()
        if not model_ids:
            return []

        snapshots: list[SnapshotRecord] = []
        for model_id in model_ids:
            state = self._state_repo.load_state(model_id)
            if state is None:
                continue

            positions_data = state.get("positions", [])
            trades_data = state.get("trades", [])
            portfolio_fees = state.get("portfolio_fees", 0.0)
            closed_carry = state.get("closed_carry", 0.0)

            total_unrealized = 0.0
            for p in positions_data:
                entry = p["entry_price"]
                current = p.get("current_price", entry)
                size = p["size"]
                if entry > 0:
                    price_return = (current - entry) / entry
                    if p["direction"] == "short":
                        price_return = -price_return
                    total_unrealized += size * price_return

            total_realized = sum(t.get("realized_pnl", 0.0) or 0.0 for t in trades_data)
            total_carry = (
                sum(p.get("accrued_carry", 0.0) for p in positions_data) + closed_carry
            )
            net_pnl = total_unrealized + total_realized - portfolio_fees - total_carry

            result_summary: dict[str, Any] = {
                "net_pnl": net_pnl,
                "unrealized_pnl": total_unrealized,
                "realized_pnl": total_realized,
                "total_fees": portfolio_fees,
                "total_carry_costs": total_carry,
                "open_position_count": len(positions_data),
            }

            result_summary.update(
                _compute_trading_metrics(
                    self._snapshot_repo, model_id, net_pnl, trades_data
                )
            )

            snapshot = SnapshotRecord(
                id=f"{SNAPSHOT_PREFIX}{model_id}_{now.strftime('%Y%m%d_%H%M%S')}",
                model_id=model_id,
                period_start=now,
                period_end=now,
                prediction_count=len(positions_data),
                result_summary=result_summary,
            )
            self._snapshot_repo.save(snapshot)
            snapshots.append(snapshot)

        return snapshots

    def rollback(self) -> None:
        pass


def build_score_snapshots(*, session, config, snapshot_repository) -> TradingStrategy:
    state_repo = TradingStateRepository(session)
    return TradingStrategy(state_repo, snapshot_repository)


def _compute_trading_metrics(
    snapshot_repository,
    model_id: str,
    current_net_pnl: float,
    trades: list[dict[str, Any]],
) -> dict[str, float]:
    metrics: dict[str, float] = {}

    historical = snapshot_repository.find(model_id=model_id)
    pnl_series = [float(s.result_summary.get("net_pnl", 0.0)) for s in historical]
    pnl_series.append(current_net_pnl)

    metrics["max_drawdown"] = _max_drawdown(pnl_series)

    profitable = sum(1 for t in trades if (t.get("realized_pnl") or 0.0) > 0)
    metrics["hit_rate"] = profitable / len(trades) if trades else 0.0

    metrics["sortino_ratio"] = _sortino_ratio(pnl_series)

    return metrics


def _max_drawdown(pnl_series: list[float]) -> float:
    if len(pnl_series) < 2:
        return 0.0
    peak = pnl_series[0]
    max_dd = 0.0
    for pnl in pnl_series[1:]:
        if pnl > peak:
            peak = pnl
        dd = peak - pnl
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _sortino_ratio(pnl_series: list[float]) -> float:
    if len(pnl_series) < 2:
        return 0.0
    returns = [pnl_series[i] - pnl_series[i - 1] for i in range(1, len(pnl_series))]
    mean_return = sum(returns) / len(returns)
    downside = [r for r in returns if r < 0]
    if not downside:
        return 0.0
    downside_var = sum(r * r for r in downside) / len(downside)
    downside_std = downside_var**0.5
    if downside_std < 1e-12:
        return 0.0
    return mean_return / downside_std


def build_trading_widgets() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "type": "CHART",
            "displayName": "P&L Over Time",
            "tooltip": "Net profit/loss per model including fees and carry costs",
            "order": 10,
            "endpointUrl": "/reports/models/metrics",
            "nativeConfiguration": {
                "type": "line",
                "xAxis": {"name": "performed_at"},
                "yAxis": {
                    "series": [{"name": "net_pnl", "label": "Net PnL"}],
                    "format": "decimal-4",
                },
                "displayEvolution": False,
            },
        },
        {
            "id": 2,
            "type": "CHART",
            "displayName": "P&L Breakdown",
            "tooltip": "Realized vs unrealized PnL, fees, and carry costs over time",
            "order": 20,
            "endpointUrl": "/reports/models/metrics",
            "nativeConfiguration": {
                "type": "line",
                "xAxis": {"name": "performed_at"},
                "yAxis": {
                    "series": [
                        {"name": "realized_pnl", "label": "Realized PnL"},
                        {"name": "unrealized_pnl", "label": "Unrealized PnL"},
                        {"name": "total_fees", "label": "Fees"},
                        {"name": "total_carry_costs", "label": "Carry Costs"},
                    ],
                    "format": "decimal-4",
                },
                "displayEvolution": False,
            },
        },
        {
            "id": 3,
            "type": "CHART",
            "displayName": "Max Drawdown",
            "tooltip": "Worst peak-to-trough drawdown on cumulative PnL",
            "order": 30,
            "endpointUrl": "/reports/models/metrics",
            "nativeConfiguration": {
                "type": "line",
                "xAxis": {"name": "performed_at"},
                "yAxis": {
                    "series": [
                        {"name": "max_drawdown", "label": "Max Drawdown"},
                    ],
                    "format": "decimal-4",
                },
                "displayEvolution": False,
            },
        },
        {
            "id": 4,
            "type": "CHART",
            "displayName": "Sortino Ratio",
            "tooltip": "Like Sharpe but only penalizes downside volatility",
            "order": 35,
            "endpointUrl": "/reports/models/metrics",
            "nativeConfiguration": {
                "type": "line",
                "xAxis": {"name": "performed_at"},
                "yAxis": {
                    "series": [
                        {"name": "sortino_ratio", "label": "Sortino Ratio"},
                    ],
                    "format": "decimal-2",
                },
                "displayEvolution": False,
            },
        },
        {
            "id": 5,
            "type": "CHART",
            "displayName": "Open Positions",
            "tooltip": "Number of open positions per model over time",
            "order": 40,
            "endpointUrl": "/reports/models/metrics",
            "nativeConfiguration": {
                "type": "line",
                "xAxis": {"name": "performed_at"},
                "yAxis": {
                    "series": [
                        {"name": "open_position_count", "label": "Positions"},
                    ],
                    "format": "decimal-0",
                },
                "displayEvolution": False,
            },
        },
    ]

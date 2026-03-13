from __future__ import annotations

import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from crunch_node.services.parquet_sink import ParquetBackfillSink

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from crunch_node import __version__
from crunch_node.config.runtime import RuntimeSettings
from crunch_node.config_loader import load_config
from crunch_node.crunch_config import CrunchConfig
from crunch_node.db import (
    DBBackfillJobRepository,
    DBCheckpointRepository,
    DBFeedRecordRepository,
    DBLeaderboardRepository,
    DBMerkleCycleRepository,
    DBMerkleNodeRepository,
    DBModelRepository,
    DBPredictionRepository,
    DBSnapshotRepository,
    create_session,
)
from crunch_node.entities.prediction import CheckpointStatus
from crunch_node.merkle.service import MerkleService
from crunch_node.schemas import ReportSchemaEnvelope

app = FastAPI(title="Node Template Report Worker")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CONTRACT = load_config()
SETTINGS = RuntimeSettings.from_env()

# API key auth — active when API_KEY env var is set
from crunch_node.middleware.auth import configure_auth

configure_auth(app)


@app.get("/info")
def get_node_info() -> dict[str, Any]:
    """Return node identity: crunch address and network."""
    return {
        "crunch_id": SETTINGS.crunch_id,
        "crunch_address": SETTINGS.crunch_pubkey,
        "network": SETTINGS.network,
    }


_METRIC_DISPLAY_NAMES: dict[str, str] = {
    "ic": "IC",
    "ic_sharpe": "IC Sharpe",
    "mean_return": "Mean Return",
    "net_pnl": "Net PnL",
    "hit_rate": "Hit Rate",
    "max_drawdown": "Max Drawdown",
    "sortino_ratio": "Sortino",
    "turnover": "Turnover",
    "model_correlation": "Model Corr",
    "fnc": "FNC",
    "contribution": "Contribution",
    "ensemble_correlation": "Ens. Corr",
}

_METRIC_FORMATS: dict[str, str] = {
    "hit_rate": "decimal-2",
    "sortino_ratio": "decimal-2",
}

_METRIC_TOOLTIPS: dict[str, str] = {
    "ic": "Information Coefficient — Spearman rank correlation between predictions and realized returns",
    "ic_sharpe": "IC Sharpe — mean(IC) / std(IC), rewards consistency",
    "mean_return": "Mean return of a long-short portfolio formed from signals",
    "net_pnl": "Current net profit/loss including fees and carry costs",
    "hit_rate": "Fraction of closed trades with positive realized PnL",
    "max_drawdown": "Worst peak-to-trough drawdown on cumulative PnL",
    "sortino_ratio": "Like Sharpe but only penalizes downside volatility",
    "turnover": "Mean absolute change in signal between consecutive predictions",
    "model_correlation": "Mean pairwise Spearman correlation against other models",
    "fnc": "Feature-Neutral Correlation — IC after orthogonalizing against known factors",
    "contribution": "Leave-one-out contribution to the ensemble",
    "ensemble_correlation": "Correlation to the ensemble output",
}


def _build_standard_widgets(
    series: list[dict[str, str]],
    metric_series: list[dict[str, str]],
    contract: CrunchConfig,
) -> list[dict[str, Any]]:
    """Build metrics widgets for standard (non-trading) competitions."""
    widget_id = 1
    widgets: list[dict[str, Any]] = [
        {
            "id": widget_id,
            "type": "CHART",
            "displayName": "Score Metrics",
            "tooltip": None,
            "order": 10,
            "endpointUrl": "/reports/models/metrics",
            "nativeConfiguration": {
                "type": "line",
                "xAxis": {"name": "performed_at"},
                "yAxis": {"series": series, "format": "decimal-2"},
                "displayEvolution": False,
            },
        },
    ]
    widget_id += 1

    if contract.metrics:
        widgets.append(
            {
                "id": widget_id,
                "type": "CHART",
                "displayName": "Multi-Metric Overview",
                "tooltip": "Portfolio-level metrics computed per model over scoring windows",
                "order": 15,
                "endpointUrl": "/reports/snapshots",
                "nativeConfiguration": {
                    "type": "bar",
                    "xAxis": {"name": "model_id"},
                    "yAxis": {"series": metric_series, "format": "decimal-4"},
                    "displayEvolution": False,
                },
            }
        )
        widget_id += 1

    widgets.extend(
        [
            {
                "id": widget_id,
                "type": "CHART",
                "displayName": "Predictions",
                "tooltip": None,
                "order": 30,
                "endpointUrl": "/reports/predictions",
                "nativeConfiguration": {
                    "type": "line",
                    "xAxis": {"name": "performed_at"},
                    "yAxis": {
                        "series": [{"name": "score_value"}],
                        "format": "decimal-2",
                    },
                    "alertConfig": {
                        "reasonField": "score_failed_reason",
                        "field": "score_success",
                    },
                    "filterConfig": [
                        {
                            "type": "select",
                            "label": "Subject",
                            "property": "subject",
                            "autoSelectFirst": True,
                        },
                        {
                            "type": "select",
                            "label": "Horizon",
                            "property": "horizon",
                            "autoSelectFirst": True,
                        },
                    ],
                    "groupByProperty": "param",
                    "displayEvolution": False,
                },
            },
            {
                "id": widget_id + 1,
                "type": "CHART",
                "displayName": "Rolling score by parameters",
                "tooltip": None,
                "order": 20,
                "endpointUrl": "/reports/models/params",
                "nativeConfiguration": {
                    "type": "line",
                    "xAxis": {"name": "performed_at"},
                    "yAxis": {"series": series, "format": "decimal-2"},
                    "filterConfig": [
                        {
                            "type": "select",
                            "label": "Subject",
                            "property": "subject",
                            "autoSelectFirst": True,
                        },
                        {
                            "type": "select",
                            "label": "Horizon",
                            "property": "horizon",
                            "autoSelectFirst": True,
                        },
                    ],
                    "groupByProperty": "param",
                    "displayEvolution": False,
                },
            },
        ]
    )

    diversity_metrics = [
        m
        for m in contract.metrics
        if m
        in (
            "model_correlation",
            "ensemble_correlation",
            "contribution",
            "fnc",
        )
    ]
    if diversity_metrics:
        diversity_series = [
            {
                "name": m,
                "label": _METRIC_DISPLAY_NAMES.get(m, m.replace("_", " ").title()),
            }
            for m in diversity_metrics
        ]
        diversity_series.append({"name": "diversity_score", "label": "Diversity Score"})
        widgets.append(
            {
                "id": widget_id + 2,
                "type": "CHART",
                "displayName": "Model Diversity",
                "tooltip": "How unique each model is relative to the ensemble",
                "order": 25,
                "endpointUrl": "/reports/diversity",
                "nativeConfiguration": {
                    "type": "bar",
                    "xAxis": {"name": "model_id"},
                    "yAxis": {"series": diversity_series, "format": "decimal-4"},
                    "displayEvolution": False,
                },
            }
        )

    if contract.ensembles:
        ensemble_series = [
            {
                "name": m,
                "label": _METRIC_DISPLAY_NAMES.get(m, m.replace("_", " ").title()),
            }
            for m in contract.metrics[:5]
        ]
        widgets.append(
            {
                "id": widget_id + 3,
                "type": "CHART",
                "displayName": "Ensemble Performance",
                "tooltip": "Ensemble metrics over time — is the collective getting smarter?",
                "order": 16,
                "endpointUrl": "/reports/ensemble/history",
                "nativeConfiguration": {
                    "type": "line",
                    "xAxis": {"name": "period_end"},
                    "yAxis": {"series": ensemble_series, "format": "decimal-4"},
                    "filterConfig": [
                        {
                            "type": "select",
                            "label": "Ensemble",
                            "property": "ensemble_name",
                            "autoSelectFirst": True,
                        },
                    ],
                    "displayEvolution": False,
                },
            }
        )

    widgets.append(
        {
            "id": widget_id + 4,
            "type": "CHART",
            "displayName": "Reward History",
            "tooltip": "Reward distribution per checkpoint period",
            "order": 40,
            "endpointUrl": "/reports/checkpoints/rewards",
            "nativeConfiguration": {
                "type": "bar",
                "xAxis": {"name": "period_end"},
                "yAxis": {
                    "series": [{"name": "reward_pct", "label": "Reward %"}],
                    "format": "decimal-2",
                },
                "groupByProperty": "model_name",
                "displayEvolution": False,
            },
        }
    )

    return widgets


def auto_report_schema(contract: CrunchConfig) -> dict[str, Any]:
    """Auto-generate report schema from the CrunchConfig aggregation + metrics config."""
    aggregation = contract.aggregation

    # Leaderboard columns: Model column + one per aggregation window + one per active metric
    columns: list[dict[str, Any]] = [
        {
            "id": 1,
            "type": "MODEL",
            "property": "model_id",
            "format": None,
            "displayName": "Model",
            "tooltip": None,
            "nativeConfiguration": {"type": "model", "statusProperty": "status"},
            "order": 0,
        },
    ]
    col_id = 2
    for i, (window_name, window) in enumerate(aggregation.windows.items()):
        display = (
            getattr(window, "display_name", None)
            or window_name.replace("_", " ").title()
        )
        tooltip = (
            getattr(window, "tooltip", None) or f"Rolling score over {window.hours}h"
        )
        fmt = getattr(window, "format", None) or "decimal-2"
        columns.append(
            {
                "id": col_id,
                "type": "VALUE",
                "property": window_name,
                "format": fmt,
                "displayName": display,
                "tooltip": tooltip,
                "nativeConfiguration": None,
                "order": (i + 1) * 10,
            }
        )
        col_id += 1

    # Add columns for active metrics
    for j, metric_name in enumerate(contract.metrics):
        display = _METRIC_DISPLAY_NAMES.get(
            metric_name, metric_name.replace("_", " ").title()
        )
        tooltip = _METRIC_TOOLTIPS.get(metric_name)
        fmt = _METRIC_FORMATS.get(metric_name, "decimal-4")
        columns.append(
            {
                "id": col_id,
                "type": "VALUE",
                "property": metric_name,
                "format": fmt,
                "displayName": display,
                "tooltip": tooltip,
                "nativeConfiguration": None,
                "order": 100 + j * 10,
            }
        )
        col_id += 1

    # Add columns for custom score_type fields (e.g. net_pnl, drawdown_pct)
    # Skip fields already covered by windows, metrics, or internal fields.
    _skip_fields = {"value", "success", "failed_reason"}
    existing_properties = {c["property"] for c in columns}
    existing_properties.update(_skip_fields)
    existing_properties.update(set(contract.metrics))

    for k, field_name in enumerate(contract.score_type.model_fields):
        if field_name in existing_properties:
            continue
        field_info = contract.score_type.model_fields[field_name]
        # Only add numeric fields
        origin = field_info.annotation
        if origin not in (int, float):
            continue
        display = field_name.replace("_", " ").title()
        columns.append(
            {
                "id": col_id,
                "type": "VALUE",
                "property": field_name,
                "format": "decimal-4",
                "displayName": display,
                "tooltip": None,
                "nativeConfiguration": None,
                "order": 200 + k * 10,
            }
        )
        col_id += 1

    # Chart series from aggregation windows
    series = [
        {"name": name, "label": name.replace("_", " ").title()}
        for name in aggregation.windows
    ]

    # Metric series for the metrics chart
    metric_series = [
        {"name": m, "label": _METRIC_DISPLAY_NAMES.get(m, m.replace("_", " ").title())}
        for m in contract.metrics
    ]

    if contract.build_trading_widgets is not None:
        widgets = contract.build_trading_widgets()
    else:
        widgets = _build_standard_widgets(series, metric_series, contract)

    schema = {
        "schema_version": "1",
        "leaderboard_columns": columns,
        "metrics_widgets": widgets,
    }

    # Validate against typed contracts
    validated = ReportSchemaEnvelope.model_validate(schema)
    return validated.model_dump()


def _strip_tz(dt: datetime | None) -> datetime | None:
    """Remove timezone info from a datetime so JSON serialises without trailing Z.

    The @crunchdao/chart lineChart appends ``"Z"`` to every x-axis value
    before passing it to ``new Date()``.  If the backend already emits an
    ISO string that ends with ``Z`` (timezone-aware UTC), the chart
    produces ``"…ZZ"`` → ``Invalid Date`` → ``NaN`` → no points render.

    Stripping tzinfo here is a **workaround** — the proper fix is to stop
    appending ``"Z"`` in ``lineChart.tsx`` (see Fix 1 in the PR).
    """
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _flatten_metrics(metrics: dict[str, Any]) -> dict[str, float | None]:
    """Flatten metrics dict to top-level keys for the leaderboard response.

    Keys are kept as-is (e.g. 'ic', 'score_recent') so they match the
    schema column `property` values directly.
    """
    flattened: dict[str, float | None] = {}
    for key, value in metrics.items():
        try:
            flattened[key] = float(value) if value is not None else None
        except Exception:
            flattened[key] = None
    return flattened


def _compute_window_metrics(
    scores: list[tuple[datetime, float]], contract: CrunchConfig
) -> dict[str, float]:
    """Compute windowed metrics from timestamped scores using contract aggregation."""
    now = datetime.now(UTC)
    metrics: dict[str, float] = {}
    for window_name, window in contract.aggregation.windows.items():
        cutoff = now - timedelta(hours=window.hours)
        window_values = [
            v
            for ts, v in scores
            if (ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts) >= cutoff
        ]
        metrics[window_name] = (
            sum(window_values) / len(window_values) if window_values else 0.0
        )
    return metrics


def _normalize_project_ids(raw_ids: list[str]) -> list[str]:
    normalized: list[str] = []
    for item in raw_ids:
        for part in item.split(","):
            stripped = part.strip()
            if stripped:
                normalized.append(stripped)
    return normalized


REPORT_SCHEMA = auto_report_schema(CONTRACT)

# Auto-discover and mount custom API routers from node/api/ directory
from crunch_node.api_discovery import mount_api_routers

mount_api_routers(app)


def get_db_session() -> Generator[Session, Any, None]:
    with create_session() as session:
        yield session


def get_model_repository(
    session_db: Annotated[Session, Depends(get_db_session)],
) -> DBModelRepository:
    return DBModelRepository(session_db)


def get_leaderboard_repository(
    session_db: Annotated[Session, Depends(get_db_session)],
) -> DBLeaderboardRepository:
    return DBLeaderboardRepository(session_db)


def get_prediction_repository(
    session_db: Annotated[Session, Depends(get_db_session)],
) -> DBPredictionRepository:
    return DBPredictionRepository(session_db)


def get_feed_record_repository(
    session_db: Annotated[Session, Depends(get_db_session)],
) -> DBFeedRecordRepository:
    return DBFeedRecordRepository(session_db)


def get_snapshot_repository(
    session_db: Annotated[Session, Depends(get_db_session)],
) -> DBSnapshotRepository:
    return DBSnapshotRepository(session_db)


def get_checkpoint_repository(
    session_db: Annotated[Session, Depends(get_db_session)],
) -> DBCheckpointRepository:
    return DBCheckpointRepository(session_db)


def get_merkle_service(
    session_db: Annotated[Session, Depends(get_db_session)],
) -> MerkleService:
    return MerkleService(
        merkle_cycle_repository=DBMerkleCycleRepository(session_db),
        merkle_node_repository=DBMerkleNodeRepository(session_db),
    )


def get_merkle_cycle_repository(
    session_db: Annotated[Session, Depends(get_db_session)],
) -> DBMerkleCycleRepository:
    return DBMerkleCycleRepository(session_db)


@app.get("/healthz")
def healthcheck() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/reports/schema")
def get_report_schema() -> dict[str, Any]:
    return REPORT_SCHEMA


@app.get("/reports/schema/leaderboard-columns")
def get_report_schema_leaderboard_columns() -> list[dict[str, Any]]:
    return list(REPORT_SCHEMA.get("leaderboard_columns", []))


@app.get("/reports/schema/metrics-widgets")
def get_report_schema_metrics_widgets() -> list[dict[str, Any]]:
    return list(REPORT_SCHEMA.get("metrics_widgets", []))


@app.get("/reports/models")
def get_models(
    model_repo: Annotated[DBModelRepository, Depends(get_model_repository)],
) -> list[dict]:
    models = model_repo.fetch_all()

    return [
        {
            "model_id": model.id,
            "model_name": model.name,
            "cruncher_name": model.player_name,
            "cruncher_id": model.player_id,
            "deployment_id": model.deployment_identifier,
        }
        for model in models.values()
    ]


@app.get("/reports/models/{model_id}/diversity")
def get_model_diversity(
    model_id: str,
    snapshot_repo: Annotated[DBSnapshotRepository, Depends(get_snapshot_repository)],
    leaderboard_repo: Annotated[
        DBLeaderboardRepository, Depends(get_leaderboard_repository)
    ],
) -> dict[str, Any]:
    """Diversity and contribution feedback for a specific model.

    Returns metrics that tell a competitor how their model relates to the
    collective: correlation to other models, correlation to ensemble,
    contribution to ensemble, and actionable guidance.
    """
    # Get latest snapshot for this model
    snapshots = snapshot_repo.find(model_id=model_id, limit=1)
    if not snapshots:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No snapshots found for model '{model_id}'",
        )

    latest = snapshots[0]
    summary = latest.result_summary

    # Extract diversity-related metrics
    model_correlation = summary.get("model_correlation")
    ensemble_correlation = summary.get("ensemble_correlation")
    contribution = summary.get("contribution")
    fnc = summary.get("fnc")
    ic = summary.get("ic")

    # Compute diversity score (0 = redundant clone, 1 = fully unique)
    diversity_score = None
    if model_correlation is not None:
        diversity_score = round(max(0.0, 1.0 - abs(model_correlation)), 4)

    # Generate actionable guidance
    guidance = []
    if model_correlation is not None and model_correlation > 0.7:
        guidance.append(
            f"High correlation ({model_correlation:.2f}) with other models. "
            "Your signal overlaps significantly with existing models. "
            "Consider a different approach or features to increase uniqueness."
        )
    if ensemble_correlation is not None and ensemble_correlation > 0.9:
        guidance.append(
            f"Very high ensemble correlation ({ensemble_correlation:.2f}). "
            "Your model closely tracks the ensemble — it adds little new information."
        )
    if contribution is not None and contribution < 0:
        guidance.append(
            f"Negative contribution ({contribution:.4f}). "
            "The ensemble performs better without your model. "
            "This may reduce rewards in contribution-weighted competitions."
        )
    if contribution is not None and contribution > 0.01:
        guidance.append(
            f"Positive contribution ({contribution:.4f}). "
            "Your model improves the ensemble — this is valuable."
        )
    if (
        model_correlation is not None
        and model_correlation < 0.3
        and ic is not None
        and ic > 0
    ):
        guidance.append(
            "Low correlation + positive IC — your model provides unique alpha. "
            "This is the ideal profile for ensemble contribution."
        )

    if not guidance:
        guidance.append(
            "Not enough data yet for diversity guidance. Keep submitting predictions."
        )

    # Get rank from leaderboard
    rank = None
    leaderboard = leaderboard_repo.get_latest()
    if leaderboard:
        for entry in leaderboard.get("entries", []):
            if entry.get("model_id") == model_id:
                rank = entry.get("rank")
                break

    return {
        "model_id": model_id,
        "rank": rank,
        "diversity_score": diversity_score,
        "metrics": {
            "ic": ic,
            "model_correlation": model_correlation,
            "ensemble_correlation": ensemble_correlation,
            "contribution": contribution,
            "fnc": fnc,
        },
        "guidance": guidance,
        "snapshot_period_end": latest.period_end,
    }


def _is_ensemble_model(model_id: str | None) -> bool:
    """Check if a model ID belongs to an ensemble virtual model."""
    return bool(model_id and model_id.startswith("__ensemble_"))


@app.get("/reports/leaderboard")
def get_leaderboard(
    leaderboard_repo: Annotated[
        DBLeaderboardRepository, Depends(get_leaderboard_repository)
    ],
    include_ensembles: Annotated[bool, Query()] = False,
) -> list[dict]:
    leaderboard = leaderboard_repo.get_latest()
    if leaderboard is None:
        return []

    created_at = leaderboard.get("created_at")
    entries = leaderboard.get("entries", [])

    normalized_entries = []
    for entry in entries:
        model_id = entry.get("model_id")

        # Filter out ensemble models unless explicitly requested
        if not include_ensembles and _is_ensemble_model(model_id):
            continue

        score = entry.get("score", {})
        metrics = score.get("metrics", {})

        normalized_entries.append(
            {
                "created_at": created_at,
                "model_id": model_id,
                "score_metrics": metrics,
                "score_ranking": score.get("ranking", {}),
                **_flatten_metrics(metrics),
                "rank": entry.get("rank", 999999),
                "model_name": entry.get("model_name"),
                "cruncher_name": entry.get("cruncher_name"),
            }
        )

    return sorted(normalized_entries, key=lambda item: item.get("rank", 999999))


@app.get("/reports/models/global")
def get_models_global(
    prediction_repo: Annotated[
        DBPredictionRepository, Depends(get_prediction_repository)
    ],
    snapshot_repo: Annotated[DBSnapshotRepository, Depends(get_snapshot_repository)],
    model_repo: Annotated[DBModelRepository, Depends(get_model_repository)],
    model_ids: Annotated[list[str] | None, Query(alias="projectIds")] = None,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
    include_ensembles: Annotated[bool, Query()] = False,
) -> list[dict]:
    if not model_ids:
        model_ids = list(model_repo.fetch_all().keys())
    else:
        model_ids = _normalize_project_ids(model_ids)
    if not include_ensembles:
        model_ids = [m for m in model_ids if not _is_ensemble_model(m)]
    if not model_ids:
        return []
    if end is None:
        end = datetime.now(UTC)
    if start is None:
        start = end - timedelta(days=7)
    predictions_by_model = prediction_repo.query_scores(
        model_ids=model_ids, _from=start, to=end
    )

    # Fetch latest snapshot per model for multi-metric enrichment
    latest_snapshots: dict[str, dict[str, Any]] = {}
    all_snaps = snapshot_repo.find(since=start, until=end)
    for snap in all_snaps:
        # Keep the most recent snapshot per model (list is ASC ordered)
        latest_snapshots[snap.model_id] = snap.result_summary or {}

    rows: list[dict] = []
    for model_id, predictions in predictions_by_model.items():
        timed_scores = [
            (p.performed_at, float(p.score.value))
            for p in predictions
            if p.score and p.score.success and p.score.value is not None
        ]
        if not timed_scores:
            continue

        metrics = _compute_window_metrics(timed_scores, CONTRACT)

        # Merge snapshot metrics (ic, hit_rate, etc.) into the response
        snap_metrics = latest_snapshots.get(model_id, {})
        for key, val in snap_metrics.items():
            if key not in metrics and isinstance(val, (int, float)):
                metrics[key] = val

        performed_at = max((p.performed_at for p in predictions), default=end)

        rows.append(
            {
                "model_id": model_id,
                "score_metrics": metrics,
                "score_ranking": {
                    "key": CONTRACT.aggregation.ranking_key,
                    "value": metrics.get(CONTRACT.aggregation.ranking_key),
                    "direction": CONTRACT.aggregation.ranking_direction,
                },
                **_flatten_metrics(metrics),
                "performed_at": _strip_tz(performed_at),
            }
        )

    return rows


@app.get("/reports/models/params")
def get_models_params(
    prediction_repo: Annotated[
        DBPredictionRepository, Depends(get_prediction_repository)
    ],
    model_repo: Annotated[DBModelRepository, Depends(get_model_repository)],
    model_ids: Annotated[list[str] | None, Query(alias="projectIds")] = None,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
    include_ensembles: Annotated[bool, Query()] = False,
) -> list[dict]:
    if not model_ids:
        model_ids = list(model_repo.fetch_all().keys())
    else:
        model_ids = _normalize_project_ids(model_ids)
    if not include_ensembles:
        model_ids = [m for m in model_ids if not _is_ensemble_model(m)]
    if not model_ids:
        return []
    if end is None:
        end = datetime.now(UTC)
    if start is None:
        start = end - timedelta(days=7)
    predictions_by_model = prediction_repo.query_scores(
        model_ids=model_ids, _from=start, to=end
    )

    grouped: dict[tuple[str, str], list] = {}
    for model_id, predictions in predictions_by_model.items():
        for prediction in predictions:
            key = (model_id, prediction.scope_key)
            grouped.setdefault(key, []).append(prediction)

    rows: list[dict] = []
    for (model_id, scope_key), predictions in grouped.items():
        timed_scores = [
            (p.performed_at, float(p.score.value))
            for p in predictions
            if p.score and p.score.success and p.score.value is not None
        ]
        if not timed_scores:
            continue

        metrics = _compute_window_metrics(timed_scores, CONTRACT)
        performed_at = max((p.performed_at for p in predictions), default=end)
        scope = predictions[-1].scope if predictions else {}

        rows.append(
            {
                "model_id": model_id,
                "scope_key": scope_key,
                "scope": scope,
                "score_metrics": metrics,
                "score_ranking": {
                    "key": CONTRACT.aggregation.ranking_key,
                    "value": metrics.get(CONTRACT.aggregation.ranking_key),
                    "direction": CONTRACT.aggregation.ranking_direction,
                },
                **_flatten_metrics(metrics),
                "performed_at": _strip_tz(performed_at),
            }
        )

    return rows


@app.get("/reports/predictions")
def get_predictions(
    prediction_repo: Annotated[
        DBPredictionRepository, Depends(get_prediction_repository)
    ],
    model_repo: Annotated[DBModelRepository, Depends(get_model_repository)],
    model_ids: Annotated[list[str] | None, Query(alias="projectIds")] = None,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
) -> list[dict]:
    if not model_ids:
        model_ids = list(model_repo.fetch_all().keys())
    else:
        model_ids = _normalize_project_ids(model_ids)
    if not model_ids:
        return []
    if end is None:
        end = datetime.now(UTC)
    if start is None:
        start = end - timedelta(days=7)
    predictions_by_model = prediction_repo.query_scores(
        model_ids=model_ids, _from=start, to=end
    )

    rows: list[dict] = []
    for _, predictions in predictions_by_model.items():
        for prediction in predictions:
            score = prediction.score
            rows.append(
                {
                    "model_id": prediction.model_id,
                    "prediction_config_id": prediction.prediction_config_id,
                    "scope_key": prediction.scope_key,
                    "scope": prediction.scope,
                    "score_value": score.value if score else None,
                    "score_failed": (not score.success) if score else True,
                    "score_failed_reason": score.failed_reason
                    if score
                    else "Prediction not scored",
                    "scored_at": _strip_tz(score.scored_at) if score else None,
                    "performed_at": _strip_tz(prediction.performed_at),
                }
            )

    return sorted(rows, key=lambda row: row["performed_at"])


@app.get("/reports/feeds")
def get_feeds(
    feed_repo: Annotated[DBFeedRecordRepository, Depends(get_feed_record_repository)],
) -> list[dict[str, Any]]:
    return feed_repo.list_indexed_feeds()


@app.get("/reports/feeds/tail")
def get_feeds_tail(
    feed_repo: Annotated[DBFeedRecordRepository, Depends(get_feed_record_repository)],
    source: Annotated[str | None, Query()] = None,
    subject: Annotated[str | None, Query()] = None,
    kind: Annotated[str | None, Query()] = None,
    granularity: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> list[dict[str, Any]]:
    records = feed_repo.tail_records(
        source=source,
        subject=subject,
        kind=kind,
        granularity=granularity,
        limit=limit,
    )

    rows: list[dict[str, Any]] = []
    for record in records:
        rows.append(
            {
                "source": record.source,
                "subject": record.subject,
                "kind": record.kind,
                "granularity": record.granularity,
                "ts_event": record.ts_event,
                "ts_ingested": record.ts_ingested,
                "values": record.values,
                "meta": record.meta,
            }
        )

    return rows


# ── Snapshots ──


@app.get("/reports/snapshots")
def get_snapshots(
    snapshot_repo: Annotated[DBSnapshotRepository, Depends(get_snapshot_repository)],
    model_id: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[dict[str, Any]]:
    snapshots = snapshot_repo.find(
        model_id=model_id, since=since, until=until, limit=limit
    )
    return [
        {
            "id": s.id,
            "model_id": s.model_id,
            "period_start": _strip_tz(s.period_start),
            "period_end": _strip_tz(s.period_end),
            "prediction_count": s.prediction_count,
            "result_summary": s.result_summary,
            "created_at": _strip_tz(s.created_at),
        }
        for s in snapshots
    ]


@app.get("/reports/models/metrics")
def get_models_metrics_timeseries(
    snapshot_repo: Annotated[DBSnapshotRepository, Depends(get_snapshot_repository)],
    model_repo: Annotated[DBModelRepository, Depends(get_model_repository)],
    model_ids: Annotated[list[str] | None, Query(alias="projectIds")] = None,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
    include_ensembles: Annotated[bool, Query()] = False,
) -> list[dict]:
    """Time-series of snapshot metrics per model — powers metric charts.

    Returns one row per snapshot with all result_summary fields flattened
    to the top level alongside model_id and performed_at.
    """
    if not model_ids:
        model_ids = list(model_repo.fetch_all().keys())
    else:
        model_ids = _normalize_project_ids(model_ids)
    if not include_ensembles:
        model_ids = [m for m in model_ids if not _is_ensemble_model(m)]
    if not model_ids:
        return []
    if end is None:
        end = datetime.now(UTC)
    if start is None:
        start = end - timedelta(days=7)

    rows: list[dict] = []
    for mid in model_ids:
        snapshots = snapshot_repo.find(model_id=mid, since=start, until=end)
        for snap in snapshots:
            summary = snap.result_summary or {}
            row: dict[str, Any] = {
                "model_id": snap.model_id,
                "performed_at": _strip_tz(snap.created_at),
                "prediction_count": snap.prediction_count,
            }
            for key, val in summary.items():
                if isinstance(val, (int, float)):
                    row[key] = val
            rows.append(row)

    return sorted(rows, key=lambda r: r["performed_at"])


@app.get("/reports/models/summary")
def get_models_summary(
    snapshot_repo: Annotated[DBSnapshotRepository, Depends(get_snapshot_repository)],
    model_repo: Annotated[DBModelRepository, Depends(get_model_repository)],
    model_ids: Annotated[list[str] | None, Query(alias="projectIds")] = None,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
    include_ensembles: Annotated[bool, Query()] = False,
) -> list[dict]:
    """One row per model with latest snapshot metrics — powers bar charts."""
    if not model_ids:
        model_ids = list(model_repo.fetch_all().keys())
    else:
        model_ids = _normalize_project_ids(model_ids)
    if not include_ensembles:
        model_ids = [m for m in model_ids if not _is_ensemble_model(m)]
    if not model_ids:
        return []
    if end is None:
        end = datetime.now(UTC)
    if start is None:
        start = end - timedelta(days=7)

    # Get latest snapshot per model
    all_snaps = snapshot_repo.find(since=start, until=end)
    latest: dict[str, Any] = {}
    for snap in all_snaps:
        latest[snap.model_id] = snap  # ASC order, last wins

    rows: list[dict] = []
    for mid in model_ids:
        snap = latest.get(mid)
        if not snap:
            continue
        summary = snap.result_summary or {}
        row: dict[str, Any] = {"model_id": mid}
        for key, val in summary.items():
            if isinstance(val, (int, float)):
                row[key] = val
        rows.append(row)

    return rows


# ── Diversity overview ──


@app.get("/reports/diversity")
def get_diversity_overview(
    snapshot_repo: Annotated[DBSnapshotRepository, Depends(get_snapshot_repository)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    """Diversity overview for all models — powers the Model Diversity chart.

    Returns one row per model with their latest diversity-related metrics.
    """
    snapshots = snapshot_repo.find(limit=500)

    # Latest snapshot per non-ensemble model
    latest: dict[str, Any] = {}
    for snap in snapshots:
        if _is_ensemble_model(snap.model_id):
            continue
        if (
            snap.model_id not in latest
            or snap.period_end > latest[snap.model_id]["period_end"]
        ):
            summary = snap.result_summary
            corr = summary.get("model_correlation")
            latest[snap.model_id] = {
                "model_id": snap.model_id,
                "period_end": snap.period_end,
                "model_correlation": corr,
                "ensemble_correlation": summary.get("ensemble_correlation"),
                "contribution": summary.get("contribution"),
                "fnc": summary.get("fnc"),
                "diversity_score": round(max(0.0, 1.0 - abs(corr)), 4)
                if corr is not None
                else None,
                "ic": summary.get("ic"),
            }

    rows = sorted(
        latest.values(), key=lambda r: r.get("diversity_score") or 0, reverse=True
    )
    return rows[:limit]


# ── Ensemble history ──


@app.get("/reports/ensemble/history")
def get_ensemble_history(
    snapshot_repo: Annotated[DBSnapshotRepository, Depends(get_snapshot_repository)],
    ensemble_name: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    """Ensemble performance over time — powers the Ensemble Performance chart.

    Returns snapshots for ensemble virtual models, with metrics flattened.
    Shows whether the collective is getting smarter over time.
    """
    snapshots = snapshot_repo.find(since=since, until=until, limit=500)

    rows = []
    for snap in snapshots:
        if not _is_ensemble_model(snap.model_id):
            continue
        name = snap.model_id.lstrip("_").replace("ensemble_", "", 1).rstrip("_")
        if ensemble_name and name != ensemble_name:
            continue
        row = {
            "ensemble_name": name,
            "model_id": snap.model_id,
            "period_start": _strip_tz(snap.period_start),
            "period_end": _strip_tz(snap.period_end),
            "prediction_count": snap.prediction_count,
            **{
                k: v
                for k, v in snap.result_summary.items()
                if isinstance(v, (int, float))
            },
        }
        rows.append(row)

    rows.sort(key=lambda r: r["period_end"])
    return rows[:limit]


# ── Checkpoint reward history ──


@app.get("/reports/checkpoints/rewards")
def get_checkpoint_rewards(
    checkpoint_repo: Annotated[
        DBCheckpointRepository, Depends(get_checkpoint_repository)
    ],
    model_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[dict[str, Any]]:
    """Reward distribution history across checkpoints — powers the Reward History chart.

    Returns one row per model per checkpoint with their rank and reward percentage.
    """
    checkpoints = checkpoint_repo.find(limit=limit)

    rows = []
    for cp in checkpoints:
        ranking = cp.meta.get("ranking", []) if cp.meta else []
        for entry in ranking:
            mid = entry.get("model_id", "")
            if model_id and mid != model_id:
                continue
            if _is_ensemble_model(mid):
                continue

            # Compute reward percentage from emission
            reward_pct = None
            if cp.entries:
                emission = cp.entries[0]
                cruncher_rewards = emission.get("cruncher_rewards", [])
                idx = entry.get("rank", 0) - 1
                if 0 <= idx < len(cruncher_rewards):
                    from crunch_node.crunch_config import FRAC_64_MULTIPLIER

                    raw = cruncher_rewards[idx].get("reward_pct", 0)
                    reward_pct = round(raw / FRAC_64_MULTIPLIER * 100, 4)

            rows.append(
                {
                    "checkpoint_id": cp.id,
                    "period_start": cp.period_start,
                    "period_end": cp.period_end,
                    "model_id": mid,
                    "model_name": entry.get("model_name"),
                    "rank": entry.get("rank"),
                    "reward_pct": reward_pct,
                    "prediction_count": entry.get("prediction_count"),
                    **{
                        k: v
                        for k, v in entry.get("result_summary", {}).items()
                        if isinstance(v, (int, float))
                    },
                }
            )

    rows.sort(key=lambda r: (r["period_end"], r["rank"] or 999))
    return rows


# ── Checkpoints ──


@app.get("/reports/checkpoints")
def get_checkpoints(
    checkpoint_repo: Annotated[
        DBCheckpointRepository, Depends(get_checkpoint_repository)
    ],
    checkpoint_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[dict[str, Any]]:
    checkpoints = checkpoint_repo.find(status=checkpoint_status, limit=limit)
    return [_checkpoint_to_dict(c) for c in checkpoints]


@app.get("/reports/checkpoints/latest")
def get_latest_checkpoint(
    checkpoint_repo: Annotated[
        DBCheckpointRepository, Depends(get_checkpoint_repository)
    ],
) -> dict[str, Any]:
    checkpoint = checkpoint_repo.get_latest()
    if checkpoint is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No checkpoints found"
        )
    return _checkpoint_to_dict(checkpoint)


@app.get("/reports/checkpoints/{checkpoint_id}/payload")
def get_checkpoint_payload(
    checkpoint_id: str,
    checkpoint_repo: Annotated[
        DBCheckpointRepository, Depends(get_checkpoint_repository)
    ],
) -> dict[str, Any]:
    checkpoints = checkpoint_repo.find()
    checkpoint = next((c for c in checkpoints if c.id == checkpoint_id), None)
    if checkpoint is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Checkpoint not found"
        )
    return {
        "checkpoint_id": checkpoint.id,
        "period_start": checkpoint.period_start.isoformat(),
        "period_end": checkpoint.period_end.isoformat(),
        "entries": checkpoint.entries,
    }


@app.post("/reports/checkpoints/{checkpoint_id}/confirm")
def confirm_checkpoint(
    checkpoint_id: str,
    body: dict[str, Any],
    checkpoint_repo: Annotated[
        DBCheckpointRepository, Depends(get_checkpoint_repository)
    ],
) -> dict[str, Any]:
    checkpoints = checkpoint_repo.find()
    checkpoint = next((c for c in checkpoints if c.id == checkpoint_id), None)
    if checkpoint is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Checkpoint not found"
        )
    if checkpoint.status != CheckpointStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Checkpoint is {checkpoint.status}, expected PENDING",
        )

    tx_hash = body.get("tx_hash")
    if not tx_hash:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="tx_hash required"
        )

    checkpoint.status = CheckpointStatus.SUBMITTED
    checkpoint.tx_hash = tx_hash
    checkpoint.submitted_at = datetime.now(UTC)
    checkpoint_repo.save(checkpoint)

    return _checkpoint_to_dict(checkpoint)


@app.patch("/reports/checkpoints/{checkpoint_id}/status")
def update_checkpoint_status(
    checkpoint_id: str,
    body: dict[str, Any],
    checkpoint_repo: Annotated[
        DBCheckpointRepository, Depends(get_checkpoint_repository)
    ],
) -> dict[str, Any]:
    checkpoints = checkpoint_repo.find()
    checkpoint = next((c for c in checkpoints if c.id == checkpoint_id), None)
    if checkpoint is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Checkpoint not found"
        )

    new_status = body.get("status")
    valid_transitions: dict[CheckpointStatus, list[CheckpointStatus]] = {
        CheckpointStatus.PENDING: [CheckpointStatus.SUBMITTED],
        CheckpointStatus.SUBMITTED: [CheckpointStatus.CLAIMABLE],
        CheckpointStatus.CLAIMABLE: [CheckpointStatus.PAID],
    }
    allowed = valid_transitions.get(CheckpointStatus(checkpoint.status), [])
    try:
        new_status = CheckpointStatus(new_status)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid status: {new_status}. Valid: {[s.value for s in CheckpointStatus]}",
        )
    if new_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot transition from {checkpoint.status} to {new_status}. Allowed: {allowed}",
        )

    checkpoint.status = new_status
    checkpoint_repo.save(checkpoint)

    return _checkpoint_to_dict(checkpoint)


# ── Emissions ──


@app.get("/reports/checkpoints/{checkpoint_id}/emission")
def get_checkpoint_emission(
    checkpoint_id: str,
    checkpoint_repo: Annotated[
        DBCheckpointRepository, Depends(get_checkpoint_repository)
    ],
) -> dict[str, Any]:
    """Return the EmissionCheckpoint in protocol format for on-chain submission."""
    checkpoints = checkpoint_repo.find()
    checkpoint = next((c for c in checkpoints if c.id == checkpoint_id), None)
    if checkpoint is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Checkpoint not found"
        )
    if not checkpoint.entries:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No emission data in checkpoint",
        )
    return checkpoint.entries[0]


@app.get("/reports/checkpoints/{checkpoint_id}/emission/cli-format")
def get_checkpoint_emission_cli_format(
    checkpoint_id: str,
    checkpoint_repo: Annotated[
        DBCheckpointRepository, Depends(get_checkpoint_repository)
    ],
) -> dict[str, Any]:
    """Return emission in coordinator-cli JSON file format.

    Format: {crunch, crunchEmission: {wallet: pct}, computeProvider: {addr: pct}, dataProvider: {addr: pct}}
    where pct values are percentages (0-100).
    """
    checkpoints = checkpoint_repo.find()
    checkpoint = next((c for c in checkpoints if c.id == checkpoint_id), None)
    if checkpoint is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Checkpoint not found"
        )
    if not checkpoint.entries:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No emission data in checkpoint",
        )

    emission = checkpoint.entries[0]
    frac64_multiplier = 1_000_000_000

    # Build crunchEmission: map cruncher_index → percentage
    # Note: in production, cruncher_index maps to wallet addresses via the on-chain AddressIndexMap.
    # The ranking in checkpoint.meta provides model_id for each index.
    ranking = checkpoint.meta.get("ranking", [])
    crunch_emission: dict[str, float] = {}
    for reward in emission.get("cruncher_rewards", []):
        idx = reward["cruncher_index"]
        pct = reward["reward_pct"] / frac64_multiplier * 100.0
        # Use model_id from ranking as key (operator maps to wallet externally)
        model_id = ranking[idx]["model_id"] if idx < len(ranking) else str(idx)
        crunch_emission[model_id] = round(pct, 6)

    compute_provider: dict[str, float] = {}
    for reward in emission.get("compute_provider_rewards", []):
        pct = reward["reward_pct"] / frac64_multiplier * 100.0
        compute_provider[reward["provider"]] = round(pct, 6)

    data_provider: dict[str, float] = {}
    for reward in emission.get("data_provider_rewards", []):
        pct = reward["reward_pct"] / frac64_multiplier * 100.0
        data_provider[reward["provider"]] = round(pct, 6)

    return {
        "crunch": emission.get("crunch", ""),
        "crunchEmission": crunch_emission,
        "computeProvider": compute_provider,
        "dataProvider": data_provider,
    }


@app.get("/reports/checkpoints/{checkpoint_id}/prizes")
def get_checkpoint_prizes(
    checkpoint_id: str,
    checkpoint_repo: Annotated[
        DBCheckpointRepository, Depends(get_checkpoint_repository)
    ],
    total_prize: Annotated[
        int,
        Query(
            description="Total prize pool to distribute (in token lowest denomination)"
        ),
    ] = 0,
) -> list[dict[str, Any]]:
    """Return checkpoint emission as Prize[] JSON for the coordinator webapp.

    The webapp's CreateCheckpoint UI expects:
      [{prizeId, timestamp, model, prize}]
    where `model` is a model ID and `prize` is an absolute token amount.

    This endpoint converts the node's frac64 percentage-based emission into
    the webapp format by distributing `total_prize` proportionally.
    """
    checkpoints = checkpoint_repo.find()
    checkpoint = next((c for c in checkpoints if c.id == checkpoint_id), None)
    if checkpoint is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Checkpoint not found"
        )
    if not checkpoint.entries:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No emission data in checkpoint",
        )

    emission = checkpoint.entries[0]
    ranking = checkpoint.meta.get("ranking", [])
    frac64_multiplier = 1_000_000_000
    timestamp = int(checkpoint.period_end.timestamp())

    prizes: list[dict[str, Any]] = []
    for reward in emission.get("cruncher_rewards", []):
        idx = reward["cruncher_index"]
        pct = reward["reward_pct"] / frac64_multiplier
        model_id = ranking[idx]["model_id"] if idx < len(ranking) else str(idx)

        prize_amount = int(round(total_prize * pct))
        prizes.append(
            {
                "prizeId": f"{checkpoint_id}-{model_id}",
                "timestamp": timestamp,
                "model": model_id,
                "prize": prize_amount,
            }
        )

    return prizes


@app.get("/reports/checkpoints/latest/prizes")
def get_latest_checkpoint_prizes(
    checkpoint_repo: Annotated[
        DBCheckpointRepository, Depends(get_checkpoint_repository)
    ],
    total_prize: Annotated[
        int,
        Query(
            description="Total prize pool to distribute (in token lowest denomination)"
        ),
    ] = 0,
) -> dict[str, Any]:
    """Return the latest checkpoint's prizes in webapp format.

    Convenience wrapper that finds the latest checkpoint and returns its prizes.
    """
    checkpoint = checkpoint_repo.get_latest()
    if checkpoint is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No checkpoints found"
        )
    if not checkpoint.entries:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No emission data in checkpoint",
        )

    emission = checkpoint.entries[0]
    ranking = checkpoint.meta.get("ranking", [])
    frac64_multiplier = 1_000_000_000
    timestamp = int(checkpoint.period_end.timestamp())

    prizes: list[dict[str, Any]] = []
    for reward in emission.get("cruncher_rewards", []):
        idx = reward["cruncher_index"]
        pct = reward["reward_pct"] / frac64_multiplier
        model_id = ranking[idx]["model_id"] if idx < len(ranking) else str(idx)

        prize_amount = int(round(total_prize * pct))
        prizes.append(
            {
                "prizeId": f"{checkpoint.id}-{model_id}",
                "timestamp": timestamp,
                "model": model_id,
                "prize": prize_amount,
            }
        )

    return {
        "checkpoint_id": checkpoint.id,
        "status": checkpoint.status,
        "period_start": checkpoint.period_start.isoformat(),
        "period_end": checkpoint.period_end.isoformat(),
        "total_prize": total_prize,
        "prizes": prizes,
    }


@app.get("/reports/emissions/latest")
def get_latest_emission(
    checkpoint_repo: Annotated[
        DBCheckpointRepository, Depends(get_checkpoint_repository)
    ],
) -> dict[str, Any]:
    """Return the emission from the most recent checkpoint."""
    checkpoint = checkpoint_repo.get_latest()
    if checkpoint is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No checkpoints found"
        )
    if not checkpoint.entries:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No emission data in checkpoint",
        )
    return {
        "checkpoint_id": checkpoint.id,
        "status": checkpoint.status,
        "period_start": checkpoint.period_start,
        "period_end": checkpoint.period_end,
        "emission": checkpoint.entries[0],
    }


def _checkpoint_to_dict(c) -> dict[str, Any]:
    return {
        "id": c.id,
        "period_start": c.period_start,
        "period_end": c.period_end,
        "status": c.status,
        "entries": c.entries,
        "meta": c.meta,
        "created_at": c.created_at,
        "tx_hash": c.tx_hash,
        "submitted_at": c.submitted_at,
    }


# ── Merkle tamper evidence ──


@app.get("/reports/merkle/cycles")
def get_merkle_cycles(
    cycle_repo: Annotated[
        DBMerkleCycleRepository, Depends(get_merkle_cycle_repository)
    ],
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[dict[str, Any]]:
    """List recent Merkle cycles. Public endpoint for external verification."""
    cycles = cycle_repo.find(since=since, until=until, limit=limit)
    return [
        {
            "id": c.id,
            "previous_cycle_id": c.previous_cycle_id,
            "previous_cycle_root": c.previous_cycle_root,
            "snapshots_root": c.snapshots_root,
            "chained_root": c.chained_root,
            "snapshot_count": c.snapshot_count,
            "created_at": c.created_at,
        }
        for c in cycles
    ]


@app.get("/reports/merkle/cycles/{cycle_id}")
def get_merkle_cycle(
    cycle_id: str,
    cycle_repo: Annotated[
        DBMerkleCycleRepository, Depends(get_merkle_cycle_repository)
    ],
) -> dict[str, Any]:
    """Get a single Merkle cycle with its chained root."""
    cycle = cycle_repo.get(cycle_id)
    if cycle is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Merkle cycle not found"
        )
    return {
        "id": cycle.id,
        "previous_cycle_id": cycle.previous_cycle_id,
        "previous_cycle_root": cycle.previous_cycle_root,
        "snapshots_root": cycle.snapshots_root,
        "chained_root": cycle.chained_root,
        "snapshot_count": cycle.snapshot_count,
        "created_at": cycle.created_at,
    }


@app.get("/reports/merkle/proof")
def get_merkle_proof(
    snapshot_id: Annotated[str, Query()],
    merkle_svc: Annotated[MerkleService, Depends(get_merkle_service)],
) -> dict[str, Any]:
    """Generate an inclusion proof for a snapshot. Public endpoint."""
    proof = merkle_svc.get_proof(snapshot_id)
    if proof is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No Merkle proof found for snapshot '{snapshot_id}'",
        )
    return {
        "snapshot_id": proof.snapshot_id,
        "snapshot_content_hash": proof.snapshot_content_hash,
        "cycle_id": proof.cycle_id,
        "cycle_root": proof.cycle_root,
        "checkpoint_id": proof.checkpoint_id,
        "merkle_root": proof.merkle_root,
        "path": [{"hash": s.hash, "position": s.position} for s in proof.path],
    }


# ── Backfill ──

import os

from fastapi import BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel

BACKFILL_DATA_DIR = os.getenv("BACKFILL_DATA_DIR", "data/backfill")
_parquet_sink = None


def _get_parquet_sink() -> ParquetBackfillSink:
    global _parquet_sink
    if _parquet_sink is None:
        from crunch_node.services.parquet_sink import ParquetBackfillSink

        _parquet_sink = ParquetBackfillSink(base_dir=BACKFILL_DATA_DIR)
    return _parquet_sink


class BackfillRequestBody(BaseModel):
    source: str
    subject: str
    kind: str
    granularity: str
    start: datetime
    end: datetime

    def model_post_init(self, __context: Any) -> None:
        if self.start.tzinfo is None:
            self.start = self.start.replace(tzinfo=UTC)
        if self.end.tzinfo is None:
            self.end = self.end.replace(tzinfo=UTC)


def get_backfill_job_repository(
    session_db: Annotated[Session, Depends(get_db_session)],
) -> DBBackfillJobRepository:
    return DBBackfillJobRepository(session_db)


@app.get("/reports/backfill/feeds")
def get_backfill_feeds(
    feed_repo: Annotated[DBFeedRecordRepository, Depends(get_feed_record_repository)],
) -> list[dict[str, Any]]:
    """Return configured feeds eligible for backfill."""
    return feed_repo.list_indexed_feeds()


@app.post("/reports/backfill", status_code=201)
def start_backfill(
    body: BackfillRequestBody,
    background_tasks: BackgroundTasks,
    backfill_repo: Annotated[
        DBBackfillJobRepository, Depends(get_backfill_job_repository)
    ],
    feed_repo: Annotated[DBFeedRecordRepository, Depends(get_feed_record_repository)],
) -> dict[str, Any]:
    """Start a backfill job. Returns 409 if one is already running."""
    # Check no running job
    running = backfill_repo.get_running()
    if running is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Backfill job {running.id} is already {running.status}",
        )

    # Create job
    job = backfill_repo.create(
        source=body.source,
        subject=body.subject,
        kind=body.kind,
        granularity=body.granularity,
        start_ts=body.start,
        end_ts=body.end,
    )

    # Start async backfill
    background_tasks.add_task(_run_backfill_async, job.id, body)

    return _backfill_job_to_dict(job)


@app.get("/reports/backfill/jobs")
def list_backfill_jobs(
    backfill_repo: Annotated[
        DBBackfillJobRepository, Depends(get_backfill_job_repository)
    ],
    job_status: Annotated[str | None, Query(alias="status")] = None,
) -> list[dict[str, Any]]:
    """List all backfill jobs."""
    jobs = backfill_repo.find(status=job_status)
    return [_backfill_job_to_dict(j) for j in jobs]


@app.get("/reports/backfill/jobs/{job_id}")
def get_backfill_job(
    job_id: str,
    backfill_repo: Annotated[
        DBBackfillJobRepository, Depends(get_backfill_job_repository)
    ],
) -> dict[str, Any]:
    """Get a single backfill job with progress."""
    job = backfill_repo.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Backfill job not found"
        )

    result = _backfill_job_to_dict(job)

    # Add progress percentage estimate
    if job.start_ts and job.end_ts and job.cursor_ts:
        total = (job.end_ts - job.start_ts).total_seconds()
        elapsed = (job.cursor_ts - job.start_ts).total_seconds()
        result["progress_pct"] = min(
            100.0, max(0.0, (elapsed / total * 100.0) if total > 0 else 0.0)
        )
    else:
        result["progress_pct"] = 0.0

    return result


# ── Data Serving ──


@app.get("/data/backfill/index")
def get_backfill_index() -> list[dict[str, object]]:
    """Return manifest of available parquet files."""
    return _get_parquet_sink().list_files()


@app.get("/data/backfill/{source}/{subject}/{kind}/{granularity}/{filename}")
def get_backfill_file(
    source: str,
    subject: str,
    kind: str,
    granularity: str,
    filename: str,
) -> FileResponse:
    """Serve a parquet file for download."""
    rel_path = f"{source}/{subject}/{kind}/{granularity}/{filename}"
    file_path = _get_parquet_sink().read_file(rel_path)
    if file_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="File not found"
        )
    return FileResponse(
        path=str(file_path),
        media_type="application/octet-stream",
        filename=filename,
    )


def _backfill_job_to_dict(job) -> dict[str, Any]:
    return {
        "id": job.id,
        "source": job.source,
        "subject": job.subject,
        "kind": job.kind,
        "granularity": job.granularity,
        "start_ts": job.start_ts,
        "end_ts": job.end_ts,
        "cursor_ts": job.cursor_ts,
        "records_written": job.records_written,
        "pages_fetched": job.pages_fetched,
        "status": job.status,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


async def _run_backfill_async(job_id: str, body: BackfillRequestBody) -> None:
    """Run backfill in background. Uses its own DB session."""
    from crunch_node.feeds import create_default_registry
    from crunch_node.services.backfill import BackfillRequest, BackfillService

    logger = logging.getLogger("backfill_worker")

    try:
        with create_session() as session:
            job_repo = DBBackfillJobRepository(session)

            # Check if we should resume
            job = job_repo.get(job_id)
            cursor_ts = None
            _cursor = (
                job.cursor_ts.replace(tzinfo=UTC)
                if job and job.cursor_ts and job.cursor_ts.tzinfo is None
                else (job.cursor_ts if job else None)
            )
            _start = (
                body.start.replace(tzinfo=UTC)
                if body.start.tzinfo is None
                else body.start
            )
            if _cursor and _cursor > _start:
                cursor_ts = job.cursor_ts

            registry = create_default_registry()
            feed = registry.create_from_env(default_provider=body.source)

            request = BackfillRequest(
                source=body.source,
                subjects=(body.subject,),
                kind=body.kind,
                granularity=body.granularity,
                start=body.start,
                end=body.end,
                cursor_ts=cursor_ts,
                job_id=job_id,
            )

            from crunch_node.services.parquet_sink import ParquetBackfillSink

            sink = ParquetBackfillSink(base_dir=BACKFILL_DATA_DIR)
            service = BackfillService(
                feed=feed, repository=sink, job_repository=job_repo
            )
            result = await service.run(request)

            logger.info(
                "backfill job=%s completed records=%d pages=%d",
                job_id,
                result.records_written,
                result.pages_fetched,
            )
    except Exception as exc:
        logger.exception("backfill job=%s failed: %s", job_id, exc)


@app.get("/timing-metrics")
async def get_timing_metrics(limit: int = Query(default=1000, le=10000)):
    """Get pipeline timing metrics from recent predictions."""
    if not CONTRACT.performance.timing_endpoint_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Timing metrics endpoint is disabled",
        )

    from crunch_node.metrics.timing import aggregate_timing_from_predictions

    with create_session() as session:
        repo = DBPredictionRepository(session)
        predictions = repo.fetch_recent_with_timing(limit=limit)

    return aggregate_timing_from_predictions(predictions)


if __name__ == "__main__":
    logging.getLogger(__name__).info("coordinator report worker bootstrap")

    uvicorn.run(app, host="0.0.0.0", port=8000)

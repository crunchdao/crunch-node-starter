from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ScheduleEnvelope(BaseModel):
    """Canonical scheduling envelope stored in `scheduled_prediction_configs.schedule_jsonb`.

    Note: extra="forbid" so that typos like ``every_seconds`` are caught
    immediately instead of being silently swallowed with defaults.
    """

    prediction_interval_seconds: int = Field(
        default=60,
        ge=1,
        description=(
            "How often the coordinator calls models to produce predictions (seconds). "
            "This is the scheduling interval — NOT the step_seconds passed to "
            "model.predict(). Example: 15 means 'call models every 15 seconds'."
        ),
    )
    resolve_horizon_seconds: int = Field(
        default=0,
        ge=0,
        description=(
            "Seconds after a prediction is made before ground truth is resolved. "
            "Must be > 0 for scoring to work (feed data needs time to accumulate). "
            "Also passed to models as resolve_horizon_seconds. "
            "Example: 60 means 'resolve ground truth 60 seconds after prediction'."
        ),
    )

    model_config = ConfigDict(extra="forbid")


class ScheduledPredictionConfigEnvelope(BaseModel):
    """Canonical envelope for active scheduled prediction configs."""

    id: str | None = None
    scope_key: str = Field(min_length=1)
    scope_template: dict[str, Any] = Field(default_factory=dict)
    schedule: ScheduleEnvelope = Field(default_factory=ScheduleEnvelope)
    active: bool = True
    order: int = 0
    meta: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Report schema contracts — must match coordinator-webapp FE types
# ---------------------------------------------------------------------------


class ReportLeaderboardColumn(BaseModel):
    """Matches FE LeaderboardColumn type in @coordinator/leaderboard."""

    id: int
    type: Literal["MODEL", "VALUE", "USERNAME", "CHART"]
    property: str
    format: str | None = None
    displayName: str
    tooltip: str | None = None
    nativeConfiguration: dict[str, Any] | None = None
    order: int = 0

    model_config = ConfigDict(extra="allow")


class ReportMetricWidget(BaseModel):
    """Matches FE Widget type in @coordinator/metrics."""

    id: int
    type: Literal["CHART", "IFRAME"]
    displayName: str
    tooltip: str | None = None
    order: int = 0
    endpointUrl: str
    nativeConfiguration: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")


class ReportSchemaEnvelope(BaseModel):
    """Validated report schema returned by REPORT_SCHEMA_PROVIDER callables.

    Ensures every leaderboard column and metric widget has all required
    fields so the coordinator-webapp FE can render without errors.
    """

    schema_version: str = "1"
    leaderboard_columns: list[ReportLeaderboardColumn]
    metrics_widgets: list[ReportMetricWidget]

    model_config = ConfigDict(extra="allow")

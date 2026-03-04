from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class Model:
    id: str
    name: str
    player_id: str
    player_name: str
    deployment_identifier: str
    overall_score: dict[str, Any] | None = None  # built by aggregation from contract
    scores_by_scope: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)  # contract.meta_type (Meta)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

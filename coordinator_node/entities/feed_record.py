from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class FeedRecord:
    source: str
    subject: str
    kind: str
    granularity: str
    ts_event: datetime
    values: dict[str, Any]  # contract.raw_input_type
    meta: dict[str, Any] = field(default_factory=dict)  # contract.meta_type (Meta)
    ts_ingested: datetime = field(default_factory=_utc_now)
    _timing: dict[str, Any] = field(default_factory=dict)  # Performance timing data


@dataclass
class FeedIngestionState:
    source: str
    subject: str
    kind: str
    granularity: str
    last_event_ts: datetime | None
    meta: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=_utc_now)

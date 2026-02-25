from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# Built-in kinds for market data feeds. Custom providers may use any string
# (e.g. "event", "aggregate") — the type is kept as str throughout the pipeline.
FeedDataKind = str


@dataclass(frozen=True)
class SubjectDescriptor:
    """Provider-native subject descriptor with per-subject capabilities."""

    symbol: str
    display_name: str | None
    kinds: tuple[FeedDataKind, ...]
    granularities: tuple[str, ...]
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FeedSubscription:
    """Push/listen mode subscription request."""

    subjects: tuple[str, ...]
    kind: FeedDataKind
    granularity: str
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeedFetchRequest:
    """Pull/fetch mode request used for backfill and truth windows."""

    subjects: tuple[str, ...]
    kind: FeedDataKind
    granularity: str
    start_ts: int | None = None
    end_ts: int | None = None
    limit: int | None = None
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeedDataRecord:
    """Canonical feed record shape normalized by feed adapters."""

    source: str
    subject: str
    kind: FeedDataKind
    granularity: str
    ts_event: int
    values: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

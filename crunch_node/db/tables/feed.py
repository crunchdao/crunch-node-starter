"""Feed data ingestion tables."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

TZDateTime = DateTime(timezone=True)


def utc_now() -> datetime:
    return datetime.now(UTC)


class FeedRecordRow(SQLModel, table=True):
    __tablename__ = "feed_records"

    id: str = Field(primary_key=True)

    source: str = Field(index=True)
    subject: str = Field(index=True)
    kind: str = Field(index=True)
    granularity: str = Field(index=True)

    ts_event: datetime = Field(index=True, sa_type=TZDateTime)
    ts_ingested: datetime = Field(
        default_factory=utc_now, index=True, sa_type=TZDateTime
    )

    values_jsonb: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB),
    )
    meta_jsonb: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB),
    )

    __table_args__ = (
        Index(
            "uq_feed_records_event",
            "source",
            "subject",
            "kind",
            "granularity",
            "ts_event",
            unique=True,
        ),
    )


class FeedIngestionStateRow(SQLModel, table=True):
    __tablename__ = "feed_ingestion_state"

    id: str = Field(primary_key=True)

    source: str = Field(index=True)
    subject: str = Field(index=True)
    kind: str = Field(index=True)
    granularity: str = Field(index=True)

    last_event_ts: datetime | None = Field(default=None, index=True, sa_type=TZDateTime)
    updated_at: datetime = Field(
        default_factory=utc_now, index=True, sa_type=TZDateTime
    )

    meta_jsonb: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB),
    )

    __table_args__ = (
        Index(
            "uq_feed_ingestion_scope",
            "source",
            "subject",
            "kind",
            "granularity",
            unique=True,
        ),
    )

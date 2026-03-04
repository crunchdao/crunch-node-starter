"""Backfill job tracking table."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class BackfillJobRow(SQLModel, table=True):
    __tablename__ = "backfill_jobs"

    id: str = Field(primary_key=True)

    source: str = Field(index=True)
    subject: str = Field(index=True)
    kind: str = Field(index=True)
    granularity: str = Field(index=True)

    start_ts: datetime
    end_ts: datetime
    cursor_ts: datetime | None = Field(default=None)

    records_written: int = Field(default=0)
    pages_fetched: int = Field(default=0)

    status: str = Field(default="pending", index=True)
    error: str | None = Field(default=None)

    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)

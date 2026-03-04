"""Repository for backfill job persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlmodel import Session, select

from crunch_node.db.tables.backfill import BackfillJobRow


class BackfillJobStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DBBackfillJobRepository:
    def __init__(self, session: Session):
        self._session = session

    def create(
        self,
        *,
        source: str,
        subject: str,
        kind: str,
        granularity: str,
        start_ts: datetime,
        end_ts: datetime,
    ) -> BackfillJobRow:
        row = BackfillJobRow(
            id=str(uuid.uuid4()),
            source=source,
            subject=subject,
            kind=kind,
            granularity=granularity,
            start_ts=_ensure_utc(start_ts),
            end_ts=_ensure_utc(end_ts),
            cursor_ts=_ensure_utc(start_ts),
            records_written=0,
            pages_fetched=0,
            status=BackfillJobStatus.PENDING,
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def get(self, job_id: str) -> BackfillJobRow | None:
        return self._session.get(BackfillJobRow, job_id)

    def find(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[BackfillJobRow]:
        stmt = select(BackfillJobRow).order_by(BackfillJobRow.created_at.desc())
        if status is not None:
            stmt = stmt.where(BackfillJobRow.status == status)
        stmt = stmt.limit(limit)
        return list(self._session.exec(stmt).all())

    def get_running(self) -> BackfillJobRow | None:
        stmt = (
            select(BackfillJobRow)
            .where(
                BackfillJobRow.status.in_(
                    [BackfillJobStatus.PENDING, BackfillJobStatus.RUNNING]
                )
            )
            .limit(1)
        )
        return self._session.exec(stmt).first()

    def update_progress(
        self,
        job_id: str,
        *,
        cursor_ts: datetime,
        records_written: int,
        pages_fetched: int,
    ) -> None:
        row = self._session.get(BackfillJobRow, job_id)
        if row is None:
            return
        row.cursor_ts = _ensure_utc(cursor_ts)
        row.records_written = records_written
        row.pages_fetched = pages_fetched
        row.updated_at = datetime.now(UTC)
        self._session.commit()

    def set_status(
        self,
        job_id: str,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        row = self._session.get(BackfillJobRow, job_id)
        if row is None:
            return
        row.status = status
        row.error = error
        row.updated_at = datetime.now(UTC)
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

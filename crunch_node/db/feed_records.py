from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, delete, select

from crunch_node.db.tables import FeedIngestionStateRow, FeedRecordRow
from crunch_node.entities.feed_record import FeedIngestionState, FeedRecord


class DBFeedRecordRepository:
    def __init__(self, session: Session):
        self._session = session

    def rollback(self) -> None:
        self._session.rollback()

    def append_records(
        self, records: Iterable[FeedRecord], *, record_persist_timing: bool = False
    ) -> int:
        rows_to_update = []
        count = 0
        for record in records:
            row = self._domain_to_row(record)
            existing = self._session.get(FeedRecordRow, row.id)

            if existing is None:
                self._session.add(row)
                if record_persist_timing:
                    rows_to_update.append(row)
            else:
                existing.values_jsonb = row.values_jsonb
                existing.meta_jsonb = row.meta_jsonb
                existing.ts_ingested = row.ts_ingested
                if record_persist_timing:
                    rows_to_update.append(existing)

            count += 1

        self._session.commit()

        if record_persist_timing and rows_to_update:
            feed_persisted_us = time.perf_counter_ns() // 1000
            for row in rows_to_update:
                meta = dict(row.meta_jsonb or {})
                meta.setdefault("timing", {})["feed_persisted_us"] = feed_persisted_us
                row.meta_jsonb = meta
                flag_modified(row, "meta_jsonb")
            self._session.commit()

        return count

    def fetch_records(
        self,
        *,
        source: str,
        subject: str,
        kind: str,
        granularity: str,
        start_ts: datetime | None = None,
        end_ts: datetime | None = None,
        limit: int | None = None,
    ) -> list[FeedRecord]:
        stmt = (
            select(FeedRecordRow)
            .where(FeedRecordRow.source == source)
            .where(FeedRecordRow.subject == subject)
            .where(FeedRecordRow.kind == kind)
            .where(FeedRecordRow.granularity == granularity)
            .order_by(FeedRecordRow.ts_event.asc())
        )

        if start_ts is not None:
            stmt = stmt.where(FeedRecordRow.ts_event >= start_ts)
        if end_ts is not None:
            stmt = stmt.where(FeedRecordRow.ts_event <= end_ts)
        if limit is not None:
            stmt = stmt.limit(max(0, int(limit)))

        rows = self._session.exec(stmt).all()
        return [self._row_to_domain(row) for row in rows]

    def prune_before(self, cutoff_ts: datetime) -> int:
        rows = self._session.exec(
            select(FeedRecordRow.id).where(FeedRecordRow.ts_event < cutoff_ts)
        ).all()
        deleted = len(rows)

        if deleted:
            self._session.exec(
                delete(FeedRecordRow).where(FeedRecordRow.ts_event < cutoff_ts)
            )
            self._session.commit()

        return deleted

    def fetch_latest_record(
        self,
        *,
        source: str,
        subject: str,
        kind: str,
        granularity: str,
        at_or_before: datetime | None = None,
    ) -> FeedRecord | None:
        stmt = (
            select(FeedRecordRow)
            .where(FeedRecordRow.source == source)
            .where(FeedRecordRow.subject == subject)
            .where(FeedRecordRow.kind == kind)
            .where(FeedRecordRow.granularity == granularity)
            .order_by(FeedRecordRow.ts_event.desc())
            .limit(1)
        )

        if at_or_before is not None:
            stmt = stmt.where(FeedRecordRow.ts_event <= at_or_before)

        row = self._session.exec(stmt).first()
        return self._row_to_domain(row) if row is not None else None

    def list_indexed_feeds(self) -> list[dict[str, object]]:
        grouped_rows = self._session.exec(
            select(
                FeedRecordRow.source,
                FeedRecordRow.subject,
                FeedRecordRow.kind,
                FeedRecordRow.granularity,
                func.count(FeedRecordRow.id),
                func.min(FeedRecordRow.ts_event),
                func.max(FeedRecordRow.ts_event),
            )
            .group_by(
                FeedRecordRow.source,
                FeedRecordRow.subject,
                FeedRecordRow.kind,
                FeedRecordRow.granularity,
            )
            .order_by(
                FeedRecordRow.source.asc(),
                FeedRecordRow.subject.asc(),
                FeedRecordRow.kind.asc(),
                FeedRecordRow.granularity.asc(),
            )
        ).all()

        watermarks = {
            (row.source, row.subject, row.kind, row.granularity): row
            for row in self._session.exec(select(FeedIngestionStateRow)).all()
        }

        summaries: list[dict[str, object]] = []
        for (
            source,
            subject,
            kind,
            granularity,
            count,
            oldest_ts,
            newest_ts,
        ) in grouped_rows:
            key = (source, subject, kind, granularity)
            state = watermarks.get(key)
            summaries.append(
                {
                    "source": source,
                    "subject": subject,
                    "kind": kind,
                    "granularity": granularity,
                    "record_count": int(count or 0),
                    "oldest_ts": _ensure_utc(oldest_ts).isoformat()
                    if oldest_ts is not None
                    else None,
                    "newest_ts": _ensure_utc(newest_ts).isoformat()
                    if newest_ts is not None
                    else None,
                    "watermark_ts": (
                        _ensure_utc(state.last_event_ts).isoformat()
                        if state is not None and state.last_event_ts is not None
                        else None
                    ),
                    "watermark_updated_at": (
                        _ensure_utc(state.updated_at).isoformat()
                        if state is not None and state.updated_at is not None
                        else None
                    ),
                }
            )

        return summaries

    def tail_records(
        self,
        *,
        source: str | None = None,
        subject: str | None = None,
        kind: str | None = None,
        granularity: str | None = None,
        limit: int = 20,
    ) -> list[FeedRecord]:
        stmt = select(FeedRecordRow).order_by(FeedRecordRow.ts_event.desc())

        if source:
            stmt = stmt.where(FeedRecordRow.source == source)
        if subject:
            stmt = stmt.where(FeedRecordRow.subject == subject)
        if kind:
            stmt = stmt.where(FeedRecordRow.kind == kind)
        if granularity:
            stmt = stmt.where(FeedRecordRow.granularity == granularity)

        stmt = stmt.limit(max(1, int(limit)))

        rows = self._session.exec(stmt).all()
        return [self._row_to_domain(row) for row in rows]

    def get_watermark(
        self,
        *,
        source: str,
        subject: str,
        kind: str,
        granularity: str,
    ) -> FeedIngestionState | None:
        row = self._session.get(
            FeedIngestionStateRow, _watermark_id(source, subject, kind, granularity)
        )
        if row is None:
            return None
        return self._watermark_row_to_domain(row)

    def set_watermark(self, state: FeedIngestionState) -> None:
        row = self._watermark_domain_to_row(state)
        existing = self._session.get(FeedIngestionStateRow, row.id)

        if existing is None:
            self._session.add(row)
        else:
            existing.last_event_ts = row.last_event_ts
            existing.meta_jsonb = row.meta_jsonb
            existing.updated_at = row.updated_at

        self._session.commit()

    @staticmethod
    def _domain_to_row(record: FeedRecord) -> FeedRecordRow:
        normalized_ts_event = _ensure_utc(record.ts_event)
        normalized_ts_ingested = _ensure_utc(record.ts_ingested)

        return FeedRecordRow(
            id=_record_id(
                record.source,
                record.subject,
                record.kind,
                record.granularity,
                normalized_ts_event,
            ),
            source=record.source,
            subject=record.subject,
            kind=record.kind,
            granularity=record.granularity,
            ts_event=normalized_ts_event,
            ts_ingested=normalized_ts_ingested,
            values_jsonb=dict(record.values),
            meta_jsonb=dict(record.meta),
        )

    @staticmethod
    def _row_to_domain(row: FeedRecordRow) -> FeedRecord:
        return FeedRecord(
            source=row.source,
            subject=row.subject,
            kind=row.kind,
            granularity=row.granularity,
            ts_event=_ensure_utc(row.ts_event),
            ts_ingested=_ensure_utc(row.ts_ingested),
            values=dict(row.values_jsonb or {}),
            meta=dict(row.meta_jsonb or {}),
        )

    @staticmethod
    def _watermark_domain_to_row(state: FeedIngestionState) -> FeedIngestionStateRow:
        return FeedIngestionStateRow(
            id=_watermark_id(
                state.source, state.subject, state.kind, state.granularity
            ),
            source=state.source,
            subject=state.subject,
            kind=state.kind,
            granularity=state.granularity,
            last_event_ts=_ensure_utc(state.last_event_ts)
            if state.last_event_ts is not None
            else None,
            updated_at=_ensure_utc(state.updated_at),
            meta_jsonb=dict(state.meta),
        )

    @staticmethod
    def _watermark_row_to_domain(row: FeedIngestionStateRow) -> FeedIngestionState:
        return FeedIngestionState(
            source=row.source,
            subject=row.subject,
            kind=row.kind,
            granularity=row.granularity,
            last_event_ts=_ensure_utc(row.last_event_ts)
            if row.last_event_ts is not None
            else None,
            updated_at=_ensure_utc(row.updated_at),
            meta=dict(row.meta_jsonb or {}),
        )


def _watermark_id(source: str, subject: str, kind: str, granularity: str) -> str:
    return f"{source}:{subject}:{kind}:{granularity}"


def _record_id(
    source: str, subject: str, kind: str, granularity: str, ts_event: datetime
) -> str:
    fingerprint = (
        f"{source}|{subject}|{kind}|{granularity}|{_ensure_utc(ts_event).isoformat()}"
    )
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()  # noqa: S324


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

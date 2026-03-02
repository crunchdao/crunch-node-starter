"""Hive-partitioned parquet writer for backfill data."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    pa = None  # type: ignore[assignment]
    pq = None  # type: ignore[assignment]

from coordinator_node.entities.feed_record import FeedRecord

logger = logging.getLogger(__name__)

# Standard value columns that get flattened from the values dict
STANDARD_VALUE_COLUMNS = ("open", "high", "low", "close", "volume")

_SCHEMA = None


def _get_schema():
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = pa.schema(
            [
                ("ts_event", pa.timestamp("us", tz="UTC")),
                ("source", pa.string()),
                ("subject", pa.string()),
                ("kind", pa.string()),
                ("granularity", pa.string()),
                ("open", pa.float64()),
                ("high", pa.float64()),
                ("low", pa.float64()),
                ("close", pa.float64()),
                ("volume", pa.float64()),
                ("meta", pa.string()),  # JSON string for non-standard fields
            ]
        )
    return _SCHEMA


# Public alias for external consumers (e.g. tests, backtest harness)
def get_schema():
    """Return the parquet schema, lazily initialized."""
    return _get_schema()


class ParquetBackfillSink:
    """Writes FeedRecords to Hive-partitioned daily parquet files.

    Path layout: {base_dir}/{source}/{subject}/{kind}/{granularity}/YYYY-MM-DD.parquet
    """

    def __init__(self, base_dir: str = "data/backfill") -> None:
        if pa is None:
            raise ImportError(
                "pyarrow is required for parquet backfill. "
                "Install it with: pip install coordinator-node[parquet]"
            )
        self.base_dir = Path(base_dir)

    def append_records(self, records: Iterable[FeedRecord]) -> int:
        """Write records to parquet, grouped by date. Merges with existing files."""
        # Group records by (source, subject, kind, granularity, date)
        grouped: dict[tuple[str, str, str, str, str], list[FeedRecord]] = defaultdict(
            list
        )
        count = 0
        for record in records:
            ts = _ensure_utc(record.ts_event)
            date_str = ts.strftime("%Y-%m-%d")
            key = (
                record.source,
                record.subject,
                record.kind,
                record.granularity,
                date_str,
            )
            grouped[key].append(record)
            count += 1

        for (source, subject, kind, granularity, date_str), recs in grouped.items():
            path = self._file_path(source, subject, kind, granularity, date_str)
            self._write_or_merge(path, recs)

        return count

    def set_watermark(self, state) -> None:
        """No-op — backfill jobs table tracks progress instead."""
        pass

    def list_files(self) -> list[dict[str, object]]:
        """Return manifest of all parquet files with metadata."""
        manifest: list[dict[str, object]] = []
        if not self.base_dir.exists():
            return manifest

        for parquet_path in sorted(self.base_dir.rglob("*.parquet")):
            try:
                metadata = pq.read_metadata(parquet_path)
                rel_path = parquet_path.relative_to(self.base_dir)
                # Extract date from filename (YYYY-MM-DD.parquet)
                date_str = parquet_path.stem
                manifest.append(
                    {
                        "path": str(rel_path),
                        "records": metadata.num_rows,
                        "size_bytes": parquet_path.stat().st_size,
                        "date": date_str,
                    }
                )
            except Exception as exc:
                logger.warning(
                    "Failed to read parquet metadata for %s: %s", parquet_path, exc
                )

        return manifest

    def read_file(self, rel_path: str) -> Path | None:
        """Return absolute path to a parquet file if it exists."""
        full_path = self.base_dir / rel_path
        if full_path.exists() and full_path.suffix == ".parquet":
            return full_path
        return None

    def _file_path(
        self, source: str, subject: str, kind: str, granularity: str, date_str: str
    ) -> Path:
        return (
            self.base_dir
            / source
            / subject
            / kind
            / granularity
            / f"{date_str}.parquet"
        )

    def _write_or_merge(self, path: Path, records: list[FeedRecord]) -> None:
        """Write records to parquet, merging with existing file if present."""
        new_table = _records_to_table(records)

        if path.exists():
            try:
                existing = pq.read_table(path, schema=_get_schema())
                merged = pa.concat_tables([existing, new_table])
                # Deduplicate by ts_event
                merged = _deduplicate_by_ts(merged)
            except Exception:
                logger.warning("Failed to read existing parquet %s, overwriting", path)
                merged = new_table
        else:
            merged = new_table

        # Sort by ts_event
        indices = pa.compute.sort_indices(merged, sort_keys=[("ts_event", "ascending")])
        merged = merged.take(indices)

        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(merged, path)


def _records_to_table(records: list[FeedRecord]) -> pa.Table:
    """Convert FeedRecords to a pyarrow Table with flattened value columns."""
    ts_events = []
    sources = []
    subjects = []
    kinds = []
    granularities = []
    opens = []
    highs = []
    lows = []
    closes = []
    volumes = []
    metas = []

    for record in records:
        ts = _ensure_utc(record.ts_event)
        values = record.values or {}

        ts_events.append(ts)
        sources.append(record.source)
        subjects.append(record.subject)
        kinds.append(record.kind)
        granularities.append(record.granularity)

        opens.append(_safe_float(values.get("open")))
        highs.append(_safe_float(values.get("high")))
        lows.append(_safe_float(values.get("low")))
        closes.append(_safe_float(values.get("close")))
        volumes.append(_safe_float(values.get("volume")))

        # Non-standard fields go into meta
        extra = {k: v for k, v in values.items() if k not in STANDARD_VALUE_COLUMNS}
        if record.meta:
            extra["_record_meta"] = record.meta
        metas.append(json.dumps(extra) if extra else "{}")

    return pa.table(
        {
            "ts_event": pa.array(ts_events, type=pa.timestamp("us", tz="UTC")),
            "source": pa.array(sources, type=pa.string()),
            "subject": pa.array(subjects, type=pa.string()),
            "kind": pa.array(kinds, type=pa.string()),
            "granularity": pa.array(granularities, type=pa.string()),
            "open": pa.array(opens, type=pa.float64()),
            "high": pa.array(highs, type=pa.float64()),
            "low": pa.array(lows, type=pa.float64()),
            "close": pa.array(closes, type=pa.float64()),
            "volume": pa.array(volumes, type=pa.float64()),
            "meta": pa.array(metas, type=pa.string()),
        },
        schema=_get_schema(),
    )


def _deduplicate_by_ts(table: pa.Table) -> pa.Table:
    """Keep last occurrence for each ts_event."""
    ts_col = table.column("ts_event")
    seen: dict[int, int] = {}
    for i in range(len(ts_col)):
        # Use microsecond timestamp as key
        val = ts_col[i].as_py()
        if val is not None:
            key = int(val.timestamp() * 1_000_000)
            seen[key] = i

    indices = sorted(seen.values())
    return table.take(indices)


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

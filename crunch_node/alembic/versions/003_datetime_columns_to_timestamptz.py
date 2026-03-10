"""Convert all datetime columns from TIMESTAMP to TIMESTAMPTZ.

Existing data is assumed to be UTC (all Python code uses datetime.now(UTC)).
ALTER COLUMN ... TYPE TIMESTAMPTZ AT TIME ZONE 'UTC' tells PostgreSQL to
interpret existing naive values as UTC when attaching timezone info.

Revision ID: 003
Revises: 002
Create Date: 2026-03-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column) pairs — every datetime column in the schema.
_COLUMNS = [
    # pipeline.py
    ("inputs", "received_at"),
    ("predictions", "performed_at"),
    ("predictions", "resolvable_at"),
    ("scores", "scored_at"),
    ("snapshots", "period_start"),
    ("snapshots", "period_end"),
    ("snapshots", "created_at"),
    ("checkpoints", "period_start"),
    ("checkpoints", "period_end"),
    ("checkpoints", "created_at"),
    ("checkpoints", "submitted_at"),
    # feed.py
    ("feed_records", "ts_event"),
    ("feed_records", "ts_ingested"),
    ("feed_ingestion_state", "last_event_ts"),
    ("feed_ingestion_state", "updated_at"),
    # models.py
    ("models", "created_at"),
    ("models", "updated_at"),
    ("leaderboards", "created_at"),
    # merkle.py
    ("merkle_cycles", "created_at"),
    ("merkle_nodes", "created_at"),
    # backfill.py
    ("backfill_jobs", "start_ts"),
    ("backfill_jobs", "end_ts"),
    ("backfill_jobs", "cursor_ts"),
    ("backfill_jobs", "created_at"),
    ("backfill_jobs", "updated_at"),
]


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.execute(
            f'ALTER TABLE "{table}" '
            f'ALTER COLUMN "{column}" TYPE TIMESTAMPTZ '
            f"USING \"{column}\" AT TIME ZONE 'UTC'"
        )


def downgrade() -> None:
    for table, column in _COLUMNS:
        op.execute(
            f'ALTER TABLE "{table}" '
            f'ALTER COLUMN "{column}" TYPE TIMESTAMP WITHOUT TIME ZONE'
        )

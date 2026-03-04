"""add snapshots.content_hash

Revision ID: 002
Revises: 001
Create Date: 2026-02-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("snapshots", sa.Column("content_hash", sa.String(), nullable=True))
    op.create_index("ix_snapshots_content_hash", "snapshots", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_snapshots_content_hash", table_name="snapshots")
    op.drop_column("snapshots", "content_hash")

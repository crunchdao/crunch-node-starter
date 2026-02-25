"""initial schema — all tables

Revision ID: 001
Revises:
Create Date: 2026-02-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── Models & Leaderboards ──
    op.create_table(
        "models",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("deployment_identifier", sa.String(), nullable=True),
        sa.Column("player_id", sa.String(), nullable=True),
        sa.Column("player_name", sa.String(), nullable=True),
        sa.Column("overall_score_jsonb", postgresql.JSONB(), server_default="{}"),
        sa.Column("scores_by_scope_jsonb", postgresql.JSONB(), server_default="[]"),
        sa.Column("meta_jsonb", postgresql.JSONB(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_models_player_id", "models", ["player_id"])

    op.create_table(
        "leaderboards",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("entries_jsonb", postgresql.JSONB(), server_default="[]"),
        sa.Column("meta_jsonb", postgresql.JSONB(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_leaderboards_created_at", "leaderboards", ["created_at"])

    # ── Pipeline: inputs → predictions → scores → snapshots → checkpoints ──
    op.create_table(
        "inputs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("status", sa.String(), nullable=False, server_default="RECEIVED"),
        sa.Column("raw_data_jsonb", postgresql.JSONB(), server_default="{}"),
        sa.Column("actuals_jsonb", postgresql.JSONB(), nullable=True),
        sa.Column("scope_jsonb", postgresql.JSONB(), server_default="{}"),
        sa.Column("meta_jsonb", postgresql.JSONB(), server_default="{}"),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("resolvable_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_inputs_status", "inputs", ["status"])
    op.create_index("ix_inputs_received_at", "inputs", ["received_at"])
    op.create_index("ix_inputs_resolvable_at", "inputs", ["resolvable_at"])

    op.create_table(
        "scheduled_prediction_configs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("scope_key", sa.String(), nullable=False),
        sa.Column("scope_template_jsonb", postgresql.JSONB(), server_default="{}"),
        sa.Column("schedule_jsonb", postgresql.JSONB(), server_default="{}"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("meta_jsonb", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_spc_scope_key", "scheduled_prediction_configs", ["scope_key"])
    op.create_index("ix_spc_active", "scheduled_prediction_configs", ["active"])

    op.create_table(
        "predictions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("input_id", sa.String(), sa.ForeignKey("inputs.id"), nullable=False),
        sa.Column("model_id", sa.String(), sa.ForeignKey("models.id"), nullable=False),
        sa.Column(
            "prediction_config_id",
            sa.String(),
            sa.ForeignKey("scheduled_prediction_configs.id"),
            nullable=True,
        ),
        sa.Column("scope_key", sa.String(), nullable=False),
        sa.Column("scope_jsonb", postgresql.JSONB(), server_default="{}"),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("exec_time_ms", sa.Float(), nullable=False),
        sa.Column("inference_output_jsonb", postgresql.JSONB(), server_default="{}"),
        sa.Column("meta_jsonb", postgresql.JSONB(), server_default="{}"),
        sa.Column("performed_at", sa.DateTime(), nullable=False),
        sa.Column("resolvable_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_predictions_input_id", "predictions", ["input_id"])
    op.create_index("ix_predictions_model_id", "predictions", ["model_id"])
    op.create_index("ix_predictions_config_id", "predictions", ["prediction_config_id"])
    op.create_index("ix_predictions_scope_key", "predictions", ["scope_key"])
    op.create_index("ix_predictions_status", "predictions", ["status"])
    op.create_index("ix_predictions_performed_at", "predictions", ["performed_at"])
    op.create_index("ix_predictions_resolvable_at", "predictions", ["resolvable_at"])
    op.create_index("idx_predictions_lookup", "predictions", ["model_id", "scope_key"])

    op.create_table(
        "scores",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "prediction_id",
            sa.String(),
            sa.ForeignKey("predictions.id"),
            nullable=False,
        ),
        sa.Column("result_jsonb", postgresql.JSONB(), server_default="{}"),
        sa.Column("success", sa.Boolean(), nullable=True),
        sa.Column("failed_reason", sa.String(), nullable=True),
        sa.Column("scored_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_scores_prediction_id", "scores", ["prediction_id"])
    op.create_index("ix_scores_scored_at", "scores", ["scored_at"])

    op.create_table(
        "snapshots",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("model_id", sa.String(), sa.ForeignKey("models.id"), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("prediction_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_summary_jsonb", postgresql.JSONB(), server_default="{}"),
        sa.Column("meta_jsonb", postgresql.JSONB(), server_default="{}"),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_snapshots_model_id", "snapshots", ["model_id"])
    op.create_index("ix_snapshots_period_start", "snapshots", ["period_start"])
    op.create_index("ix_snapshots_period_end", "snapshots", ["period_end"])
    op.create_index("ix_snapshots_content_hash", "snapshots", ["content_hash"])
    op.create_index("ix_snapshots_created_at", "snapshots", ["created_at"])

    op.create_table(
        "checkpoints",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
        sa.Column("entries_jsonb", postgresql.JSONB(), server_default="[]"),
        sa.Column("meta_jsonb", postgresql.JSONB(), server_default="{}"),
        sa.Column("merkle_root", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("tx_hash", sa.String(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_checkpoints_period_start", "checkpoints", ["period_start"])
    op.create_index("ix_checkpoints_period_end", "checkpoints", ["period_end"])
    op.create_index("ix_checkpoints_status", "checkpoints", ["status"])
    op.create_index("ix_checkpoints_created_at", "checkpoints", ["created_at"])

    # ── Feed records ──
    op.create_table(
        "feed_records",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("granularity", sa.String(), nullable=False),
        sa.Column("ts_event", sa.DateTime(), nullable=False),
        sa.Column("ts_ingested", sa.DateTime(), nullable=False),
        sa.Column("values_jsonb", postgresql.JSONB(), server_default="{}"),
        sa.Column("meta_jsonb", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_feed_records_source", "feed_records", ["source"])
    op.create_index("ix_feed_records_subject", "feed_records", ["subject"])
    op.create_index("ix_feed_records_kind", "feed_records", ["kind"])
    op.create_index("ix_feed_records_granularity", "feed_records", ["granularity"])
    op.create_index("ix_feed_records_ts_event", "feed_records", ["ts_event"])
    op.create_index(
        "idx_feed_records_lookup",
        "feed_records",
        ["source", "subject", "kind", "granularity", "ts_event"],
    )

    op.create_table(
        "feed_ingestion_state",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("granularity", sa.String(), nullable=False),
        sa.Column("last_ts", sa.DateTime(), nullable=True),
        sa.Column("meta_jsonb", postgresql.JSONB(), server_default="{}"),
    )

    # ── Backfill jobs ──
    op.create_table(
        "backfill_jobs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("granularity", sa.String(), nullable=False),
        sa.Column("start_ts", sa.DateTime(), nullable=False),
        sa.Column("end_ts", sa.DateTime(), nullable=False),
        sa.Column("cursor_ts", sa.DateTime(), nullable=True),
        sa.Column("records_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pages_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_backfill_jobs_status", "backfill_jobs", ["status"])

    # ── Merkle tamper evidence ──
    op.create_table(
        "merkle_cycles",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("previous_cycle_id", sa.String(), nullable=True),
        sa.Column("previous_cycle_root", sa.String(), nullable=True),
        sa.Column("snapshots_root", sa.String(), nullable=False),
        sa.Column("chained_root", sa.String(), nullable=False),
        sa.Column("snapshot_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_merkle_cycles_previous_cycle_id", "merkle_cycles", ["previous_cycle_id"]
    )
    op.create_index("ix_merkle_cycles_chained_root", "merkle_cycles", ["chained_root"])
    op.create_index("ix_merkle_cycles_created_at", "merkle_cycles", ["created_at"])

    op.create_table(
        "merkle_nodes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "checkpoint_id", sa.String(), sa.ForeignKey("checkpoints.id"), nullable=True
        ),
        sa.Column(
            "cycle_id", sa.String(), sa.ForeignKey("merkle_cycles.id"), nullable=True
        ),
        sa.Column("level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hash", sa.String(), nullable=False),
        sa.Column("left_child_id", sa.String(), nullable=True),
        sa.Column("right_child_id", sa.String(), nullable=True),
        sa.Column(
            "snapshot_id", sa.String(), sa.ForeignKey("snapshots.id"), nullable=True
        ),
        sa.Column("snapshot_content_hash", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_merkle_nodes_checkpoint_id", "merkle_nodes", ["checkpoint_id"])
    op.create_index("ix_merkle_nodes_cycle_id", "merkle_nodes", ["cycle_id"])
    op.create_index("ix_merkle_nodes_snapshot_id", "merkle_nodes", ["snapshot_id"])


def downgrade() -> None:
    op.drop_table("merkle_nodes")
    op.drop_table("merkle_cycles")
    op.drop_table("backfill_jobs")
    op.drop_table("feed_ingestion_state")
    op.drop_table("feed_records")
    op.drop_table("checkpoints")
    op.drop_table("snapshots")
    op.drop_table("scores")
    op.drop_table("predictions")
    op.drop_table("scheduled_prediction_configs")
    op.drop_table("inputs")
    op.drop_table("leaderboards")
    op.drop_table("models")

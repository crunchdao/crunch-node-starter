"""Model, scoring, and leaderboard tables."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

TZDateTime = DateTime(timezone=True)


def utc_now() -> datetime:
    return datetime.now(UTC)


class ModelRow(SQLModel, table=True):
    __tablename__ = "models"

    id: str = Field(primary_key=True)
    name: str
    deployment_identifier: str
    player_id: str = Field(index=True)
    player_name: str

    overall_score_jsonb: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB),
    )
    scores_by_scope_jsonb: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB),
    )
    meta_jsonb: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB),
    )

    created_at: datetime = Field(
        default_factory=utc_now, index=True, sa_type=TZDateTime
    )
    updated_at: datetime = Field(
        default_factory=utc_now, index=True, sa_type=TZDateTime
    )


class LeaderboardRow(SQLModel, table=True):
    __tablename__ = "leaderboards"

    id: str = Field(primary_key=True)
    created_at: datetime = Field(
        default_factory=utc_now, index=True, sa_type=TZDateTime
    )

    entries_jsonb: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB),
    )
    meta_jsonb: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB),
    )

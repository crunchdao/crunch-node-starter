from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class TradingStateRow(SQLModel, table=True):
    __tablename__ = "trading_portfolio_state"

    model_id: str = Field(primary_key=True)

    positions_jsonb: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB),
    )
    trades_jsonb: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB),
    )
    portfolio_fees: float = Field(default=0.0)
    closed_carry: float = Field(default=0.0)

    updated_at: datetime = Field(default_factory=utc_now, index=True)

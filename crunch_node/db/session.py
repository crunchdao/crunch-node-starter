from __future__ import annotations

import os

from sqlmodel import Session, create_engine


def database_url() -> str:
    user = os.getenv("POSTGRES_USER", "starter")
    password = os.getenv("POSTGRES_PASSWORD", "starter")
    host = os.getenv("POSTGRES_HOST", "postgres")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "starter")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


engine = create_engine(
    database_url(),
    pool_pre_ping=True,
    pool_recycle=300,
)


def create_session() -> Session:
    return Session(engine)

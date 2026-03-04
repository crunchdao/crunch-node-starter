from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlmodel import SQLModel, delete

from crunch_node.db.session import create_session, engine
from crunch_node.db.tables import PredictionConfigRow
from crunch_node.schemas import ScheduledPredictionConfigEnvelope


def tables_to_reset() -> list[str]:
    return [
        "merkle_nodes",
        "merkle_cycles",
        "backfill_jobs",
        "checkpoints",
        "snapshots",
        "scores",
        "predictions",
        "inputs",
        "leaderboards",
        "feed_records",
        "feed_ingestion_state",
        "scheduled_prediction_configs",
        "models",
        "alembic_version",
    ]


def load_scheduled_prediction_configs() -> list[dict[str, Any]]:
    """Load scheduled predictions from CrunchConfig.scheduled_predictions."""
    from crunch_node.config_loader import load_config

    config = load_config()
    predictions = getattr(config, "scheduled_predictions", [])
    if not predictions:
        return []

    # Convert ScheduledPrediction models to the envelope dict format
    return [
        {
            "scope_key": sp.scope_key,
            "scope_template": sp.scope,
            "schedule": {
                "prediction_interval_seconds": sp.prediction_interval_seconds,
                "resolve_horizon_seconds": sp.resolve_horizon_seconds,
            },
            "active": sp.active,
            "order": sp.order,
            "meta": sp.meta,
        }
        for sp in predictions
    ]


def validate_scheduled_configs(configs: list[dict[str, Any]]) -> None:
    """Validate timing constraints in scheduled prediction configs.

    Catches misconfiguration at deploy time instead of silently scoring 0.
    Raises ValueError if any active config has invalid timing.
    """
    for config in configs:
        if not config.get("active", True):
            continue

        schedule = config.get("schedule") or {}
        resolve_horizon = schedule.get("resolve_horizon_seconds", 0)
        scope_key = config.get("scope_key", "<unknown>")

        if resolve_horizon < 0:
            raise ValueError(
                f"Config '{scope_key}': resolve_horizon_seconds={resolve_horizon} "
                f"must be >= 0."
            )


# ---------------------------------------------------------------------------
# Alembic migrations directory resolution
# ---------------------------------------------------------------------------


def _find_alembic_dir() -> Path | None:
    """Locate the Alembic migrations directory.

    Checks (in order):
      1. ``ALEMBIC_DIR`` env var (explicit override — scaffolds can point here
         to include their own migrations alongside the engine's)
      2. Inside the package: ``crunch_node/alembic/`` (canonical location)

    Returns ``None`` when no valid migrations directory is found.
    Callers should fall back to ``SQLModel.metadata.create_all()`` in that case.
    """

    def _is_valid(p: Path) -> bool:
        return p.is_dir() and (p / "env.py").exists() and (p / "versions").is_dir()

    # 1. Explicit env var (scaffold override)
    env_dir = os.getenv("ALEMBIC_DIR")
    if env_dir:
        p = Path(env_dir)
        if _is_valid(p):
            return p

    # 2. Inside the package (crunch_node/alembic/)
    pkg_dir = Path(__file__).resolve().parent.parent / "alembic"
    if _is_valid(pkg_dir):
        return pkg_dir

    return None


def _run_alembic_upgrade(alembic_dir: Path | None = None) -> None:
    """Run Alembic migrations programmatically.

    Sets a lock_timeout so DDL that needs AccessExclusiveLock won't hang
    indefinitely if another session holds a conflicting lock.

    Raises ``FileNotFoundError`` when no migrations directory is available.
    """
    if alembic_dir is None:
        alembic_dir = _find_alembic_dir()
    if alembic_dir is None:
        raise FileNotFoundError(
            "Alembic migrations directory not found. "
            "Set ALEMBIC_DIR or ensure the alembic/ directory is alongside the package."
        )

    from alembic import command
    from alembic.config import Config

    # Set a lock timeout so ALTER TABLE won't block forever on concurrent reads
    with engine.connect() as conn:
        conn.execute(text("SET lock_timeout = '30s'"))
        conn.commit()

    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", str(alembic_dir))
    alembic_cfg.set_main_option("sqlalchemy.url", str(engine.url))
    command.upgrade(alembic_cfg, "head")


def migrate() -> None:
    """Run Alembic migrations and upsert prediction configs.
    Safe to run on every boot — never drops data."""
    alembic_dir = _find_alembic_dir()
    if alembic_dir is not None:
        print(f"➡️  Running Alembic migrations from {alembic_dir} ...")
        try:
            _run_alembic_upgrade(alembic_dir)
        except Exception as exc:
            print(f"⚠️  Alembic migration failed ({exc}), falling back to create_all...")
            SQLModel.metadata.create_all(engine)
    else:
        print("➡️  No Alembic migrations directory found, using SQLModel create_all...")
        SQLModel.metadata.create_all(engine)

    configs = load_scheduled_prediction_configs()
    validate_scheduled_configs(configs)

    print("➡️  Upserting scheduled prediction configs...")
    with create_session() as session:
        # Drop FK temporarily so we can replace prediction configs
        session.exec(
            text(
                "ALTER TABLE predictions DROP CONSTRAINT IF EXISTS predictions_prediction_config_id_fkey"
            )
        )
        session.exec(delete(PredictionConfigRow))
        for idx, config in enumerate(configs, start=1):
            envelope = ScheduledPredictionConfigEnvelope.model_validate(config)
            session.add(
                PredictionConfigRow(
                    id=f"CFG_{idx:03d}",
                    scope_key=envelope.scope_key,
                    scope_template_jsonb=envelope.scope_template,
                    schedule_jsonb=envelope.schedule.model_dump(),
                    active=envelope.active,
                    order=envelope.order,
                    meta_jsonb=envelope.meta,
                )
            )
        session.commit()

    print("✅ Database migration complete.")


def reset_db() -> None:
    """Drop all tables and recreate from scratch. Destroys all data."""
    print("⚠️  Dropping all tables...")
    with engine.begin() as conn:
        for table in tables_to_reset():
            conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))

    migrate()
    print("✅ Database reset complete.")


def _stamp_alembic_if_needed() -> None:
    """Stamp the DB at revision 001 if tables exist but alembic_version doesn't.

    This handles databases created by the old ``create_all`` code path before
    Alembic was introduced.  Without the stamp, ``alembic upgrade head`` tries
    to re-create every table and fails.
    """
    alembic_dir = _find_alembic_dir()
    if alembic_dir is None:
        return  # No migrations available — nothing to stamp

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(engine)
    if inspector.has_table("models") and not inspector.has_table("alembic_version"):
        print("➡️  Stamping existing DB at Alembic revision 001...")
        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", str(alembic_dir))
        alembic_cfg.set_main_option("sqlalchemy.url", str(engine.url))
        command.stamp(alembic_cfg, "001")


def auto_migrate() -> None:
    """Run Alembic migrations if needed.

    Only called by the dedicated init-db container (not by workers).
    Workers depend on init-db completing before they start via
    docker-compose ``service_completed_successfully``.
    """
    try:
        from sqlalchemy import inspect as sa_inspect

        inspector = sa_inspect(engine)
        if not inspector.has_table("models"):
            migrate()
        else:
            # Tables exist — stamp at 001 if created before Alembic was added,
            # then run upgrade to apply any pending migrations (e.g. 002+).
            try:
                _stamp_alembic_if_needed()
                _run_alembic_upgrade()
            except Exception as exc:
                print(f"⚠️  auto_migrate alembic step: {exc}")
    except Exception:
        # First boot or connection issue — try migrate anyway
        try:
            migrate()
        except Exception:
            pass


if __name__ == "__main__":
    import sys

    # Ensure stdout/stderr are line-buffered even when piped (avoids TTY hangs
    # in Docker containers where there is no allocating terminal).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)

    if "--reset" in sys.argv:
        reset_db()
    else:
        migrate()

    # Explicit exit — prevents the process from hanging when run inside
    # ``docker compose run`` without a TTY.
    sys.exit(0)

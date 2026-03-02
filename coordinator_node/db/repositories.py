from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, delete, select

from coordinator_node.db.tables import (
    CheckpointRow,
    InputRow,
    LeaderboardRow,
    MerkleCycleRow,
    MerkleNodeRow,
    ModelRow,
    PredictionConfigRow,
    PredictionRow,
    ScoreRow,
    SnapshotRow,
)
from coordinator_node.entities.model import Model
from coordinator_node.entities.prediction import (
    CheckpointRecord,
    CheckpointStatus,
    InputRecord,
    PredictionRecord,
    PredictionStatus,
    ScoreRecord,
    SnapshotRecord,
)
from coordinator_node.schemas import ScheduledPredictionConfigEnvelope


class DBModelRepository:
    def __init__(self, session: Session):
        self._session = session

    def rollback(self) -> None:
        self._session.rollback()

    def fetch_all(self) -> dict[str, Model]:
        rows = self._session.exec(select(ModelRow)).all()
        return {row.id: self._row_to_domain(row) for row in rows}

    def fetch_by_ids(self, ids: list[str]) -> dict[str, Model]:
        if not ids:
            return {}
        rows = self._session.exec(select(ModelRow).where(ModelRow.id.in_(ids))).all()
        return {row.id: self._row_to_domain(row) for row in rows}

    def fetch(self, model_id: str) -> Model | None:
        row = self._session.get(ModelRow, model_id)
        return self._row_to_domain(row) if row else None

    def save(self, model: Model) -> None:
        existing = self._session.get(ModelRow, model.id)
        row = self._domain_to_row(model)

        if existing is None:
            self._session.add(row)
        else:
            existing.name = row.name
            existing.deployment_identifier = row.deployment_identifier
            existing.player_id = row.player_id
            existing.player_name = row.player_name
            existing.overall_score_jsonb = row.overall_score_jsonb
            existing.scores_by_scope_jsonb = row.scores_by_scope_jsonb
            existing.meta_jsonb = row.meta_jsonb
            existing.updated_at = datetime.now(UTC)

        self._session.commit()

    def save_all(self, models: Iterable[Model]) -> None:
        for model in models:
            self.save(model)

    @staticmethod
    def _row_to_domain(row: ModelRow) -> Model:
        return Model(
            id=row.id,
            name=row.name,
            player_id=row.player_id,
            player_name=row.player_name,
            deployment_identifier=row.deployment_identifier,
            overall_score=row.overall_score_jsonb or None,
            scores_by_scope=row.scores_by_scope_jsonb or [],
            meta=row.meta_jsonb or {},
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _domain_to_row(model: Model) -> ModelRow:
        return ModelRow(
            id=model.id,
            name=model.name,
            deployment_identifier=model.deployment_identifier,
            player_id=model.player_id,
            player_name=model.player_name,
            overall_score_jsonb=model.overall_score or {},
            scores_by_scope_jsonb=model.scores_by_scope,
            meta_jsonb=model.meta,
            created_at=model.created_at,
            updated_at=datetime.now(UTC),
        )


class DBInputRepository:
    def __init__(self, session: Session):
        self._session = session

    def get(self, input_id: str) -> InputRecord | None:
        row = self._session.get(InputRow, input_id)
        if row is None:
            return None
        return InputRecord(
            id=row.id,
            raw_data=row.raw_data_jsonb or {},
            received_at=row.received_at,
        )

    def save(self, record: InputRecord) -> None:
        row = InputRow(
            id=record.id,
            raw_data_jsonb=record.raw_data,
            received_at=record.received_at,
        )
        existing = self._session.get(InputRow, row.id)
        if existing is None:
            self._session.add(row)
        self._session.commit()

    def find(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[InputRecord]:
        stmt = select(InputRow).order_by(InputRow.received_at.asc())
        if since is not None:
            stmt = stmt.where(InputRow.received_at >= since)
        if until is not None:
            stmt = stmt.where(InputRow.received_at <= until)
        if limit is not None:
            stmt = stmt.limit(max(1, int(limit)))
        rows = self._session.exec(stmt).all()
        return [
            InputRecord(
                id=r.id,
                raw_data=r.raw_data_jsonb or {},
                received_at=r.received_at,
            )
            for r in rows
        ]


class DBPredictionRepository:
    def __init__(self, session: Session):
        self._session = session

    def rollback(self) -> None:
        self._session.rollback()

    def save(self, prediction: PredictionRecord) -> None:
        row = self._domain_to_row(prediction)
        existing = self._session.get(PredictionRow, row.id)
        if existing is None:
            self._session.add(row)
        else:
            existing.status = row.status
            existing.exec_time_ms = row.exec_time_ms
            existing.inference_output_jsonb = row.inference_output_jsonb
            existing.meta_jsonb = row.meta_jsonb
            existing.scope_key = row.scope_key
            existing.scope_jsonb = row.scope_jsonb
            existing.resolvable_at = row.resolvable_at
        self._session.commit()

    def save_all(self, predictions: Iterable[PredictionRecord]) -> None:
        for prediction in predictions:
            self.save(prediction)

    def find(
        self,
        *,
        status: str | list[str] | None = None,
        scope_key: str | None = None,
        scope_key_prefix: str | None = None,
        model_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        resolvable_before: datetime | None = None,
        limit: int | None = None,
    ) -> list[PredictionRecord]:
        stmt = select(PredictionRow)
        if status is not None:
            if isinstance(status, list):
                stmt = stmt.where(PredictionRow.status.in_(status))
            else:
                stmt = stmt.where(PredictionRow.status == status)
        if scope_key is not None:
            stmt = stmt.where(PredictionRow.scope_key == scope_key)
        if scope_key_prefix is not None:
            stmt = stmt.where(PredictionRow.scope_key.like(f"{scope_key_prefix}%"))
        if model_id is not None:
            stmt = stmt.where(PredictionRow.model_id == model_id)
        if since is not None:
            stmt = stmt.where(PredictionRow.performed_at >= since)
        if until is not None:
            stmt = stmt.where(PredictionRow.performed_at <= until)
        if resolvable_before is not None:
            stmt = stmt.where(PredictionRow.resolvable_at <= resolvable_before)
        if limit is not None:
            stmt = stmt.limit(max(1, int(limit)))
        stmt = stmt.order_by(PredictionRow.performed_at.asc())
        rows = self._session.exec(stmt).all()
        return [self._row_to_domain(row) for row in rows]

    def fetch_active_configs(self) -> list[dict]:
        rows = self._session.exec(
            select(PredictionConfigRow)
            .where(PredictionConfigRow.active.is_(True))
            .order_by(PredictionConfigRow.order.asc())
        ).all()
        configs: list[dict[str, Any]] = []
        for row in rows:
            envelope = ScheduledPredictionConfigEnvelope.model_validate(
                {
                    "id": row.id,
                    "scope_key": row.scope_key,
                    "scope_template": row.scope_template_jsonb or {},
                    "schedule": row.schedule_jsonb or {},
                    "active": row.active,
                    "order": row.order,
                    "meta": row.meta_jsonb or {},
                }
            )
            configs.append(envelope.model_dump())
        return configs

    def query_scores(
        self,
        *,
        model_ids: list[str],
        _from: datetime | None = None,
        to: datetime | None = None,
    ) -> dict[str, list[ScoredPrediction]]:
        """Return predictions with scores joined, grouped by model_id."""
        from coordinator_node.entities.prediction import ScoredPrediction

        stmt = (
            select(PredictionRow, ScoreRow)
            .outerjoin(ScoreRow, ScoreRow.prediction_id == PredictionRow.id)
            .where(PredictionRow.model_id.in_(model_ids))
        )
        if _from is not None:
            stmt = stmt.where(PredictionRow.performed_at >= _from)
        if to is not None:
            stmt = stmt.where(PredictionRow.performed_at <= to)
        stmt = stmt.order_by(PredictionRow.performed_at.asc())

        results: dict[str, list[ScoredPrediction]] = {}
        for pred_row, score_row in self._session.exec(stmt).all():
            score = None
            if score_row is not None:
                score = ScoreRecord(
                    id=score_row.id,
                    prediction_id=score_row.prediction_id,
                    result=score_row.result_jsonb or {},
                    success=score_row.success
                    if score_row.success is not None
                    else True,
                    failed_reason=score_row.failed_reason,
                    scored_at=score_row.scored_at,
                )
            sp = ScoredPrediction(
                id=pred_row.id,
                input_id=pred_row.input_id,
                model_id=pred_row.model_id,
                prediction_config_id=pred_row.prediction_config_id,
                scope_key=pred_row.scope_key,
                scope=pred_row.scope_jsonb or {},
                status=PredictionStatus(pred_row.status),
                exec_time_ms=pred_row.exec_time_ms,
                inference_output=pred_row.inference_output_jsonb or {},
                meta=pred_row.meta_jsonb or {},
                performed_at=pred_row.performed_at,
                resolvable_at=pred_row.resolvable_at,
                score=score,
            )
            results.setdefault(pred_row.model_id, []).append(sp)
        return results

    @staticmethod
    def _domain_to_row(prediction: PredictionRecord) -> PredictionRow:
        return PredictionRow(
            id=prediction.id,
            input_id=prediction.input_id,
            model_id=prediction.model_id,
            prediction_config_id=prediction.prediction_config_id,
            scope_key=prediction.scope_key,
            scope_jsonb=prediction.scope,
            status=prediction.status,
            exec_time_ms=prediction.exec_time_ms,
            inference_output_jsonb=prediction.inference_output,
            meta_jsonb=prediction.meta,
            performed_at=prediction.performed_at,
            resolvable_at=prediction.resolvable_at or prediction.performed_at,
        )

    @staticmethod
    def _row_to_domain(row: PredictionRow) -> PredictionRecord:
        return PredictionRecord(
            id=row.id,
            input_id=row.input_id,
            model_id=row.model_id,
            prediction_config_id=row.prediction_config_id,
            scope_key=row.scope_key,
            scope=row.scope_jsonb or {},
            status=PredictionStatus(row.status),
            exec_time_ms=row.exec_time_ms,
            inference_output=row.inference_output_jsonb or {},
            meta=row.meta_jsonb or {},
            performed_at=row.performed_at,
            resolvable_at=row.resolvable_at,
        )


class DBScoreRepository:
    def __init__(self, session: Session):
        self._session = session

    def save(self, record: ScoreRecord) -> None:
        row = ScoreRow(
            id=record.id,
            prediction_id=record.prediction_id,
            result_jsonb=record.result,
            success=record.success,
            failed_reason=record.failed_reason,
            scored_at=record.scored_at,
        )
        existing = self._session.get(ScoreRow, row.id)
        if existing is None:
            self._session.add(row)
        else:
            existing.result_jsonb = row.result_jsonb
            existing.success = row.success
            existing.failed_reason = row.failed_reason
            existing.scored_at = row.scored_at
        self._session.commit()

    def find(
        self,
        *,
        prediction_id: str | None = None,
        model_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[ScoreRecord]:
        stmt = select(ScoreRow)
        if prediction_id is not None:
            stmt = stmt.where(ScoreRow.prediction_id == prediction_id)
        if since is not None:
            stmt = stmt.where(ScoreRow.scored_at >= since)
        if until is not None:
            stmt = stmt.where(ScoreRow.scored_at <= until)
        if limit is not None:
            stmt = stmt.limit(max(1, int(limit)))
        stmt = stmt.order_by(ScoreRow.scored_at.asc())
        rows = self._session.exec(stmt).all()
        return [
            ScoreRecord(
                id=r.id,
                prediction_id=r.prediction_id,
                result=r.result_jsonb or {},
                success=bool(r.success),
                failed_reason=r.failed_reason,
                scored_at=r.scored_at,
            )
            for r in rows
        ]


class DBLeaderboardRepository:
    def __init__(self, session: Session):
        self._session = session

    def rollback(self) -> None:
        self._session.rollback()

    def save(
        self,
        leaderboard_entries: list[dict[str, Any]],
        meta: dict[str, Any] | None = None,
    ) -> None:
        row = LeaderboardRow(
            id=f"LBR_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S.%f')[:-3]}",
            entries_jsonb=leaderboard_entries,
            meta_jsonb=meta or {},
        )
        self._session.add(row)
        self._session.commit()

    def get_latest(self) -> dict[str, Any] | None:
        row = self._session.exec(
            select(LeaderboardRow).order_by(LeaderboardRow.created_at.desc())
        ).first()
        if row is None:
            return None

        return {
            "id": row.id,
            "created_at": row.created_at,
            "entries": row.entries_jsonb,
            "meta": row.meta_jsonb,
        }

    def clear(self) -> None:
        self._session.exec(delete(LeaderboardRow))
        self._session.commit()


class DBSnapshotRepository:
    def __init__(self, session: Session):
        self._session = session

    def rollback(self) -> None:
        self._session.rollback()

    def save(self, record: SnapshotRecord) -> None:
        row = SnapshotRow(
            id=record.id,
            model_id=record.model_id,
            period_start=record.period_start,
            period_end=record.period_end,
            prediction_count=record.prediction_count,
            result_summary_jsonb=record.result_summary,
            meta_jsonb=record.meta,
            created_at=record.created_at,
        )
        existing = self._session.get(SnapshotRow, row.id)
        if existing is None:
            self._session.add(row)
        else:
            existing.result_summary_jsonb = row.result_summary_jsonb
            existing.prediction_count = row.prediction_count
            existing.meta_jsonb = row.meta_jsonb
        self._session.commit()

    def find(
        self,
        *,
        model_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[SnapshotRecord]:
        stmt = select(SnapshotRow)
        if model_id is not None:
            stmt = stmt.where(SnapshotRow.model_id == model_id)
        if since is not None:
            stmt = stmt.where(SnapshotRow.period_end >= since)
        if until is not None:
            stmt = stmt.where(SnapshotRow.period_start <= until)
        if limit is not None:
            stmt = stmt.limit(max(1, int(limit)))
        stmt = stmt.order_by(SnapshotRow.created_at.asc())
        rows = self._session.exec(stmt).all()
        return [
            SnapshotRecord(
                id=r.id,
                model_id=r.model_id,
                period_start=r.period_start,
                period_end=r.period_end,
                prediction_count=r.prediction_count,
                result_summary=r.result_summary_jsonb or {},
                meta=r.meta_jsonb or {},
                created_at=r.created_at,
            )
            for r in rows
        ]


class DBCheckpointRepository:
    def __init__(self, session: Session):
        self._session = session

    def rollback(self) -> None:
        self._session.rollback()

    def save(self, record: CheckpointRecord) -> None:
        row = CheckpointRow(
            id=record.id,
            period_start=record.period_start,
            period_end=record.period_end,
            status=record.status,
            entries_jsonb=record.entries,
            meta_jsonb=record.meta,
            created_at=record.created_at,
            tx_hash=record.tx_hash,
            submitted_at=record.submitted_at,
        )
        existing = self._session.get(CheckpointRow, row.id)
        if existing is None:
            self._session.add(row)
        else:
            existing.status = row.status
            existing.entries_jsonb = row.entries_jsonb
            existing.meta_jsonb = row.meta_jsonb
            existing.tx_hash = row.tx_hash
            existing.submitted_at = row.submitted_at
        self._session.commit()

    def find(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[CheckpointRecord]:
        stmt = select(CheckpointRow)
        if status is not None:
            stmt = stmt.where(CheckpointRow.status == status)
        if limit is not None:
            stmt = stmt.limit(max(1, int(limit)))
        stmt = stmt.order_by(CheckpointRow.created_at.desc())
        rows = self._session.exec(stmt).all()
        return [
            CheckpointRecord(
                id=r.id,
                period_start=r.period_start,
                period_end=r.period_end,
                status=CheckpointStatus(r.status),
                entries=r.entries_jsonb or [],
                meta=r.meta_jsonb or {},
                created_at=r.created_at,
                tx_hash=r.tx_hash,
                submitted_at=r.submitted_at,
            )
            for r in rows
        ]

    def get_latest(self) -> CheckpointRecord | None:
        row = self._session.exec(
            select(CheckpointRow).order_by(CheckpointRow.created_at.desc())
        ).first()
        if row is None:
            return None
        return CheckpointRecord(
            id=row.id,
            period_start=row.period_start,
            period_end=row.period_end,
            status=CheckpointStatus(row.status),
            entries=row.entries_jsonb or [],
            meta=row.meta_jsonb or {},
            created_at=row.created_at,
            tx_hash=row.tx_hash,
            submitted_at=row.submitted_at,
        )

    def update_merkle_root(self, checkpoint_id: str, merkle_root: str) -> None:
        row = self._session.get(CheckpointRow, checkpoint_id)
        if row is not None:
            row.merkle_root = merkle_root
            self._session.commit()


class DBMerkleCycleRepository:
    def __init__(self, session: Session):
        self._session = session

    def save(self, cycle: MerkleCycleRow) -> None:
        existing = self._session.get(MerkleCycleRow, cycle.id)
        if existing is None:
            self._session.add(cycle)
        else:
            existing.previous_cycle_id = cycle.previous_cycle_id
            existing.previous_cycle_root = cycle.previous_cycle_root
            existing.snapshots_root = cycle.snapshots_root
            existing.chained_root = cycle.chained_root
            existing.snapshot_count = cycle.snapshot_count
        self._session.commit()

    def get(self, cycle_id: str) -> MerkleCycleRow | None:
        return self._session.get(MerkleCycleRow, cycle_id)

    def get_latest(self) -> MerkleCycleRow | None:
        return self._session.exec(
            select(MerkleCycleRow).order_by(MerkleCycleRow.created_at.desc())
        ).first()

    def find(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[MerkleCycleRow]:
        stmt = select(MerkleCycleRow)
        if since is not None:
            stmt = stmt.where(MerkleCycleRow.created_at >= since)
        if until is not None:
            stmt = stmt.where(MerkleCycleRow.created_at <= until)
        if limit is not None:
            stmt = stmt.limit(max(1, int(limit)))
        stmt = stmt.order_by(MerkleCycleRow.created_at.asc())
        return list(self._session.exec(stmt).all())


class DBMerkleNodeRepository:
    def __init__(self, session: Session):
        self._session = session

    def save(self, node: MerkleNodeRow) -> None:
        existing = self._session.get(MerkleNodeRow, node.id)
        if existing is None:
            self._session.add(node)
        else:
            existing.hash = node.hash
            existing.left_child_id = node.left_child_id
            existing.right_child_id = node.right_child_id
            existing.snapshot_id = node.snapshot_id
            existing.snapshot_content_hash = node.snapshot_content_hash
        self._session.commit()

    def find_by_cycle_id(self, cycle_id: str) -> list[MerkleNodeRow]:
        stmt = (
            select(MerkleNodeRow)
            .where(
                MerkleNodeRow.cycle_id == cycle_id,
            )
            .order_by(MerkleNodeRow.level.asc(), MerkleNodeRow.position.asc())
        )
        return list(self._session.exec(stmt).all())

    def find_by_checkpoint_id(self, checkpoint_id: str) -> list[MerkleNodeRow]:
        stmt = (
            select(MerkleNodeRow)
            .where(
                MerkleNodeRow.checkpoint_id == checkpoint_id,
            )
            .order_by(MerkleNodeRow.level.asc(), MerkleNodeRow.position.asc())
        )
        return list(self._session.exec(stmt).all())

    def find_by_snapshot_id(self, snapshot_id: str) -> MerkleNodeRow | None:
        return self._session.exec(
            select(MerkleNodeRow).where(MerkleNodeRow.snapshot_id == snapshot_id)
        ).first()

    def find_by_hash_in_checkpoint(self, hash_value: str) -> list[MerkleNodeRow]:
        """Find checkpoint-level nodes matching a hash (used to link cycle → checkpoint)."""
        stmt = select(MerkleNodeRow).where(
            MerkleNodeRow.checkpoint_id.isnot(None),
            MerkleNodeRow.hash == hash_value,
            MerkleNodeRow.level == 0,
        )
        return list(self._session.exec(stmt).all())

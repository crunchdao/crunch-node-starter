from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from crunch_node.crunch_config import CrunchConfig
from crunch_node.entities.feed_record import FeedRecord
from crunch_node.entities.model import Model
from crunch_node.entities.prediction import (
    CheckpointRecord,
    CheckpointStatus,
    PredictionStatus,
    ScoredPrediction,
    ScoreRecord,
    SnapshotRecord,
)
from crunch_node.workers.report_worker import (
    auto_report_schema,
    confirm_checkpoint,
    get_checkpoint_emission,
    get_checkpoint_emission_cli_format,
    get_checkpoint_payload,
    get_checkpoints,
    get_feeds,
    get_feeds_tail,
    get_latest_checkpoint,
    get_latest_emission,
    get_leaderboard,
    get_models,
    get_models_global,
    get_models_params,
    get_predictions,
    get_report_schema,
    get_report_schema_leaderboard_columns,
    get_report_schema_metrics_widgets,
    get_snapshots,
    update_checkpoint_status,
)

# ── In-memory repositories ──────────────────────────────────────────────


class InMemoryModelRepository:
    def __init__(self, models: dict[str, Model] | None = None):
        self._models = models or {}

    def fetch_all(self):
        return dict(self._models)

    def save(self, model):
        self._models[model.id] = model

    def save_all(self, models):
        for m in models:
            self.save(m)


class InMemoryLeaderboardRepository:
    def __init__(self, entries=None, meta=None):
        self._latest = (
            {"entries": entries or [], "meta": meta or {}} if entries else None
        )

    def save(self, entries, meta=None):
        self._latest = {"entries": entries, "meta": meta or {}}

    def get_latest(self):
        return self._latest


class InMemoryFeedRecordRepository:
    def __init__(
        self,
        records: list[FeedRecord] | None = None,
        summaries: list[dict] | None = None,
    ):
        self._records = records or []
        self._summaries = summaries or []

    def list_indexed_feeds(self):
        return list(self._summaries)

    def tail_records(
        self, *, source=None, subject=None, kind=None, granularity=None, limit=20
    ):
        rows = list(self._records)
        if source:
            rows = [r for r in rows if r.source == source]
        if subject:
            rows = [r for r in rows if r.subject == subject]
        rows.sort(key=lambda r: r.ts_event, reverse=True)
        return rows[:limit]


class InMemoryPredictionRepository:
    def __init__(
        self, scored_predictions: dict[str, list[ScoredPrediction]] | None = None
    ):
        self._data = scored_predictions or {}

    def query_scores(self, *, model_ids, _from=None, to=None):
        result = {}
        for mid in model_ids:
            preds = self._data.get(mid, [])
            if _from:
                preds = [p for p in preds if p.performed_at >= _from]
            if to:
                preds = [p for p in preds if p.performed_at <= to]
            if preds:
                result[mid] = preds
        return result


class InMemorySnapshotRepository:
    def __init__(self, snapshots: list[SnapshotRecord] | None = None):
        self._snapshots = snapshots or []

    def find(self, *, model_id=None, since=None, until=None, limit=100):
        rows = list(self._snapshots)
        if model_id:
            rows = [s for s in rows if s.model_id == model_id]
        if since:
            rows = [s for s in rows if s.period_start >= since]
        if until:
            rows = [s for s in rows if s.period_end <= until]
        return rows[:limit]


class InMemoryCheckpointRepository:
    def __init__(self, checkpoints: list[CheckpointRecord] | None = None):
        self._checkpoints = checkpoints or []

    def find(self, *, status=None, limit=20):
        rows = list(self._checkpoints)
        if status:
            rows = [c for c in rows if c.status == status]
        return rows[:limit]

    def get_latest(self):
        if not self._checkpoints:
            return None
        return self._checkpoints[-1]

    def save(self, checkpoint):
        for i, c in enumerate(self._checkpoints):
            if c.id == checkpoint.id:
                self._checkpoints[i] = checkpoint
                return
        self._checkpoints.append(checkpoint)


# ── Helpers ──────────────────────────────────────────────────────────────

NOW = datetime(2026, 2, 13, 12, 0, 0, tzinfo=UTC)


def _make_model(model_id="m1", name="model-alpha", player_id="p1", player_name="alice"):
    return Model(
        id=model_id,
        name=name,
        player_id=player_id,
        player_name=player_name,
        deployment_identifier="d1",
    )


def _make_scored_prediction(
    model_id,
    score_value,
    performed_at=None,
    scope_key="BTC:60s",
    success=True,
    failed_reason=None,
):
    ts = performed_at or NOW
    score = ScoreRecord(
        id=f"SCR_{model_id}_{score_value}",
        prediction_id=f"PRED_{model_id}",
        result={"value": score_value},
        success=success,
        failed_reason=failed_reason,
        scored_at=ts,
    )
    return ScoredPrediction(
        id=f"PRED_{model_id}_{score_value}",
        input_id="inp1",
        model_id=model_id,
        prediction_config_id="cfg1",
        scope_key=scope_key,
        scope={"subject": "BTC", "horizon": "60s"},
        status=PredictionStatus.SCORED,
        exec_time_ms=10.0,
        performed_at=ts,
        score=score,
    )


def _make_checkpoint(
    checkpoint_id="cp1", status=CheckpointStatus.PENDING, entries=None, meta=None
):
    return CheckpointRecord(
        id=checkpoint_id,
        period_start=NOW - timedelta(days=7),
        period_end=NOW,
        status=status,
        entries=entries or [],
        meta=meta or {},
        created_at=NOW,
    )


SAMPLE_EMISSION = {
    "crunch": "CRUNCHpubkey123",
    "cruncher_rewards": [
        {"cruncher_index": 0, "reward_pct": 600_000_000},
        {"cruncher_index": 1, "reward_pct": 400_000_000},
    ],
    "compute_provider_rewards": [{"provider": "CPwallet", "reward_pct": 500_000_000}],
    "data_provider_rewards": [{"provider": "DPwallet", "reward_pct": 500_000_000}],
}


# ── ScoreRecord.value ────────────────────────────────────────────────────


class TestScoreRecordValue(unittest.TestCase):
    def test_value_returns_float(self):
        s = ScoreRecord(id="s1", prediction_id="p1", result={"value": 0.75})
        self.assertAlmostEqual(s.value, 0.75)

    def test_value_returns_none_when_missing(self):
        s = ScoreRecord(id="s1", prediction_id="p1", result={})
        self.assertIsNone(s.value)

    def test_value_returns_none_for_explicit_none(self):
        s = ScoreRecord(id="s1", prediction_id="p1", result={"value": None})
        self.assertIsNone(s.value)

    def test_value_coerces_int_to_float(self):
        s = ScoreRecord(id="s1", prediction_id="p1", result={"value": 3})
        self.assertAlmostEqual(s.value, 3.0)
        self.assertIsInstance(s.value, float)


# ── ScoredPrediction ─────────────────────────────────────────────────────


class TestScoredPrediction(unittest.TestCase):
    def test_has_score(self):
        sp = _make_scored_prediction("m1", 0.9)
        self.assertIsNotNone(sp.score)
        self.assertAlmostEqual(sp.score.value, 0.9)

    def test_without_score(self):
        sp = ScoredPrediction(
            id="p1",
            input_id="i1",
            model_id="m1",
            prediction_config_id=None,
            scope_key="BTC:60s",
            scope={},
            status=PredictionStatus.PENDING,
            exec_time_ms=5.0,
            score=None,
        )
        self.assertIsNone(sp.score)


# ── /reports/schema ──────────────────────────────────────────────────────


class TestReportSchema(unittest.TestCase):
    def test_get_report_schema(self):
        schema = get_report_schema()
        self.assertIn("leaderboard_columns", schema)
        self.assertIn("metrics_widgets", schema)

    def test_get_leaderboard_columns(self):
        columns = get_report_schema_leaderboard_columns()
        self.assertIsInstance(columns, list)
        self.assertTrue(len(columns) > 0)
        for col in columns:
            self.assertIn("property", col)

    def test_get_metrics_widgets(self):
        widgets = get_report_schema_metrics_widgets()
        self.assertIsInstance(widgets, list)
        self.assertTrue(len(widgets) > 0)
        for w in widgets:
            self.assertIn("id", w)
            self.assertIn("endpointUrl", w)

    def test_auto_report_schema_from_contract(self):
        schema = auto_report_schema(CrunchConfig())
        self.assertTrue(len(schema.get("leaderboard_columns", [])) > 0)


# ── /info ────────────────────────────────────────────────────────────────


class TestGetNodeInfo(unittest.TestCase):
    def test_returns_crunch_identity(self):
        from fastapi.testclient import TestClient

        from crunch_node.workers.report_worker import app

        with TestClient(app) as client:
            response = client.get("/info")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIn("crunch_id", data)
            self.assertIn("crunch_address", data)
            self.assertIn("network", data)
            self.assertIsInstance(data["crunch_id"], str)
            self.assertIsInstance(data["crunch_address"], str)
            self.assertIsInstance(data["network"], str)


# ── /reports/models ──────────────────────────────────────────────────────


class TestGetModels(unittest.TestCase):
    def test_returns_model_list(self):
        repo = InMemoryModelRepository({"m1": _make_model()})
        result = get_models(repo)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["model_id"], "m1")
        self.assertEqual(result[0]["model_name"], "model-alpha")
        self.assertEqual(result[0]["cruncher_name"], "alice")

    def test_returns_empty_when_no_models(self):
        result = get_models(InMemoryModelRepository())
        self.assertEqual(result, [])

    def test_returns_multiple_models(self):
        models = {
            "m1": _make_model("m1", "alpha"),
            "m2": _make_model("m2", "beta", "p2", "bob"),
        }
        result = get_models(InMemoryModelRepository(models))
        self.assertEqual(len(result), 2)
        ids = {r["model_id"] for r in result}
        self.assertEqual(ids, {"m1", "m2"})


# ── /reports/leaderboard ─────────────────────────────────────────────────


class TestGetLeaderboard(unittest.TestCase):
    def test_returns_sorted_entries(self):
        entries = [
            {
                "model_id": "m2",
                "rank": 2,
                "model_name": "beta",
                "cruncher_name": "bob",
                "score": {
                    "metrics": {"score_recent": 0.5},
                    "ranking": {
                        "key": "score_recent",
                        "value": 0.5,
                        "direction": "desc",
                    },
                    "payload": {},
                },
            },
            {
                "model_id": "m1",
                "rank": 1,
                "model_name": "alpha",
                "cruncher_name": "alice",
                "score": {
                    "metrics": {"score_recent": 0.8},
                    "ranking": {
                        "key": "score_recent",
                        "value": 0.8,
                        "direction": "desc",
                    },
                    "payload": {},
                },
            },
        ]
        result = get_leaderboard(InMemoryLeaderboardRepository(entries=entries))
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["rank"], 1)
        self.assertEqual(result[0]["model_id"], "m1")
        # _flatten_metrics prefixes keys with "score_"
        self.assertIn("score_recent", result[0])

    def test_returns_empty_when_no_leaderboard(self):
        result = get_leaderboard(InMemoryLeaderboardRepository())
        self.assertEqual(result, [])

    def test_excludes_ensemble_models_by_default(self):
        entries = [
            {
                "model_id": "m1",
                "rank": 1,
                "model_name": "alpha",
                "cruncher_name": "alice",
                "score": {"metrics": {"score_recent": 0.8}, "ranking": {}},
            },
            {
                "model_id": "__ensemble_main__",
                "rank": 2,
                "model_name": "ensemble",
                "cruncher_name": "",
                "score": {"metrics": {"score_recent": 0.9}, "ranking": {}},
            },
        ]
        result = get_leaderboard(InMemoryLeaderboardRepository(entries=entries))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["model_id"], "m1")

    def test_includes_ensemble_models_when_requested(self):
        entries = [
            {
                "model_id": "m1",
                "rank": 1,
                "model_name": "alpha",
                "cruncher_name": "alice",
                "score": {"metrics": {"score_recent": 0.8}, "ranking": {}},
            },
            {
                "model_id": "__ensemble_main__",
                "rank": 2,
                "model_name": "ensemble",
                "cruncher_name": "",
                "score": {"metrics": {"score_recent": 0.9}, "ranking": {}},
            },
        ]
        result = get_leaderboard(
            InMemoryLeaderboardRepository(entries=entries), include_ensembles=True
        )
        self.assertEqual(len(result), 2)
        model_ids = {r["model_id"] for r in result}
        self.assertIn("__ensemble_main__", model_ids)


# ── /reports/models/global ───────────────────────────────────────────────


class TestGetModelsGlobal(unittest.TestCase):
    def _call(
        self,
        pred_repo=None,
        model_repo=None,
        snapshot_repo=None,
        model_ids=None,
        start=None,
        end=None,
    ):
        return get_models_global(
            prediction_repo=pred_repo or InMemoryPredictionRepository(),
            snapshot_repo=snapshot_repo or InMemorySnapshotRepository(),
            model_repo=model_repo or InMemoryModelRepository(),
            model_ids=model_ids,
            start=start,
            end=end,
        )

    def test_returns_empty_when_no_models(self):
        result = self._call()
        self.assertEqual(result, [])

    def test_returns_scores_for_explicit_model_ids(self):
        preds = {"m1": [_make_scored_prediction("m1", 0.8)]}
        result = self._call(
            pred_repo=InMemoryPredictionRepository(preds),
            model_ids=["m1"],
            start=NOW - timedelta(days=1),
            end=NOW + timedelta(days=1),
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["model_id"], "m1")
        self.assertIn("score_recent", result[0])
        self.assertIn("performed_at", result[0])

    def test_defaults_to_all_models_when_no_ids(self):
        models = {"m1": _make_model("m1"), "m2": _make_model("m2", "beta")}
        preds = {
            "m1": [_make_scored_prediction("m1", 0.8)],
            "m2": [_make_scored_prediction("m2", 0.6)],
        }
        result = self._call(
            pred_repo=InMemoryPredictionRepository(preds),
            model_repo=InMemoryModelRepository(models),
            start=NOW - timedelta(days=1),
            end=NOW + timedelta(days=1),
        )
        self.assertEqual(len(result), 2)

    def test_skips_models_with_no_successful_scores(self):
        preds = {"m1": [_make_scored_prediction("m1", 0.5, success=False)]}
        result = self._call(
            pred_repo=InMemoryPredictionRepository(preds),
            model_ids=["m1"],
            start=NOW - timedelta(days=1),
            end=NOW + timedelta(days=1),
        )
        self.assertEqual(result, [])

    def test_returns_ranking_info(self):
        preds = {"m1": [_make_scored_prediction("m1", 0.7)]}
        result = self._call(
            pred_repo=InMemoryPredictionRepository(preds),
            model_ids=["m1"],
            start=NOW - timedelta(days=1),
            end=NOW + timedelta(days=1),
        )
        self.assertIn("score_ranking", result[0])
        self.assertIn("key", result[0]["score_ranking"])

    def test_handles_naive_datetime_timestamps(self):
        """Regression: performed_at from PostgreSQL may be timezone-naive.
        _compute_window_metrics must not raise TypeError when comparing
        naive timestamps against an aware cutoff."""
        naive_ts = datetime(2026, 2, 13, 12, 0, 0)  # no tzinfo
        preds = {"m1": [_make_scored_prediction("m1", 0.8, performed_at=naive_ts)]}
        # Should not raise TypeError
        result = self._call(
            pred_repo=InMemoryPredictionRepository(preds),
            model_ids=["m1"],
            start=naive_ts - timedelta(days=1),
            end=naive_ts + timedelta(days=1),
        )
        self.assertEqual(len(result), 1)


# ── /reports/models/params ───────────────────────────────────────────────


class TestGetModelsParams(unittest.TestCase):
    def _call(
        self, pred_repo=None, model_repo=None, model_ids=None, start=None, end=None
    ):
        return get_models_params(
            prediction_repo=pred_repo or InMemoryPredictionRepository(),
            model_repo=model_repo or InMemoryModelRepository(),
            model_ids=model_ids,
            start=start,
            end=end,
        )

    def test_returns_empty_when_no_data(self):
        result = self._call()
        self.assertEqual(result, [])

    def test_groups_by_scope_key(self):
        preds = {
            "m1": [
                _make_scored_prediction("m1", 0.8, scope_key="BTC:60s"),
                _make_scored_prediction("m1", 0.6, scope_key="ETH:60s"),
            ]
        }
        result = self._call(
            pred_repo=InMemoryPredictionRepository(preds),
            model_ids=["m1"],
            start=NOW - timedelta(days=1),
            end=NOW + timedelta(days=1),
        )
        self.assertEqual(len(result), 2)
        scope_keys = {r["scope_key"] for r in result}
        self.assertEqual(scope_keys, {"BTC:60s", "ETH:60s"})

    def test_includes_scope_and_ranking(self):
        preds = {"m1": [_make_scored_prediction("m1", 0.7)]}
        result = self._call(
            pred_repo=InMemoryPredictionRepository(preds),
            model_ids=["m1"],
            start=NOW - timedelta(days=1),
            end=NOW + timedelta(days=1),
        )
        self.assertIn("scope", result[0])
        self.assertIn("score_ranking", result[0])

    def test_handles_naive_datetime_timestamps(self):
        """Regression: performed_at from PostgreSQL may be timezone-naive.
        _compute_window_metrics must not raise TypeError when comparing
        naive timestamps against an aware cutoff."""
        naive_ts = datetime(2026, 2, 13, 12, 0, 0)  # no tzinfo
        preds = {"m1": [_make_scored_prediction("m1", 0.8, performed_at=naive_ts)]}
        result = self._call(
            pred_repo=InMemoryPredictionRepository(preds),
            model_ids=["m1"],
            start=naive_ts - timedelta(days=1),
            end=naive_ts + timedelta(days=1),
        )
        self.assertEqual(len(result), 1)


# ── /reports/predictions ─────────────────────────────────────────────────


class TestGetPredictions(unittest.TestCase):
    def _call(
        self, pred_repo=None, model_repo=None, model_ids=None, start=None, end=None
    ):
        return get_predictions(
            prediction_repo=pred_repo or InMemoryPredictionRepository(),
            model_repo=model_repo or InMemoryModelRepository(),
            model_ids=model_ids,
            start=start,
            end=end,
        )

    def test_returns_empty_when_no_data(self):
        result = self._call()
        self.assertEqual(result, [])

    def test_returns_prediction_rows_with_score_fields(self):
        preds = {"m1": [_make_scored_prediction("m1", 0.9)]}
        result = self._call(
            pred_repo=InMemoryPredictionRepository(preds),
            model_ids=["m1"],
            start=NOW - timedelta(days=1),
            end=NOW + timedelta(days=1),
        )
        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertEqual(row["model_id"], "m1")
        self.assertAlmostEqual(row["score_value"], 0.9)
        self.assertFalse(row["score_failed"])
        self.assertIn("performed_at", row)

    def test_handles_prediction_without_score(self):
        sp = ScoredPrediction(
            id="p1",
            input_id="i1",
            model_id="m1",
            prediction_config_id=None,
            scope_key="BTC:60s",
            scope={},
            status=PredictionStatus.PENDING,
            exec_time_ms=5.0,
            performed_at=NOW,
            score=None,
        )
        preds = {"m1": [sp]}
        result = self._call(
            pred_repo=InMemoryPredictionRepository(preds),
            model_ids=["m1"],
            start=NOW - timedelta(days=1),
            end=NOW + timedelta(days=1),
        )
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["score_value"])
        self.assertTrue(result[0]["score_failed"])

    def test_results_sorted_by_performed_at(self):
        t1 = NOW - timedelta(hours=2)
        t2 = NOW - timedelta(hours=1)
        preds = {
            "m1": [
                _make_scored_prediction("m1", 0.5, performed_at=t2),
                _make_scored_prediction("m1", 0.3, performed_at=t1),
            ]
        }
        result = self._call(
            pred_repo=InMemoryPredictionRepository(preds),
            model_ids=["m1"],
            start=NOW - timedelta(days=1),
            end=NOW + timedelta(days=1),
        )
        self.assertTrue(result[0]["performed_at"] <= result[1]["performed_at"])


# ── /reports/feeds ───────────────────────────────────────────────────────


class TestGetFeeds(unittest.TestCase):
    def test_returns_indexed_summaries(self):
        summaries = [
            {
                "source": "binance",
                "subject": "BTC",
                "kind": "candle",
                "granularity": "1m",
                "record_count": 100,
            },
        ]
        result = get_feeds(InMemoryFeedRecordRepository(summaries=summaries))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "binance")

    def test_returns_empty_when_no_feeds(self):
        result = get_feeds(InMemoryFeedRecordRepository())
        self.assertEqual(result, [])


# ── /reports/feeds/tail ──────────────────────────────────────────────────


class TestGetFeedsTail(unittest.TestCase):
    def test_returns_recent_records(self):
        records = [
            FeedRecord(
                source="pyth",
                subject="BTC",
                kind="tick",
                granularity="1s",
                ts_event=NOW - timedelta(seconds=i),
                values={"price": 50000.0 + i},
            )
            for i in range(5)
        ]
        result = get_feeds_tail(
            InMemoryFeedRecordRepository(records=records),
            "pyth",
            "BTC",
            "tick",
            "1s",
            3,
        )
        self.assertEqual(len(result), 3)
        self.assertIn("values", result[0])
        self.assertIn("ts_event", result[0])

    def test_returns_empty_when_no_records(self):
        result = get_feeds_tail(
            InMemoryFeedRecordRepository(), "pyth", "BTC", "tick", "1s", 10
        )
        self.assertEqual(result, [])


# ── /reports/snapshots ───────────────────────────────────────────────────


class TestGetSnapshots(unittest.TestCase):
    def test_returns_snapshot_list(self):
        snapshots = [
            SnapshotRecord(
                id="s1",
                model_id="m1",
                period_start=NOW - timedelta(hours=1),
                period_end=NOW,
                prediction_count=10,
                result_summary={"score_recent": 0.8},
            ),
        ]
        result = get_snapshots(InMemorySnapshotRepository(snapshots))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "s1")
        self.assertEqual(result[0]["model_id"], "m1")
        self.assertEqual(result[0]["prediction_count"], 10)

    def test_returns_empty_when_no_snapshots(self):
        result = get_snapshots(InMemorySnapshotRepository())
        self.assertEqual(result, [])

    def test_filters_by_model_id(self):
        snapshots = [
            SnapshotRecord(
                id="s1",
                model_id="m1",
                period_start=NOW - timedelta(hours=1),
                period_end=NOW,
                prediction_count=5,
            ),
            SnapshotRecord(
                id="s2",
                model_id="m2",
                period_start=NOW - timedelta(hours=1),
                period_end=NOW,
                prediction_count=3,
            ),
        ]
        result = get_snapshots(InMemorySnapshotRepository(snapshots), model_id="m1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["model_id"], "m1")


# ── /reports/checkpoints ─────────────────────────────────────────────────


class TestGetCheckpoints(unittest.TestCase):
    def test_returns_checkpoint_list(self):
        cps = [_make_checkpoint("cp1"), _make_checkpoint("cp2")]
        result = get_checkpoints(InMemoryCheckpointRepository(cps))
        self.assertEqual(len(result), 2)

    def test_returns_empty_when_none(self):
        result = get_checkpoints(InMemoryCheckpointRepository())
        self.assertEqual(result, [])


# ── /reports/checkpoints/latest ──────────────────────────────────────────


class TestGetLatestCheckpoint(unittest.TestCase):
    def test_returns_latest(self):
        cps = [_make_checkpoint("cp1"), _make_checkpoint("cp2")]
        result = get_latest_checkpoint(InMemoryCheckpointRepository(cps))
        self.assertEqual(result["id"], "cp2")

    def test_raises_404_when_empty(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            get_latest_checkpoint(InMemoryCheckpointRepository())
        self.assertEqual(ctx.exception.status_code, 404)


# ── /reports/checkpoints/{id}/payload ────────────────────────────────────


class TestGetCheckpointPayload(unittest.TestCase):
    def test_returns_payload(self):
        cp = _make_checkpoint("cp1", entries=[SAMPLE_EMISSION])
        result = get_checkpoint_payload("cp1", InMemoryCheckpointRepository([cp]))
        self.assertEqual(result["checkpoint_id"], "cp1")
        self.assertEqual(len(result["entries"]), 1)

    def test_raises_404_for_missing_checkpoint(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            get_checkpoint_payload("missing", InMemoryCheckpointRepository())
        self.assertEqual(ctx.exception.status_code, 404)


# ── /reports/checkpoints/{id}/confirm ────────────────────────────────────


class TestConfirmCheckpoint(unittest.TestCase):
    def test_confirms_pending_checkpoint(self):
        cp = _make_checkpoint("cp1")
        repo = InMemoryCheckpointRepository([cp])
        result = confirm_checkpoint("cp1", {"tx_hash": "0xabc"}, repo)
        self.assertEqual(result["status"], "SUBMITTED")
        self.assertEqual(result["tx_hash"], "0xabc")

    def test_rejects_non_pending(self):
        from fastapi import HTTPException

        cp = _make_checkpoint("cp1", status=CheckpointStatus.SUBMITTED)
        with self.assertRaises(HTTPException) as ctx:
            confirm_checkpoint(
                "cp1", {"tx_hash": "0xabc"}, InMemoryCheckpointRepository([cp])
            )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_rejects_missing_tx_hash(self):
        from fastapi import HTTPException

        cp = _make_checkpoint("cp1")
        with self.assertRaises(HTTPException) as ctx:
            confirm_checkpoint("cp1", {}, InMemoryCheckpointRepository([cp]))
        self.assertEqual(ctx.exception.status_code, 422)

    def test_raises_404_for_missing_checkpoint(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            confirm_checkpoint(
                "missing", {"tx_hash": "0x"}, InMemoryCheckpointRepository()
            )
        self.assertEqual(ctx.exception.status_code, 404)


# ── /reports/checkpoints/{id}/status ─────────────────────────────────────


class TestUpdateCheckpointStatus(unittest.TestCase):
    def test_valid_transition_submitted_to_claimable(self):
        cp = _make_checkpoint("cp1", status=CheckpointStatus.SUBMITTED)
        repo = InMemoryCheckpointRepository([cp])
        result = update_checkpoint_status("cp1", {"status": "CLAIMABLE"}, repo)
        self.assertEqual(result["status"], "CLAIMABLE")

    def test_valid_transition_claimable_to_paid(self):
        cp = _make_checkpoint("cp1", status=CheckpointStatus.CLAIMABLE)
        repo = InMemoryCheckpointRepository([cp])
        result = update_checkpoint_status("cp1", {"status": "PAID"}, repo)
        self.assertEqual(result["status"], "PAID")

    def test_rejects_invalid_transition(self):
        from fastapi import HTTPException

        cp = _make_checkpoint("cp1", status=CheckpointStatus.PENDING)
        with self.assertRaises(HTTPException) as ctx:
            update_checkpoint_status(
                "cp1", {"status": "PAID"}, InMemoryCheckpointRepository([cp])
            )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_rejects_invalid_status_value(self):
        from fastapi import HTTPException

        cp = _make_checkpoint("cp1", status=CheckpointStatus.PENDING)
        with self.assertRaises(HTTPException) as ctx:
            update_checkpoint_status(
                "cp1", {"status": "BOGUS"}, InMemoryCheckpointRepository([cp])
            )
        self.assertEqual(ctx.exception.status_code, 422)

    def test_raises_404_for_missing_checkpoint(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            update_checkpoint_status(
                "missing", {"status": "SUBMITTED"}, InMemoryCheckpointRepository()
            )
        self.assertEqual(ctx.exception.status_code, 404)


# ── /reports/checkpoints/{id}/emission ───────────────────────────────────


class TestGetCheckpointEmission(unittest.TestCase):
    def test_returns_emission(self):
        cp = _make_checkpoint("cp1", entries=[SAMPLE_EMISSION])
        result = get_checkpoint_emission("cp1", InMemoryCheckpointRepository([cp]))
        self.assertEqual(result["crunch"], "CRUNCHpubkey123")
        self.assertEqual(len(result["cruncher_rewards"]), 2)

    def test_raises_404_when_no_entries(self):
        from fastapi import HTTPException

        cp = _make_checkpoint("cp1", entries=[])
        with self.assertRaises(HTTPException):
            get_checkpoint_emission("cp1", InMemoryCheckpointRepository([cp]))

    def test_raises_404_for_missing_checkpoint(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException):
            get_checkpoint_emission("missing", InMemoryCheckpointRepository())


# ── /reports/checkpoints/{id}/emission/cli-format ────────────────────────


class TestGetCheckpointEmissionCliFormat(unittest.TestCase):
    def test_returns_cli_format(self):
        meta = {"ranking": [{"model_id": "m1"}, {"model_id": "m2"}]}
        cp = _make_checkpoint("cp1", entries=[SAMPLE_EMISSION], meta=meta)
        result = get_checkpoint_emission_cli_format(
            "cp1", InMemoryCheckpointRepository([cp])
        )
        self.assertEqual(result["crunch"], "CRUNCHpubkey123")
        self.assertIn("crunchEmission", result)
        self.assertIn("computeProvider", result)
        self.assertIn("dataProvider", result)
        # Check pct conversion: 600_000_000 / 1_000_000_000 * 100 = 60%
        self.assertAlmostEqual(result["crunchEmission"]["m1"], 60.0)
        self.assertAlmostEqual(result["crunchEmission"]["m2"], 40.0)
        self.assertAlmostEqual(result["computeProvider"]["CPwallet"], 50.0)

    def test_raises_404_when_no_entries(self):
        from fastapi import HTTPException

        cp = _make_checkpoint("cp1", entries=[])
        with self.assertRaises(HTTPException):
            get_checkpoint_emission_cli_format(
                "cp1", InMemoryCheckpointRepository([cp])
            )


# ── /reports/emissions/latest ────────────────────────────────────────────


class TestGetLatestEmission(unittest.TestCase):
    def test_returns_latest_emission(self):
        cp = _make_checkpoint("cp1", entries=[SAMPLE_EMISSION])
        result = get_latest_emission(InMemoryCheckpointRepository([cp]))
        self.assertEqual(result["checkpoint_id"], "cp1")
        self.assertIn("emission", result)
        self.assertEqual(result["emission"]["crunch"], "CRUNCHpubkey123")

    def test_raises_404_when_no_checkpoints(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException):
            get_latest_emission(InMemoryCheckpointRepository())

    def test_raises_404_when_no_emission_data(self):
        from fastapi import HTTPException

        cp = _make_checkpoint("cp1", entries=[])
        with self.assertRaises(HTTPException):
            get_latest_emission(InMemoryCheckpointRepository([cp]))


if __name__ == "__main__":
    unittest.main()

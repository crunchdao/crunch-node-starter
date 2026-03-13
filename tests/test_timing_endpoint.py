"""
Tests for the timing metrics HTTP endpoint.

The /timing-metrics endpoint uses aggregate_timing_from_predictions() which
reads from DB. These tests mock the DB layer to avoid requiring postgres.
"""

from datetime import UTC, datetime
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from crunch_node.entities.prediction import PredictionRecord, PredictionStatus


def _make_prediction_with_timing(pred_id, timing_data):
    return PredictionRecord(
        id=pred_id,
        input_id="input-1",
        model_id="test-model",
        prediction_config_id="test-config",
        scope_key="test-scope",
        scope={"subject": "BTC"},
        status=PredictionStatus.PENDING,
        exec_time_ms=10.0,
        inference_output={"value": 1.0},
        performed_at=datetime.now(UTC),
        resolvable_at=datetime.now(UTC),
        meta={"timing": timing_data},
    )


def test_timing_endpoint_enabled():
    with patch("crunch_node.workers.report_worker.CONTRACT") as mock_contract:
        mock_contract.performance.timing_endpoint_enabled = True

        from crunch_node.workers.report_worker import app

        client = TestClient(app)

        predictions = [
            _make_prediction_with_timing(
                "pred-1",
                {"feed_received_us": 1000, "persistence_completed_us": 2000},
            )
        ]

        with patch("crunch_node.workers.report_worker.create_session") as mock_session:
            mock_repo = Mock()
            mock_repo.fetch_recent_with_timing.return_value = predictions
            mock_session.return_value.__enter__ = Mock(return_value=Mock())
            mock_session.return_value.__exit__ = Mock(return_value=False)

            with patch(
                "crunch_node.workers.report_worker.DBPredictionRepository",
                return_value=mock_repo,
            ):
                response = client.get("/timing-metrics")

        assert response.status_code == 200

        data = response.json()
        assert data["enabled"] is True
        assert data["total_records"] == 1
        assert "stage_latencies" in data
        assert "recent_samples" in data


def test_timing_endpoint_disabled():
    with patch("crunch_node.workers.report_worker.CONTRACT") as mock_contract:
        mock_contract.performance.timing_endpoint_enabled = False

        from crunch_node.workers.report_worker import app

        client = TestClient(app)

        response = client.get("/timing-metrics")
        assert response.status_code == 404
        assert "disabled" in response.json()["detail"].lower()


def test_timing_endpoint_empty_results():
    with patch("crunch_node.workers.report_worker.CONTRACT") as mock_contract:
        mock_contract.performance.timing_endpoint_enabled = True

        from crunch_node.workers.report_worker import app

        client = TestClient(app)

        with patch("crunch_node.workers.report_worker.create_session") as mock_session:
            mock_repo = Mock()
            mock_repo.fetch_recent_with_timing.return_value = []
            mock_session.return_value.__enter__ = Mock(return_value=Mock())
            mock_session.return_value.__exit__ = Mock(return_value=False)

            with patch(
                "crunch_node.workers.report_worker.DBPredictionRepository",
                return_value=mock_repo,
            ):
                response = client.get("/timing-metrics")

        assert response.status_code == 200

        data = response.json()
        assert data["total_records"] == 0
        assert data["buffer_size"] == 0
        assert data["stage_latencies"] == []
        assert data["recent_samples"] == []

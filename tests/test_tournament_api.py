"""Tests for tournament API endpoints (scaffold/node/api/tournament.py)."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from crunch_node.entities.prediction import (
    PredictionRecord,
    PredictionStatus,
    ScoreRecord,
)

# Add scaffold/node to path for API discovery
scaffold_api_dir = str(Path(__file__).parent.parent / "scaffold" / "node")
if scaffold_api_dir not in sys.path:
    sys.path.insert(0, scaffold_api_dir)


def _make_app_with_mock_service(mock_service) -> FastAPI:
    """Create a FastAPI app with tournament router using a mock service."""
    # Import fresh to avoid cached singleton
    import importlib

    import api.tournament as tournament_module

    importlib.reload(tournament_module)

    # Patch the service singletons
    tournament_module._service = mock_service
    tournament_module._score_service = tournament_module._ScoreServices(
        snapshot_repo=MagicMock(),
        leaderboard_service=MagicMock(),
        config=MagicMock(),
    )

    app = FastAPI()
    app.include_router(tournament_module.router)
    return app


class TestInferenceEndpoint(unittest.TestCase):
    def test_inference_success(self):
        mock_service = MagicMock()
        mock_service.run_inference = AsyncMock(
            return_value=[
                PredictionRecord(
                    id="PRE_model1_round-001",
                    input_id="INP_round-001",
                    model_id="model1",
                    prediction_config_id=None,
                    scope_key="round-001",
                    scope={},
                    status=PredictionStatus.PENDING,
                    exec_time_ms=10.0,
                ),
                PredictionRecord(
                    id="PRE_model2_round-001",
                    input_id="INP_round-001",
                    model_id="model2",
                    prediction_config_id=None,
                    scope_key="round-001",
                    scope={},
                    status=PredictionStatus.PENDING,
                    exec_time_ms=15.0,
                ),
            ]
        )

        app = _make_app_with_mock_service(mock_service)
        client = TestClient(app)

        response = client.post(
            "/tournament/rounds/round-001/inference",
            json={"features": [{"x": 1.0}, {"x": 2.0}]},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["round_id"], "round-001")
        self.assertEqual(data["model_count"], 2)
        self.assertEqual(data["prediction_count"], 2)
        self.assertEqual(data["status"], "inference_complete")

    def test_inference_empty_features(self):
        mock_service = MagicMock()
        app = _make_app_with_mock_service(mock_service)
        client = TestClient(app)

        response = client.post(
            "/tournament/rounds/round-001/inference",
            json={"features": []},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("empty", response.json()["detail"])

    def test_inference_error(self):
        mock_service = MagicMock()
        mock_service.run_inference = AsyncMock(
            side_effect=RuntimeError("model crashed")
        )

        app = _make_app_with_mock_service(mock_service)
        client = TestClient(app)

        response = client.post(
            "/tournament/rounds/round-001/inference",
            json={"features": [{"x": 1.0}]},
        )

        self.assertEqual(response.status_code, 500)


class TestScoreEndpoint(unittest.TestCase):
    def test_score_success(self):
        mock_service = MagicMock()
        mock_service.score_round = MagicMock(
            return_value=[
                ScoreRecord(
                    id="SCR_PRE_model1_round-001",
                    prediction_id="PRE_model1_round-001",
                    result={"value": 0.92, "success": True, "failed_reason": None},
                    success=True,
                ),
            ]
        )

        app = _make_app_with_mock_service(mock_service)
        client = TestClient(app)

        response = client.post(
            "/tournament/rounds/round-001/score",
            json={"ground_truth": {"price": 500000}},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["round_id"], "round-001")
        self.assertEqual(data["scores_count"], 1)
        self.assertEqual(data["results"][0]["score"], 0.92)
        self.assertTrue(data["results"][0]["success"])

    def test_score_with_list_ground_truth(self):
        mock_service = MagicMock()
        mock_service.score_round = MagicMock(return_value=[])

        app = _make_app_with_mock_service(mock_service)
        client = TestClient(app)

        response = client.post(
            "/tournament/rounds/round-001/score",
            json={"ground_truth": [{"price": 500000}, {"price": 600000}]},
        )

        self.assertEqual(response.status_code, 200)
        mock_service.score_round.assert_called_once()

    def test_score_no_scoring_function(self):
        mock_service = MagicMock()
        mock_service.score_round = MagicMock(
            side_effect=RuntimeError("No scoring_function configured")
        )

        app = _make_app_with_mock_service(mock_service)
        client = TestClient(app)

        response = client.post(
            "/tournament/rounds/round-001/score",
            json={"ground_truth": {"price": 1.0}},
        )

        self.assertEqual(response.status_code, 400)


class TestRoundStatusEndpoint(unittest.TestCase):
    def test_status_not_found(self):
        mock_service = MagicMock()
        mock_service.get_round_status = MagicMock(
            return_value={
                "round_id": "nonexistent",
                "status": "not_found",
                "total": 0,
                "by_status": {},
            }
        )

        app = _make_app_with_mock_service(mock_service)
        client = TestClient(app)

        response = client.get("/tournament/rounds/nonexistent/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "not_found")

    def test_status_inference_complete(self):
        mock_service = MagicMock()
        mock_service.get_round_status = MagicMock(
            return_value={
                "round_id": "round-001",
                "status": "inference_complete",
                "total": 3,
                "by_status": {"PENDING": 3},
            }
        )

        app = _make_app_with_mock_service(mock_service)
        client = TestClient(app)

        response = client.get("/tournament/rounds/round-001/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "inference_complete")
        self.assertEqual(data["total"], 3)

    def test_status_scored(self):
        mock_service = MagicMock()
        mock_service.get_round_status = MagicMock(
            return_value={
                "round_id": "round-001",
                "status": "scored",
                "total": 3,
                "by_status": {"SCORED": 3},
            }
        )

        app = _make_app_with_mock_service(mock_service)
        client = TestClient(app)

        response = client.get("/tournament/rounds/round-001/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "scored")


if __name__ == "__main__":
    unittest.main()

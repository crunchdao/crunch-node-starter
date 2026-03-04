"""Smoke tests for the report API.

These verify the FastAPI app boots successfully and core endpoints respond.
Catches module-level failures: bad CrunchConfig, broken schema generation,
failed API route discovery, missing imports, etc.

Run after scaffolding or any change to CrunchConfig, report_worker, or api/.
"""

from __future__ import annotations

import unittest


class TestReportApiSmoke(unittest.TestCase):
    """Boot the report-worker app and hit core endpoints."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from crunch_node.workers.report_worker import app

        cls.client = TestClient(app)

    def test_healthz(self):
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_schema_returns_valid_structure(self):
        resp = self.client.get("/reports/schema")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("leaderboard_columns", data)
        self.assertIn("metrics_widgets", data)
        self.assertIsInstance(data["leaderboard_columns"], list)
        self.assertIsInstance(data["metrics_widgets"], list)
        # Must have at least the MODEL column
        model_cols = [
            c for c in data["leaderboard_columns"] if c.get("type") == "MODEL"
        ]
        self.assertGreaterEqual(
            len(model_cols), 1, "Schema must include a MODEL column"
        )

    def test_schema_leaderboard_columns(self):
        resp = self.client.get("/reports/schema/leaderboard-columns")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_schema_metrics_widgets(self):
        resp = self.client.get("/reports/schema/metrics-widgets")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_info(self):
        resp = self.client.get("/info")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("crunch_id", data)
        self.assertIn("crunch_address", data)
        self.assertIn("network", data)

    def test_openapi_schema_generates(self):
        """FastAPI's OpenAPI schema generation exercises all route signatures."""
        resp = self.client.get("/openapi.json")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("paths", data)
        self.assertIn("/healthz", data["paths"])


if __name__ == "__main__":
    unittest.main()

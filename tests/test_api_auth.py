"""Tests for API key authentication middleware."""

from __future__ import annotations

import unittest

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from crunch_node.middleware.auth import APIKeyMiddleware


def _make_app(api_key: str | None = None, read_auth: bool = False) -> FastAPI:
    """Build a test app with standard route tiers."""
    app = FastAPI()

    if api_key:
        app.add_middleware(
            APIKeyMiddleware,
            api_key=api_key,
            read_auth=read_auth,
        )

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/reports/leaderboard")
    def leaderboard():
        return [{"rank": 1}]

    @app.get("/reports/models")
    def models():
        return [{"model_id": "m1"}]

    @app.get("/reports/predictions")
    def predictions():
        return [{"pred": 1}]

    @app.get("/reports/snapshots")
    def snapshots():
        return [{"snap": 1}]

    @app.post("/reports/backfill")
    def start_backfill():
        return {"job_id": "j1"}

    @app.post("/reports/checkpoints/cp1/confirm")
    def confirm():
        return {"confirmed": True}

    router = APIRouter(prefix="/custom")

    @router.get("/data")
    def custom_data():
        return {"custom": True}

    app.include_router(router)

    return app


class TestNoApiKey(unittest.TestCase):
    """When API_KEY is not set, everything is open."""

    def setUp(self):
        self.client = TestClient(_make_app(api_key=None))

    def test_public_open(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/reports/leaderboard").status_code, 200)

    def test_read_open(self):
        self.assertEqual(self.client.get("/reports/predictions").status_code, 200)
        self.assertEqual(self.client.get("/reports/snapshots").status_code, 200)

    def test_admin_open(self):
        self.assertEqual(self.client.post("/reports/backfill").status_code, 200)
        self.assertEqual(
            self.client.post("/reports/checkpoints/cp1/confirm").status_code, 200
        )

    def test_custom_open(self):
        self.assertEqual(self.client.get("/custom/data").status_code, 200)


class TestApiKeyAdminOnly(unittest.TestCase):
    """Default: API_KEY set, read_auth=false. Only admin endpoints gated."""

    def setUp(self):
        self.client = TestClient(_make_app(api_key="secret123", read_auth=False))

    def test_public_no_key(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/reports/leaderboard").status_code, 200)
        self.assertEqual(self.client.get("/reports/models").status_code, 200)

    def test_read_no_key(self):
        # Read endpoints open when read_auth=false
        self.assertEqual(self.client.get("/reports/predictions").status_code, 200)
        self.assertEqual(self.client.get("/reports/snapshots").status_code, 200)

    def test_admin_rejected_without_key(self):
        self.assertEqual(self.client.post("/reports/backfill").status_code, 401)
        self.assertEqual(
            self.client.post("/reports/checkpoints/cp1/confirm").status_code, 401
        )

    def test_custom_rejected_without_key(self):
        self.assertEqual(self.client.get("/custom/data").status_code, 401)

    def test_admin_with_x_api_key_header(self):
        resp = self.client.post("/reports/backfill", headers={"X-API-Key": "secret123"})
        self.assertEqual(resp.status_code, 200)

    def test_admin_with_bearer_token(self):
        resp = self.client.post(
            "/reports/backfill",
            headers={"Authorization": "Bearer secret123"},
        )
        self.assertEqual(resp.status_code, 200)

    def test_admin_with_query_param(self):
        resp = self.client.post("/reports/backfill?api_key=secret123")
        self.assertEqual(resp.status_code, 200)

    def test_admin_wrong_key_rejected(self):
        resp = self.client.post("/reports/backfill", headers={"X-API-Key": "wrong"})
        self.assertEqual(resp.status_code, 401)

    def test_custom_with_key(self):
        resp = self.client.get("/custom/data", headers={"X-API-Key": "secret123"})
        self.assertEqual(resp.status_code, 200)


class TestApiKeyReadAuth(unittest.TestCase):
    """API_KEY set, read_auth=true. All non-public endpoints gated."""

    def setUp(self):
        self.client = TestClient(_make_app(api_key="secret123", read_auth=True))

    def test_public_still_open(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/reports/leaderboard").status_code, 200)

    def test_read_rejected_without_key(self):
        self.assertEqual(self.client.get("/reports/predictions").status_code, 401)
        self.assertEqual(self.client.get("/reports/snapshots").status_code, 401)

    def test_read_with_key(self):
        headers = {"X-API-Key": "secret123"}
        self.assertEqual(
            self.client.get("/reports/predictions", headers=headers).status_code, 200
        )
        self.assertEqual(
            self.client.get("/reports/snapshots", headers=headers).status_code, 200
        )

    def test_admin_still_gated(self):
        self.assertEqual(self.client.post("/reports/backfill").status_code, 401)
        resp = self.client.post("/reports/backfill", headers={"X-API-Key": "secret123"})
        self.assertEqual(resp.status_code, 200)


class TestBearerCaseInsensitive(unittest.TestCase):
    def test_lowercase_bearer(self):
        client = TestClient(_make_app(api_key="key1"))
        resp = client.post(
            "/reports/backfill", headers={"Authorization": "bearer key1"}
        )
        self.assertEqual(resp.status_code, 200)

    def test_mixed_case_bearer(self):
        client = TestClient(_make_app(api_key="key1"))
        resp = client.post(
            "/reports/backfill", headers={"Authorization": "Bearer key1"}
        )
        self.assertEqual(resp.status_code, 200)


class TestPublicPrefixesIncludeModelsSubpaths(unittest.TestCase):
    """Ensure /reports/models/global and /reports/models/params are public."""

    def setUp(self):
        app = FastAPI()
        app.add_middleware(APIKeyMiddleware, api_key="secret", read_auth=True)

        @app.get("/reports/models/global")
        def models_global():
            return []

        @app.get("/reports/models/params")
        def models_params():
            return []

        self.client = TestClient(app)

    def test_models_global_public(self):
        self.assertEqual(self.client.get("/reports/models/global").status_code, 200)

    def test_models_params_public(self):
        self.assertEqual(self.client.get("/reports/models/params").status_code, 200)


if __name__ == "__main__":
    unittest.main()

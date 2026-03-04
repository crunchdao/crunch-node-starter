"""Tests for API router auto-discovery."""

from __future__ import annotations

import os
import tempfile
import textwrap
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from crunch_node.api_discovery import (
    _mount_from_directory,
    _mount_from_path,
    mount_api_routers,
)


class TestMountFromDirectory(unittest.TestCase):
    def test_discovers_router_from_py_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api_dir = Path(tmpdir) / "api"
            api_dir.mkdir()
            (api_dir / "__init__.py").write_text("")
            (api_dir / "greet.py").write_text(
                textwrap.dedent("""
                from fastapi import APIRouter
                router = APIRouter(prefix="/greet")

                @router.get("/hello")
                def hello():
                    return {"msg": "hi"}
            """)
            )

            app = FastAPI()
            count = _mount_from_directory(app, str(api_dir))

            self.assertEqual(count, 1)

            client = TestClient(app)
            resp = client.get("/greet/hello")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"msg": "hi"})

    def test_skips_underscore_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api_dir = Path(tmpdir) / "api"
            api_dir.mkdir()
            (api_dir / "__init__.py").write_text("")
            (api_dir / "_internal.py").write_text(
                textwrap.dedent("""
                from fastapi import APIRouter
                router = APIRouter()

                @router.get("/secret")
                def secret():
                    return {"hidden": True}
            """)
            )

            app = FastAPI()
            count = _mount_from_directory(app, str(api_dir))
            self.assertEqual(count, 0)

    def test_skips_files_without_router(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api_dir = Path(tmpdir) / "api"
            api_dir.mkdir()
            (api_dir / "__init__.py").write_text("")
            (api_dir / "utils.py").write_text("HELPER = 42\n")

            app = FastAPI()
            count = _mount_from_directory(app, str(api_dir))
            self.assertEqual(count, 0)

    def test_nonexistent_directory_returns_zero(self):
        app = FastAPI()
        count = _mount_from_directory(app, "/nonexistent/path")
        self.assertEqual(count, 0)

    def test_multiple_files_alphabetical_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api_dir = Path(tmpdir) / "api"
            api_dir.mkdir()
            (api_dir / "__init__.py").write_text("")

            for name in ["beta", "alpha"]:
                (api_dir / f"{name}.py").write_text(
                    textwrap.dedent(f"""
                    from fastapi import APIRouter
                    router = APIRouter(prefix="/{name}")

                    @router.get("/ping")
                    def ping():
                        return {{"source": "{name}"}}
                """)
                )

            app = FastAPI()
            count = _mount_from_directory(app, str(api_dir))
            self.assertEqual(count, 2)

            client = TestClient(app)
            self.assertEqual(client.get("/alpha/ping").json(), {"source": "alpha"})
            self.assertEqual(client.get("/beta/ping").json(), {"source": "beta"})

    def test_bad_import_skipped_gracefully(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api_dir = Path(tmpdir) / "api"
            api_dir.mkdir()
            (api_dir / "__init__.py").write_text("")
            (api_dir / "broken.py").write_text("raise RuntimeError('boom')\n")

            app = FastAPI()
            count = _mount_from_directory(app, str(api_dir))
            self.assertEqual(count, 0)


class TestMountFromPath(unittest.TestCase):
    def test_mount_explicit_path(self):
        # Use a known module that has a router-like attribute
        # We'll create a temp module
        import sys
        import types

        mod = types.ModuleType("_test_api_mod")
        from fastapi import APIRouter

        mod.router = APIRouter(prefix="/explicit")

        @mod.router.get("/test")
        def test_endpoint():
            return {"explicit": True}

        sys.modules["_test_api_mod"] = mod

        try:
            app = FastAPI()
            count = _mount_from_path(app, "_test_api_mod:router")
            self.assertEqual(count, 1)

            client = TestClient(app)
            resp = client.get("/explicit/test")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"explicit": True})
        finally:
            del sys.modules["_test_api_mod"]

    def test_bad_path_returns_zero(self):
        app = FastAPI()
        count = _mount_from_path(app, "nonexistent_module:router")
        self.assertEqual(count, 0)

    def test_non_router_attribute_returns_zero(self):
        import sys
        import types

        mod = types.ModuleType("_test_non_router")
        mod.router = "not a router"
        sys.modules["_test_non_router"] = mod

        try:
            app = FastAPI()
            count = _mount_from_path(app, "_test_non_router:router")
            self.assertEqual(count, 0)
        finally:
            del sys.modules["_test_non_router"]


class TestMountApiRouters(unittest.TestCase):
    def test_env_var_api_routes(self):
        import sys
        import types

        from fastapi import APIRouter

        mod = types.ModuleType("_test_env_routes")
        mod.router = APIRouter(prefix="/env")

        @mod.router.get("/check")
        def check():
            return {"via": "env"}

        sys.modules["_test_env_routes"] = mod

        old_val = os.environ.get("API_ROUTES", "")
        os.environ["API_ROUTES"] = "_test_env_routes:router"

        try:
            app = FastAPI()
            count = mount_api_routers(app)
            self.assertGreaterEqual(count, 1)

            client = TestClient(app)
            resp = client.get("/env/check")
            self.assertEqual(resp.status_code, 200)
        finally:
            if old_val:
                os.environ["API_ROUTES"] = old_val
            else:
                os.environ.pop("API_ROUTES", None)
            del sys.modules["_test_env_routes"]

    def test_no_config_returns_zero(self):
        old_dir = os.environ.get("API_ROUTES_DIR", "")
        old_routes = os.environ.get("API_ROUTES", "")

        os.environ["API_ROUTES_DIR"] = "/nonexistent"
        os.environ.pop("API_ROUTES", None)

        try:
            app = FastAPI()
            count = mount_api_routers(app)
            self.assertEqual(count, 0)
        finally:
            if old_dir:
                os.environ["API_ROUTES_DIR"] = old_dir
            else:
                os.environ.pop("API_ROUTES_DIR", None)
            if old_routes:
                os.environ["API_ROUTES"] = old_routes


if __name__ == "__main__":
    unittest.main()

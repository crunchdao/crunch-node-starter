"""API key authentication middleware for the report worker.

Endpoints are classified into three tiers:

- **Public** — no auth required (leaderboard, schema, healthz, models list)
- **Read** — requires API key when `API_KEY` is set (predictions, snapshots, data)
- **Admin** — always requires API key (backfill triggers, checkpoint mutations, custom api/)

Configuration via environment variables:

- `API_KEY` — the shared secret. When unset, all endpoints are open (backward compat).
- `API_PUBLIC_PREFIXES` — comma-separated path prefixes that never require auth.
  Default: `/healthz,/reports/schema,/reports/leaderboard,/reports/models,/reports/feeds,/info,/docs,/openapi.json`
- `API_ADMIN_PREFIXES` — comma-separated path prefixes that always require auth.
  Default: `/reports/backfill,/reports/checkpoints/,/custom`
- `API_READ_AUTH` — if `true`, read endpoints (everything not public/admin) also require
  the API key. Default: `false` (read endpoints are open when API_KEY is set, only admin is gated).

The key can be sent as:
- `X-API-Key: <key>` header
- `Authorization: Bearer <key>` header
- `?api_key=<key>` query parameter
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

_DEFAULT_PUBLIC_PREFIXES = (
    "/healthz",
    "/reports/schema",
    "/reports/leaderboard",
    "/reports/models",
    "/reports/feeds",
    "/reports/diversity",
    "/reports/ensemble",
    "/reports/checkpoints/rewards",
    "/info",
    "/docs",
    "/redoc",
    "/openapi.json",
)
# Note: /reports/models includes /reports/models/{id}/diversity — competitors
# can always see their own diversity feedback.

_DEFAULT_ADMIN_PREFIXES = (
    "/reports/backfill",
    "/reports/checkpoints/",
    "/custom",
)


def _parse_prefixes(env_var: str, defaults: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return defaults
    return tuple(p.strip() for p in raw.split(",") if p.strip())


class APIKeyMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that gates endpoints by API key.

    Inactive when `api_key` is None (no API_KEY env var set).
    """

    def __init__(
        self,
        app,
        api_key: str | None = None,
        public_prefixes: tuple[str, ...] | None = None,
        admin_prefixes: tuple[str, ...] | None = None,
        read_auth: bool = False,
    ):
        super().__init__(app)
        self.api_key = api_key
        self.public_prefixes = public_prefixes or _parse_prefixes(
            "API_PUBLIC_PREFIXES", _DEFAULT_PUBLIC_PREFIXES
        )
        self.admin_prefixes = admin_prefixes or _parse_prefixes(
            "API_ADMIN_PREFIXES", _DEFAULT_ADMIN_PREFIXES
        )
        self.read_auth = read_auth

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # No API key configured → everything open (backward compat)
        if not self.api_key:
            return await call_next(request)

        path = request.url.path

        # Public endpoints — always open
        if self._is_public(path):
            return await call_next(request)

        # Admin endpoints — always require key
        if self._is_admin(path):
            if not self._check_key(request):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "API key required"},
                )
            return await call_next(request)

        # Read endpoints — require key only if read_auth is on
        if self.read_auth:
            if not self._check_key(request):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "API key required"},
                )

        return await call_next(request)

    def _is_public(self, path: str) -> bool:
        return any(path.startswith(p) for p in self.public_prefixes)

    def _is_admin(self, path: str) -> bool:
        return any(path.startswith(p) for p in self.admin_prefixes)

    def _check_key(self, request: Request) -> bool:
        """Extract API key from request and validate."""
        # X-API-Key header
        key = request.headers.get("x-api-key")
        if key:
            return key == self.api_key

        # Authorization: Bearer <key>
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip() == self.api_key

        # Query parameter fallback
        key = request.query_params.get("api_key")
        if key:
            return key == self.api_key

        return False


def configure_auth(app) -> None:
    """Read env vars and add API key middleware to a FastAPI app.

    Call this during app startup. Does nothing if API_KEY is not set.
    """
    api_key = os.getenv("API_KEY", "").strip() or None
    read_auth = os.getenv("API_READ_AUTH", "false").lower() in ("true", "1", "yes")

    if api_key:
        app.add_middleware(
            APIKeyMiddleware,
            api_key=api_key,
            read_auth=read_auth,
        )
        logger.info(
            "API key auth enabled (read_auth=%s, %d public prefixes, %d admin prefixes)",
            read_auth,
            len(_parse_prefixes("API_PUBLIC_PREFIXES", _DEFAULT_PUBLIC_PREFIXES)),
            len(_parse_prefixes("API_ADMIN_PREFIXES", _DEFAULT_ADMIN_PREFIXES)),
        )
    else:
        logger.info("API key auth disabled (API_KEY not set)")

"""PostgreSQL LISTEN/NOTIFY helpers for cross-worker event signaling.

Usage:
    # publish
    from crunch_node.db.pg_notify import notify
    notify("new_feed_data")
    notify("score_complete", payload='{"model_id": "1"}')

    # subscribe (async)
    from crunch_node.db.pg_notify import wait_for_notify, listen
    notified = await wait_for_notify("new_feed_data", timeout=30.0)

    # subscribe to multiple channels
    async for channel, payload in listen("new_feed_data", "score_complete"):
        print(f"got {channel}: {payload}")
"""

from __future__ import annotations

import asyncio
import logging
import select as _select
from collections.abc import AsyncIterator
from typing import Any

import psycopg2

from crunch_node.db.session import database_url

logger = logging.getLogger(__name__)

# Default channel for backward compat
DEFAULT_CHANNEL = "new_feed_data"


def notify(
    channel: str = DEFAULT_CHANNEL, payload: str = "", connection: Any = None
) -> None:
    """Send a NOTIFY on the given channel with an optional payload string."""
    own_conn = connection is None
    if own_conn:
        connection = _raw_connection()
    try:
        connection.autocommit = True
        with connection.cursor() as cur:
            if payload:
                cur.execute("SELECT pg_notify(%s, %s)", (channel, payload))
            else:
                cur.execute(f"NOTIFY {channel}")
    finally:
        if own_conn:
            connection.close()


async def wait_for_notify(
    channel: str = DEFAULT_CHANNEL, timeout: float = 30.0
) -> bool:
    """Block (async) until a NOTIFY arrives on the channel or timeout.

    Returns True if notified, False on timeout.
    """
    conn = _raw_connection()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f"LISTEN {channel}")

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _poll_notify, conn, timeout)
    finally:
        conn.close()


async def listen(
    *channels: str, timeout: float | None = None
) -> AsyncIterator[tuple[str, str]]:
    """Async generator that yields (channel, payload) tuples as notifications arrive.

    Subscribes to one or more channels. Runs until cancelled.
    Optional timeout (seconds) per poll cycle — None means block indefinitely.

    Usage:
        async for channel, payload in listen("new_feed_data", "score_complete"):
            handle(channel, payload)
    """
    if not channels:
        channels = (DEFAULT_CHANNEL,)

    conn = _raw_connection()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for ch in channels:
                cur.execute(f"LISTEN {ch}")

        loop = asyncio.get_event_loop()
        while True:
            notified = await loop.run_in_executor(
                None,
                _poll_notify,
                conn,
                timeout if timeout is not None else 30.0,
            )
            if notified:
                while conn.notifies:
                    n = conn.notifies.pop(0)
                    yield (n.channel, n.payload or "")
    finally:
        conn.close()


def _poll_notify(conn: Any, timeout: float) -> bool:
    """Synchronous poll — runs in executor thread."""
    if _select.select([conn], [], [], timeout) == ([], [], []):
        return False  # timeout
    conn.poll()
    return bool(conn.notifies)


def _raw_connection():
    """Create a raw psycopg2 connection from the same DB URL."""
    url = database_url()
    dsn = url.replace("+psycopg2", "")
    return psycopg2.connect(dsn)

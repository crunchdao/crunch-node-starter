"""Tests for crunch_node.db.pg_notify — PostgreSQL LISTEN/NOTIFY helpers.

All tests mock the psycopg2 connection so no real Postgres is needed.
"""

from __future__ import annotations

import asyncio
import unittest
from collections import namedtuple
from unittest.mock import MagicMock, patch

from crunch_node.db.pg_notify import (
    DEFAULT_CHANNEL,
    _poll_notify,
    listen,
    notify,
    wait_for_notify,
)

# ── Helpers ──────────────────────────────────────────────────────────────

Notification = namedtuple("Notification", ["channel", "payload"])


def _make_mock_connection(notifies: list | None = None):
    """Build a mock psycopg2 connection with cursor context manager."""
    conn = MagicMock()
    conn.notifies = notifies if notifies is not None else []
    cursor = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cursor


# ── notify() ─────────────────────────────────────────────────────────────


class TestNotify(unittest.TestCase):
    def test_notify_default_channel_no_payload(self):
        conn, cursor = _make_mock_connection()
        notify(connection=conn)
        cursor.execute.assert_called_once_with(f"NOTIFY {DEFAULT_CHANNEL}")
        conn.close.assert_not_called()  # caller owns connection

    def test_notify_custom_channel_no_payload(self):
        conn, cursor = _make_mock_connection()
        notify("score_complete", connection=conn)
        cursor.execute.assert_called_once_with("NOTIFY score_complete")

    def test_notify_with_payload(self):
        conn, cursor = _make_mock_connection()
        notify("score_complete", payload='{"model": "1"}', connection=conn)
        cursor.execute.assert_called_once_with(
            "SELECT pg_notify(%s, %s)", ("score_complete", '{"model": "1"}')
        )

    def test_notify_sets_autocommit(self):
        conn, _ = _make_mock_connection()
        notify(connection=conn)
        self.assertTrue(conn.autocommit)

    @patch("crunch_node.db.pg_notify._raw_connection")
    def test_notify_creates_and_closes_own_connection(self, mock_raw):
        conn, cursor = _make_mock_connection()
        mock_raw.return_value = conn
        notify("test_channel")
        mock_raw.assert_called_once()
        conn.close.assert_called_once()

    @patch("crunch_node.db.pg_notify._raw_connection")
    def test_notify_closes_connection_on_error(self, mock_raw):
        conn, cursor = _make_mock_connection()
        cursor.execute.side_effect = RuntimeError("db error")
        mock_raw.return_value = conn
        with self.assertRaises(RuntimeError):
            notify("test_channel")
        conn.close.assert_called_once()

    def test_notify_does_not_close_provided_connection(self):
        conn, _ = _make_mock_connection()
        notify("ch1", connection=conn)
        conn.close.assert_not_called()

    def test_notify_empty_payload_uses_notify_syntax(self):
        """Empty string payload should use NOTIFY (no pg_notify function)."""
        conn, cursor = _make_mock_connection()
        notify("ch1", payload="", connection=conn)
        cursor.execute.assert_called_once_with("NOTIFY ch1")


# ── _poll_notify() ───────────────────────────────────────────────────────


class TestPollNotify(unittest.TestCase):
    @patch("crunch_node.db.pg_notify._select.select")
    def test_returns_false_on_timeout(self, mock_select):
        mock_select.return_value = ([], [], [])
        conn = MagicMock()
        result = _poll_notify(conn, 5.0)
        self.assertFalse(result)
        conn.poll.assert_not_called()

    @patch("crunch_node.db.pg_notify._select.select")
    def test_returns_true_when_notified(self, mock_select):
        conn = MagicMock()
        conn.notifies = [Notification("ch", "")]
        mock_select.return_value = ([conn], [], [])
        result = _poll_notify(conn, 5.0)
        self.assertTrue(result)
        conn.poll.assert_called_once()

    @patch("crunch_node.db.pg_notify._select.select")
    def test_returns_false_when_poll_but_no_notifies(self, mock_select):
        conn = MagicMock()
        conn.notifies = []
        mock_select.return_value = ([conn], [], [])
        result = _poll_notify(conn, 5.0)
        self.assertFalse(result)


# ── wait_for_notify() ───────────────────────────────────────────────────


class TestWaitForNotify(unittest.TestCase):
    @patch("crunch_node.db.pg_notify._poll_notify")
    @patch("crunch_node.db.pg_notify._raw_connection")
    def test_returns_true_and_payload_when_notified(self, mock_raw, mock_poll):
        notif = Notification(channel="my_channel", payload='{"test": 123}')
        conn, cursor = _make_mock_connection(notifies=[notif])
        mock_raw.return_value = conn
        mock_poll.return_value = True

        notified, payload = asyncio.run(wait_for_notify("my_channel", timeout=1.0))
        self.assertTrue(notified)
        self.assertEqual(payload, '{"test": 123}')
        cursor.execute.assert_called_once_with("LISTEN my_channel")
        conn.close.assert_called_once()

    @patch("crunch_node.db.pg_notify._poll_notify")
    @patch("crunch_node.db.pg_notify._raw_connection")
    def test_returns_false_and_empty_payload_on_timeout(self, mock_raw, mock_poll):
        conn, cursor = _make_mock_connection()
        mock_raw.return_value = conn
        mock_poll.return_value = False

        notified, payload = asyncio.run(wait_for_notify("my_channel", timeout=0.1))
        self.assertFalse(notified)
        self.assertEqual(payload, "")
        conn.close.assert_called_once()

    @patch("crunch_node.db.pg_notify._poll_notify")
    @patch("crunch_node.db.pg_notify._raw_connection")
    def test_default_channel(self, mock_raw, mock_poll):
        conn, cursor = _make_mock_connection()
        mock_raw.return_value = conn
        mock_poll.return_value = False

        asyncio.run(wait_for_notify(timeout=0.1))
        cursor.execute.assert_called_once_with(f"LISTEN {DEFAULT_CHANNEL}")

    @patch("crunch_node.db.pg_notify._poll_notify")
    @patch("crunch_node.db.pg_notify._raw_connection")
    def test_closes_connection_on_error(self, mock_raw, mock_poll):
        conn, cursor = _make_mock_connection()
        mock_raw.return_value = conn
        mock_poll.side_effect = RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            asyncio.run(wait_for_notify("ch", timeout=0.1))
        conn.close.assert_called_once()


# ── listen() ─────────────────────────────────────────────────────────────


class TestListen(unittest.TestCase):
    @patch("crunch_node.db.pg_notify._poll_notify")
    @patch("crunch_node.db.pg_notify._raw_connection")
    def test_yields_channel_and_payload(self, mock_raw, mock_poll):
        conn, cursor = _make_mock_connection()
        mock_raw.return_value = conn

        notifications = [
            Notification("ch1", '{"x": 1}'),
            Notification("ch2", "hello"),
        ]

        call_count = 0

        def poll_side_effect(c, t):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                conn.notifies.extend(notifications)
                return True
            return False  # second call — no more

        mock_poll.side_effect = poll_side_effect

        async def collect():
            results = []
            async for channel, payload in listen("ch1", "ch2", timeout=0.01):
                results.append((channel, payload))
                if len(results) >= 2:
                    break
            return results

        results = asyncio.run(collect())
        self.assertEqual(results, [("ch1", '{"x": 1}'), ("ch2", "hello")])
        conn.close.assert_called_once()

    @patch("crunch_node.db.pg_notify._poll_notify")
    @patch("crunch_node.db.pg_notify._raw_connection")
    def test_listens_on_all_channels(self, mock_raw, mock_poll):
        conn, cursor = _make_mock_connection()
        mock_raw.return_value = conn
        mock_poll.return_value = False

        async def run():
            async for _ in listen("a", "b", "c", timeout=0.01):
                break  # won't yield since poll returns False

        # Run briefly — will loop once then we check LISTEN calls
        try:
            asyncio.run(asyncio.wait_for(run(), timeout=0.1))
        except (TimeoutError, StopAsyncIteration):
            pass

        listen_calls = [c for c in cursor.execute.call_args_list if "LISTEN" in str(c)]
        channels_listened = [str(c) for c in listen_calls]
        self.assertIn("LISTEN a", str(channels_listened))
        self.assertIn("LISTEN b", str(channels_listened))
        self.assertIn("LISTEN c", str(channels_listened))

    @patch("crunch_node.db.pg_notify._poll_notify")
    @patch("crunch_node.db.pg_notify._raw_connection")
    def test_defaults_to_default_channel(self, mock_raw, mock_poll):
        conn, cursor = _make_mock_connection()
        mock_raw.return_value = conn
        mock_poll.return_value = False

        async def run():
            async for _ in listen(timeout=0.01):
                break

        try:
            asyncio.run(asyncio.wait_for(run(), timeout=0.1))
        except (TimeoutError, StopAsyncIteration):
            pass

        cursor.execute.assert_any_call(f"LISTEN {DEFAULT_CHANNEL}")

    @patch("crunch_node.db.pg_notify._poll_notify")
    @patch("crunch_node.db.pg_notify._raw_connection")
    def test_empty_payload_yields_empty_string(self, mock_raw, mock_poll):
        conn, cursor = _make_mock_connection()
        mock_raw.return_value = conn

        call_count = 0

        def poll_side_effect(c, t):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                conn.notifies.append(Notification("ch1", None))
                return True
            return False

        mock_poll.side_effect = poll_side_effect

        async def collect():
            async for channel, payload in listen("ch1", timeout=0.01):
                return (channel, payload)

        result = asyncio.run(collect())
        self.assertEqual(result, ("ch1", ""))

    @patch("crunch_node.db.pg_notify._poll_notify")
    @patch("crunch_node.db.pg_notify._raw_connection")
    def test_closes_connection_on_break(self, mock_raw, mock_poll):
        conn, cursor = _make_mock_connection()
        mock_raw.return_value = conn

        call_count = 0

        def poll_side_effect(c, t):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                conn.notifies.append(Notification("ch", "data"))
                return True
            return False

        mock_poll.side_effect = poll_side_effect

        async def run():
            async for _ in listen("ch", timeout=0.01):
                break

        asyncio.run(run())
        conn.close.assert_called_once()

    @patch("crunch_node.db.pg_notify._poll_notify")
    @patch("crunch_node.db.pg_notify._raw_connection")
    def test_multiple_notifications_in_single_poll(self, mock_raw, mock_poll):
        """Multiple notifications queued in one poll cycle are all yielded."""
        conn, cursor = _make_mock_connection()
        mock_raw.return_value = conn

        call_count = 0

        def poll_side_effect(c, t):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                conn.notifies.extend(
                    [
                        Notification("a", "1"),
                        Notification("b", "2"),
                        Notification("a", "3"),
                    ]
                )
                return True
            return False

        mock_poll.side_effect = poll_side_effect

        async def collect():
            results = []
            async for channel, payload in listen("a", "b", timeout=0.01):
                results.append((channel, payload))
                if len(results) >= 3:
                    break
            return results

        results = asyncio.run(collect())
        self.assertEqual(results, [("a", "1"), ("b", "2"), ("a", "3")])


# ── _raw_connection() ───────────────────────────────────────────────────


class TestRawConnection(unittest.TestCase):
    @patch("crunch_node.db.pg_notify.database_url")
    @patch("crunch_node.db.pg_notify.psycopg2.connect")
    def test_strips_psycopg2_dialect(self, mock_connect, mock_url):
        mock_url.return_value = "postgresql+psycopg2://user:pass@host:5432/db"
        from crunch_node.db.pg_notify import _raw_connection

        _raw_connection()
        mock_connect.assert_called_once_with("postgresql://user:pass@host:5432/db")

    @patch("crunch_node.db.pg_notify.database_url")
    @patch("crunch_node.db.pg_notify.psycopg2.connect")
    def test_plain_url_unchanged(self, mock_connect, mock_url):
        mock_url.return_value = "postgresql://user:pass@host:5432/db"
        from crunch_node.db.pg_notify import _raw_connection

        _raw_connection()
        mock_connect.assert_called_once_with("postgresql://user:pass@host:5432/db")


if __name__ == "__main__":
    unittest.main()

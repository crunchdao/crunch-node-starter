"""Tests for CheckpointService.maybe_checkpoint interval logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from crunch_node.services.checkpoint import CheckpointService


@pytest.fixture
def mock_repos():
    return MagicMock(), MagicMock(), MagicMock()


@pytest.fixture
def service(mock_repos):
    snapshot_repo, checkpoint_repo, model_repo = mock_repos
    emission = MagicMock()
    return CheckpointService(
        snapshot_repository=snapshot_repo,
        checkpoint_repository=checkpoint_repo,
        model_repository=model_repo,
        emission=emission,
        interval_seconds=3600,
    )


class TestMaybeCheckpoint:
    def test_skips_when_interval_not_elapsed(self, service):
        now = datetime(2026, 3, 12, 12, 0, 0, tzinfo=UTC)
        latest = MagicMock()
        latest.period_end = now - timedelta(minutes=30)
        service.checkpoint_repository.get_latest.return_value = latest

        result = service.maybe_checkpoint(now)

        assert result is None
        assert service._last_checkpoint_at == latest.period_end

    def test_creates_checkpoint_when_interval_elapsed(self, service):
        now = datetime(2026, 3, 12, 12, 0, 0, tzinfo=UTC)
        latest = MagicMock()
        latest.period_end = now - timedelta(hours=2)
        service.checkpoint_repository.get_latest.return_value = latest

        checkpoint = MagicMock()
        with patch.object(service, "create_checkpoint", return_value=checkpoint):
            result = service.maybe_checkpoint(now)

        assert result is checkpoint
        assert service._last_checkpoint_at == now

    def test_first_call_no_existing_checkpoint(self, service):
        now = datetime(2026, 3, 12, 12, 0, 0, tzinfo=UTC)
        service.checkpoint_repository.get_latest.return_value = None

        checkpoint = MagicMock()
        with patch.object(service, "create_checkpoint", return_value=checkpoint):
            result = service.maybe_checkpoint(now)

        assert result is checkpoint
        assert service._last_checkpoint_at == now

    def test_does_not_update_last_checkpoint_when_create_returns_none(self, service):
        now = datetime(2026, 3, 12, 12, 0, 0, tzinfo=UTC)
        service.checkpoint_repository.get_latest.return_value = None

        with patch.object(service, "create_checkpoint", return_value=None):
            result = service.maybe_checkpoint(now)

        assert result is None
        assert service._last_checkpoint_at == datetime.min.replace(tzinfo=UTC)

    def test_catches_exception_from_create_checkpoint(self, service):
        now = datetime(2026, 3, 12, 12, 0, 0, tzinfo=UTC)
        service.checkpoint_repository.get_latest.return_value = None

        with patch.object(
            service, "create_checkpoint", side_effect=RuntimeError("boom")
        ):
            result = service.maybe_checkpoint(now)

        assert result is None

    def test_caches_last_checkpoint_across_calls(self, service):
        now = datetime(2026, 3, 12, 12, 0, 0, tzinfo=UTC)
        latest = MagicMock()
        latest.period_end = now - timedelta(minutes=30)
        service.checkpoint_repository.get_latest.return_value = latest

        service.maybe_checkpoint(now)
        service.maybe_checkpoint(now + timedelta(minutes=10))

        service.checkpoint_repository.get_latest.assert_called_once()

    def test_handles_naive_datetime_from_repository(self, service):
        now = datetime(2026, 3, 12, 12, 0, 0, tzinfo=UTC)
        latest = MagicMock()
        latest.period_end = datetime(2026, 3, 12, 10, 0, 0)  # naive
        service.checkpoint_repository.get_latest.return_value = latest

        checkpoint = MagicMock()
        with patch.object(service, "create_checkpoint", return_value=checkpoint):
            result = service.maybe_checkpoint(now)

        assert result is checkpoint


class TestEnsureUtc:
    def test_naive_datetime_gets_utc(self):
        dt = datetime(2026, 1, 1, 12, 0, 0)
        result = CheckpointService._ensure_utc(dt)
        assert result.tzinfo is UTC

    def test_aware_datetime_unchanged(self):
        dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = CheckpointService._ensure_utc(dt)
        assert result is dt

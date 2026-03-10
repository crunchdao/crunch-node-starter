"""
Tests for the timing metrics HTTP endpoint.

These tests require a running database with tables initialized.
Skip in CI where only unit tests should run.
"""

import os
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from crunch_node.metrics.timing import timing_collector


def _can_connect_to_db():
    """Check if the test postgres is reachable."""
    try:
        import psycopg2

        psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "postgres"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            user=os.getenv("POSTGRES_USER", "test"),
            password=os.getenv("POSTGRES_PASSWORD", "test"),
            dbname=os.getenv("POSTGRES_DB", "test"),
            connect_timeout=1,
        ).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    os.getenv("CI") == "true" or not _can_connect_to_db(),
    reason="Integration test requires running postgres",
)


def test_timing_endpoint_enabled():
    """Test timing endpoint when enabled."""
    # Mock the CONTRACT to have timing enabled
    with patch("crunch_node.workers.report_worker.CONTRACT") as mock_contract:
        mock_contract.timing_endpoint_enabled = True
        mock_contract.timing_metrics_enabled = True
        mock_contract.timing_buffer_size = 1000

        # Import and create test client after mocking
        from crunch_node.workers.report_worker import app

        client = TestClient(app)

        # Configure timing collector
        timing_collector.configure(enabled=True, buffer_size=1000)
        timing_collector.clear()

        # Add some test data
        timing_collector.record_timing(
            "test-prediction-1",
            {"feed_received_us": 1000, "persistence_completed_us": 2000},
        )

        # Test endpoint
        response = client.get("/timing-metrics")
        assert response.status_code == 200

        data = response.json()
        assert "enabled" in data
        assert "buffer_size" in data
        assert "total_records" in data
        assert "stage_latencies" in data
        assert "recent_samples" in data

        assert data["enabled"] is True
        assert data["total_records"] == 1


def test_timing_endpoint_disabled():
    """Test timing endpoint when disabled."""
    with patch("crunch_node.workers.report_worker.CONTRACT") as mock_contract:
        mock_contract.timing_endpoint_enabled = False

        from crunch_node.workers.report_worker import app

        client = TestClient(app)

        response = client.get("/timing-metrics")
        assert response.status_code == 404
        assert "disabled" in response.json()["detail"].lower()


def test_timing_endpoint_empty_buffer():
    """Test timing endpoint with empty buffer."""
    with patch("crunch_node.workers.report_worker.CONTRACT") as mock_contract:
        mock_contract.timing_endpoint_enabled = True
        mock_contract.timing_metrics_enabled = True

        from crunch_node.workers.report_worker import app

        client = TestClient(app)

        # Clear any existing data
        timing_collector.configure(enabled=True)
        timing_collector.clear()

        response = client.get("/timing-metrics")
        assert response.status_code == 200

        data = response.json()
        assert data["total_records"] == 0
        assert data["buffer_size"] == 0
        assert data["stage_latencies"] == []
        assert data["recent_samples"] == []

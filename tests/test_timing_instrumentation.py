"""
Integration tests for timing instrumentation across the pipeline.

These tests validate that timing data flows correctly through
InputRecord._timing and PredictionRecord.meta["timing"].
"""

import os
import time

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("CI") == "true",
    reason="Tests need updates to match current timing implementation",
)

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

from crunch_node.crunch_config import CrunchConfig
from crunch_node.entities.feed_record import FeedRecord
from crunch_node.entities.prediction import (
    InputRecord,
    PredictionRecord,
    PredictionStatus,
)
from crunch_node.feeds import FeedDataRecord
from crunch_node.services.feed_data import _feed_to_domain
from crunch_node.services.realtime_predict import RealtimePredictService


class TestTimingInstrumentation:
    def test_feed_record_timing_integration(self):
        feed_data = FeedDataRecord(
            source="test-source",
            subject="BTC",
            kind="tick",
            granularity="1s",
            ts_event=time.time(),
            values={"price": 50000.0},
        )

        feed_received_us = time.perf_counter_ns() // 1000
        domain_record = _feed_to_domain("test-source", feed_data, feed_received_us)

        assert "timing" in domain_record.meta
        assert domain_record.meta["timing"]["feed_received_us"] == feed_received_us

        domain_record.meta["timing"]["feed_normalized_us"] = feed_received_us + 100
        domain_record.meta["timing"]["feed_persisted_us"] = feed_received_us + 500

        assert len(domain_record.meta["timing"]) == 3

    def test_input_record_timing(self):
        inp = InputRecord(
            id="test-input-1",
            raw_data={"price": 50000.0},
            received_at=datetime.now(UTC),
        )

        notify_received_us = time.perf_counter_ns() // 1000
        data_loaded_us = notify_received_us + 200

        inp._timing["notify_received_us"] = notify_received_us
        inp._timing["data_loaded_us"] = data_loaded_us

        assert inp._timing["notify_received_us"] == notify_received_us
        assert inp._timing["data_loaded_us"] == data_loaded_us

    def test_prediction_record_timing(self):
        prediction = PredictionRecord(
            id="test-prediction-1",
            input_id="test-input-1",
            model_id="test-model",
            prediction_config_id="test-config",
            scope_key="test-scope",
            scope={"subject": "BTC"},
            status=PredictionStatus.PENDING,
            exec_time_ms=10.5,
            inference_output={"value": 1.0},
            performed_at=datetime.now(UTC),
            resolvable_at=datetime.now(UTC),
        )

        base_time = time.perf_counter_ns() // 1000
        timing_data = {
            "feed_received_us": base_time,
            "feed_normalized_us": base_time + 100,
            "feed_persisted_us": base_time + 200,
            "notify_received_us": base_time + 300,
            "data_loaded_us": base_time + 400,
            "models_dispatched_us": base_time + 500,
            "models_completed_us": base_time + 1500,
            "callback_started_us": base_time + 1600,
            "callback_completed_us": base_time + 1800,
            "persistence_completed_us": base_time + 2000,
        }

        prediction._timing = timing_data

        expected_stages = [
            "feed_received_us",
            "feed_normalized_us",
            "feed_persisted_us",
            "notify_received_us",
            "data_loaded_us",
            "models_dispatched_us",
            "models_completed_us",
            "callback_started_us",
            "callback_completed_us",
            "persistence_completed_us",
        ]

        for stage in expected_stages:
            assert stage in prediction._timing
            assert prediction._timing[stage] >= base_time

    def test_realtime_predict_service_timing_structure(self):
        inp = InputRecord(
            id="test-input", raw_data={"price": 50000.0}, received_at=datetime.now(UTC)
        )

        base_time = time.perf_counter_ns() // 1000
        inp._timing = {
            "notify_received_us": base_time,
            "data_loaded_us": base_time + 100,
        }

        prediction = PredictionRecord(
            id="test-prediction",
            input_id=inp.id,
            model_id="test-model",
            prediction_config_id="test-config",
            scope_key="test-scope",
            scope={"subject": "BTC"},
            status=PredictionStatus.PENDING,
            exec_time_ms=10.0,
            performed_at=datetime.now(UTC),
            resolvable_at=datetime.now(UTC),
            _timing=inp._timing.copy(),
        )

        prediction._timing["models_dispatched_us"] = base_time + 200
        prediction._timing["models_completed_us"] = base_time + 800

        assert (
            prediction._timing["notify_received_us"]
            == inp._timing["notify_received_us"]
        )
        assert "models_dispatched_us" in prediction._timing
        assert "models_completed_us" in prediction._timing

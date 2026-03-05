from __future__ import annotations

from datetime import UTC, datetime

from crunch_node.entities.prediction import PredictionStatus
from crunch_node.services.predict_components import PredictionRecordFactory


def test_factory_builds_prediction_record_with_expected_id_and_scope():
    factory = PredictionRecordFactory()
    now = datetime(2026, 3, 5, 12, 0, 0, 123000, tzinfo=UTC)

    record = factory.build(
        model_id="model-1",
        input_id="INP_1",
        scope_key="btc/usdt:1m",
        scope={"scope_key": "btc/usdt:1m", "subject": "BTCUSDT", "kind": "tick"},
        status=PredictionStatus.PENDING,
        output={"value": 0.42},
        now=now,
        resolvable_at=now,
        exec_time_ms=12.3,
        config_id="CFG_001",
        timing_data={"models_completed_us": 123456},
    )

    assert record.id == "PRE_model-1_btc_usdt_1m_20260305_120000.123"
    assert record.scope == {"subject": "BTCUSDT", "kind": "tick"}
    assert record.meta == {"timing": {"models_completed_us": 123456}}
    assert record.status == PredictionStatus.PENDING
    assert record.prediction_config_id == "CFG_001"


def test_factory_uses_abs_prefix_for_absent_status():
    factory = PredictionRecordFactory()
    now = datetime(2026, 3, 5, 12, 0, 0, tzinfo=UTC)

    record = factory.build(
        model_id="model-1",
        input_id="INP_1",
        scope_key="scope-1",
        scope={"scope_key": "scope-1"},
        status=PredictionStatus.ABSENT,
        output={},
        now=now,
        resolvable_at=None,
    )

    assert record.id.startswith("ABS_model-1_scope-1_")
    assert record.resolvable_at is None

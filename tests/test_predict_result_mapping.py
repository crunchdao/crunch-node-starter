from __future__ import annotations

from pydantic import BaseModel, Field

from crunch_node.crunch_config import CrunchConfig
from crunch_node.entities.prediction import PredictionStatus
from crunch_node.services.predict import PredictService


class _Result:
    def __init__(self, status="SUCCESS", result=None):
        self.status = status
        self.result = {} if result is None else result


def test_map_runner_result_success_sets_pending():
    service = PredictService(contract=CrunchConfig(), runner=object())

    status, output = service._map_runner_result(
        _Result(status="SUCCESS", result={"value": 1})
    )

    assert status == PredictionStatus.PENDING
    assert output["value"] == 1.0


def test_map_runner_result_validation_error_sets_failed_with_payload():
    class StrictOutput(BaseModel):
        value: float = Field(ge=0.0, le=1.0)

    service = PredictService(
        contract=CrunchConfig(output_type=StrictOutput), runner=object()
    )

    status, output = service._map_runner_result(
        _Result(status="SUCCESS", result={"value": "bad"})
    )

    assert status == PredictionStatus.FAILED
    assert "_validation_error" in output
    assert "raw_output" in output


def test_map_runner_result_unknown_status_sets_failed():
    service = PredictService(contract=CrunchConfig(), runner=object())

    status, output = service._map_runner_result(
        _Result(status="BOGUS", result={"value": 0.1})
    )

    assert status == PredictionStatus.FAILED
    assert output["value"] == 0.1

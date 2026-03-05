from __future__ import annotations

import logging

from pydantic import BaseModel

from crunch_node.entities.model import Model
from crunch_node.services.predict_components import ModelRegistry, OutputValidator


class _Repo:
    def __init__(self):
        self.saved: list[Model] = []

    def save(self, model: Model) -> None:
        self.saved.append(model)


class _FailingRepo:
    def save(self, model: Model) -> None:
        raise RuntimeError("db down")


def _model(model_id: str = "m1") -> Model:
    return Model(
        id=model_id,
        name=f"model-{model_id}",
        player_id="p1",
        player_name="alice",
        deployment_identifier="dep-1",
    )


def test_model_registry_register_tracks_model_without_immediate_persist():
    known: dict[str, Model] = {}
    repo = _Repo()
    registry = ModelRegistry(known_models=known, model_repository=repo)

    model = _model("m1")
    registry.register(model)

    assert "m1" in known
    assert repo.saved == []


def test_model_registry_repo_failure_does_not_break_flow(caplog):
    known: dict[str, Model] = {}
    registry = ModelRegistry(
        known_models=known,
        model_repository=_FailingRepo(),
        logger=logging.getLogger("test-model-registry"),
    )

    model = _model("m1")

    with caplog.at_level(logging.WARNING):
        registry.register(model)
        registry.flush_non_critical()

    assert "m1" in known
    assert any(
        "non-critical model persistence failed" in r.message for r in caplog.records
    )


def test_model_registry_skips_persistence_for_semantically_unchanged_model():
    known: dict[str, Model] = {}
    repo = _Repo()
    registry = ModelRegistry(known_models=known, model_repository=repo)

    first = _model("m1")
    second = _model("m1")  # different timestamps, same identity metadata

    registry.register(first)
    registry.flush_non_critical()
    registry.register(second)
    registry.flush_non_critical()

    assert repo.saved == [first]


def test_model_registry_flush_persists_new_models():
    known: dict[str, Model] = {}
    repo = _Repo()
    registry = ModelRegistry(known_models=known, model_repository=repo)

    model = _model("m1")
    registry.register(model)
    registry.flush_non_critical()

    assert repo.saved == [model]


class _Output(BaseModel):
    value: float


def test_output_validator_accepts_and_normalizes_output():
    validator = OutputValidator(output_type=_Output, logger=logging.getLogger("test"))
    output = {"value": "1.5"}

    error = validator.validate_and_normalize(output)

    assert error is None
    assert output["value"] == 1.5


def test_output_validator_rejects_no_matching_keys():
    validator = OutputValidator(output_type=_Output, logger=logging.getLogger("test"))
    output = {"prediction": 1.0}

    error = validator.validate_and_normalize(output)

    assert error is not None


def test_output_validator_rejects_wrong_type():
    class TypedOutput(BaseModel):
        value: float
        direction: str

    validator = OutputValidator(
        output_type=TypedOutput,
        logger=logging.getLogger("test"),
    )
    output = {"value": 1.0, "direction": ["bad"]}

    error = validator.validate_and_normalize(output)

    assert error is not None

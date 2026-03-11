from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestSimulatorSinkHook:
    def test_calls_build_simulator_sink_when_set(self):
        fake_sink = MagicMock()
        config = MagicMock()
        config.build_simulator_sink = MagicMock(return_value=fake_sink)
        config.feed_subject_mapping = {}
        session = MagicMock()

        config.build_simulator_sink(session=session, config=config)

        config.build_simulator_sink.assert_called_once_with(
            session=session, config=config
        )

    def test_no_sink_when_hook_is_none(self):
        config = MagicMock()
        config.build_simulator_sink = None
        config.feed_subject_mapping = {}

        simulator_sink = None
        if config.build_simulator_sink is not None:
            simulator_sink = config.build_simulator_sink(
                session=MagicMock(), config=config
            )

        assert simulator_sink is None

    def test_hook_return_value_used_as_sink(self):
        fake_sink = MagicMock()
        config = MagicMock()
        config.build_simulator_sink = MagicMock(return_value=fake_sink)
        session = MagicMock()

        result = config.build_simulator_sink(session=session, config=config)

        assert result is fake_sink

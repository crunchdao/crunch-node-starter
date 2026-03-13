from __future__ import annotations

from unittest.mock import MagicMock, patch

from crunch_node.services.prediction_scorer import PredictionScorer


class TestScoreWorkerTrading:
    @patch("crunch_node.workers.score_worker.load_config")
    @patch("crunch_node.workers.score_worker.create_session")
    @patch("crunch_node.workers.score_worker.FeedReader")
    @patch("crunch_node.workers.score_worker.resolve_callable")
    @patch("crunch_node.workers.score_worker.ExtensionSettings")
    @patch("crunch_node.workers.score_worker.RuntimeSettings")
    def test_build_snapshots_fn_wired_when_hook_present(
        self,
        mock_runtime,
        mock_ext,
        mock_resolve,
        mock_reader,
        mock_session,
        mock_config,
    ):
        custom_strategy = MagicMock()
        factory = MagicMock(return_value=custom_strategy)
        config = MagicMock()
        config.build_score_snapshots = factory
        config.scoring_function = None
        mock_config.return_value = config
        mock_session.return_value = MagicMock()
        mock_runtime.from_env.return_value = MagicMock(
            checkpoint_interval_seconds=300,
            score_interval_seconds=60,
        )
        mock_ext.from_env.return_value = MagicMock()
        mock_resolve.return_value = lambda p, g: MagicMock()

        from crunch_node.workers.score_worker import build_service

        service = build_service()

        factory.assert_called_once()
        assert service.scoring_strategy is custom_strategy

    @patch("crunch_node.workers.score_worker.load_config")
    @patch("crunch_node.workers.score_worker.create_session")
    @patch("crunch_node.workers.score_worker.FeedReader")
    @patch("crunch_node.workers.score_worker.resolve_callable")
    @patch("crunch_node.workers.score_worker.ExtensionSettings")
    @patch("crunch_node.workers.score_worker.RuntimeSettings")
    def test_no_build_snapshots_fn_when_hook_absent(
        self,
        mock_runtime,
        mock_ext,
        mock_resolve,
        mock_reader,
        mock_session,
        mock_config,
    ):
        config = MagicMock()
        config.build_score_snapshots = None
        config.scoring_function = None
        mock_config.return_value = config
        mock_session.return_value = MagicMock()
        mock_runtime.from_env.return_value = MagicMock(
            checkpoint_interval_seconds=300,
            score_interval_seconds=60,
        )
        mock_ext.from_env.return_value = MagicMock()
        mock_resolve.return_value = lambda p, g: MagicMock()

        from crunch_node.workers.score_worker import build_service

        service = build_service()

        assert isinstance(service.scoring_strategy, PredictionScorer)

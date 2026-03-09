from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestScoreWorkerTrading:
    @patch("crunch_node.workers.score_worker.load_config")
    @patch("crunch_node.workers.score_worker.create_session")
    @patch("crunch_node.workers.score_worker.FeedReader")
    @patch("crunch_node.workers.score_worker.resolve_callable")
    @patch("crunch_node.workers.score_worker.ExtensionSettings")
    @patch("crunch_node.workers.score_worker.RuntimeSettings")
    def test_trading_state_repo_wired_when_cost_model_present(
        self,
        mock_runtime,
        mock_ext,
        mock_resolve,
        mock_reader,
        mock_session,
        mock_config,
    ):
        from crunch_node.services.trading.costs import CostModel

        config = MagicMock()
        config.cost_model = CostModel()
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

        assert service.trading_state_repository is not None

    @patch("crunch_node.workers.score_worker.load_config")
    @patch("crunch_node.workers.score_worker.create_session")
    @patch("crunch_node.workers.score_worker.FeedReader")
    @patch("crunch_node.workers.score_worker.resolve_callable")
    @patch("crunch_node.workers.score_worker.ExtensionSettings")
    @patch("crunch_node.workers.score_worker.RuntimeSettings")
    def test_no_trading_state_repo_when_no_cost_model(
        self,
        mock_runtime,
        mock_ext,
        mock_resolve,
        mock_reader,
        mock_session,
        mock_config,
    ):
        config = MagicMock(
            spec=[
                "scoring_function",
                "performance",
            ]
        )
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

        assert service.trading_state_repository is None

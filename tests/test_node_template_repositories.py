import inspect
import unittest

from crunch_node.db.repositories import (
    DBInputRepository,
    DBLeaderboardRepository,
    DBModelRepository,
    DBPredictionRepository,
    DBScoreRepository,
)


class TestRepositoryAPIs(unittest.TestCase):
    def test_model_repository_has_required_methods(self):
        self.assertTrue(callable(getattr(DBModelRepository, "fetch_all", None)))
        self.assertTrue(callable(getattr(DBModelRepository, "save", None)))

    def test_input_repository_has_required_methods(self):
        self.assertTrue(callable(getattr(DBInputRepository, "save", None)))
        self.assertTrue(callable(getattr(DBInputRepository, "find", None)))

    def test_prediction_repository_has_required_methods(self):
        self.assertTrue(callable(getattr(DBPredictionRepository, "save", None)))
        self.assertTrue(callable(getattr(DBPredictionRepository, "save_all", None)))
        self.assertTrue(callable(getattr(DBPredictionRepository, "find", None)))

    def test_score_repository_has_required_methods(self):
        self.assertTrue(callable(getattr(DBScoreRepository, "save", None)))
        self.assertTrue(callable(getattr(DBScoreRepository, "find", None)))

    def test_prediction_repository_has_query_scores_method(self):
        self.assertTrue(callable(getattr(DBPredictionRepository, "query_scores", None)))

    def test_leaderboard_repository_has_required_methods(self):
        self.assertTrue(callable(getattr(DBLeaderboardRepository, "save", None)))
        self.assertTrue(callable(getattr(DBLeaderboardRepository, "get_latest", None)))

    def test_input_repository_save_is_insert_only(self):
        """InputRepository.save() is insert-only (dumb log table)."""
        source = inspect.getsource(DBInputRepository.save)
        self.assertIn("raw_data_jsonb", source)
        self.assertNotIn("scope_jsonb", source)
        self.assertNotIn("actuals_jsonb", source)

    def test_prediction_repository_save_updates_resolvable_at(self):
        """Regression: DBPredictionRepository.save() must update resolvable_at
        on existing records."""
        source = inspect.getsource(DBPredictionRepository.save)
        self.assertIn(
            "existing.resolvable_at",
            source,
            "save() must update resolvable_at on existing records",
        )

    def test_all_repository_save_methods_update_all_constructor_fields(self):
        """Regression: every save() method must update all fields it constructs,
        except the primary key (id). Catches field omission bugs like the
        scope_jsonb/resolvable_at issue."""
        import re

        from crunch_node.db.repositories import (
            DBCheckpointRepository,
            DBSnapshotRepository,
        )

        repos = [
            ("DBModelRepository", DBModelRepository),
            ("DBInputRepository", DBInputRepository),
            ("DBPredictionRepository", DBPredictionRepository),
            ("DBScoreRepository", DBScoreRepository),
            ("DBSnapshotRepository", DBSnapshotRepository),
            ("DBCheckpointRepository", DBCheckpointRepository),
        ]
        for name, cls in repos:
            source = inspect.getsource(cls.save)
            # Fields assigned via row.X in constructor
            row_fields = set(re.findall(r"(\w+)=row\.(\w+)", source))
            constructor_fields = {f[0] for f in row_fields if f[0] != "id"}
            # Fields updated via existing.X = row.X
            existing_fields = set(
                re.findall(r"existing\.(\w+)\s*=\s*row\.(\w+)", source)
            )
            update_fields = {f[0] for f in existing_fields}

            missing = constructor_fields - update_fields
            self.assertEqual(
                missing,
                set(),
                f"{name}.save() creates fields {sorted(missing)} "
                f"but doesn't update them on existing records",
            )


if __name__ == "__main__":
    unittest.main()

import unittest

from crunch_node.db.tables import (
    CheckpointRow,
    FeedIngestionStateRow,
    FeedRecordRow,
    InputRow,
    LeaderboardRow,
    ModelRow,
    PredictionConfigRow,
    PredictionRow,
    ScoreRow,
    SnapshotRow,
)


class TestCoordinatorCoreSchema(unittest.TestCase):
    def test_required_table_names(self):
        self.assertEqual(ModelRow.__tablename__, "models")
        self.assertEqual(InputRow.__tablename__, "inputs")
        self.assertEqual(PredictionRow.__tablename__, "predictions")
        self.assertEqual(ScoreRow.__tablename__, "scores")
        self.assertEqual(SnapshotRow.__tablename__, "snapshots")
        self.assertEqual(CheckpointRow.__tablename__, "checkpoints")
        self.assertEqual(LeaderboardRow.__tablename__, "leaderboards")
        self.assertEqual(
            PredictionConfigRow.__tablename__, "scheduled_prediction_configs"
        )
        self.assertEqual(FeedRecordRow.__tablename__, "feed_records")
        self.assertEqual(FeedIngestionStateRow.__tablename__, "feed_ingestion_state")

    def test_jsonb_extension_fields_exist(self):
        self.assertIn("overall_score_jsonb", ModelRow.model_fields)
        self.assertIn("scores_by_scope_jsonb", ModelRow.model_fields)
        self.assertIn("meta_jsonb", ModelRow.model_fields)

        self.assertIn("raw_data_jsonb", InputRow.model_fields)

        self.assertIn("inference_output_jsonb", PredictionRow.model_fields)
        self.assertIn("scope_jsonb", PredictionRow.model_fields)
        self.assertIn("input_id", PredictionRow.model_fields)

        self.assertIn("entries_jsonb", LeaderboardRow.model_fields)
        self.assertIn("meta_jsonb", LeaderboardRow.model_fields)

        self.assertIn("scope_template_jsonb", PredictionConfigRow.model_fields)
        self.assertIn("schedule_jsonb", PredictionConfigRow.model_fields)

        self.assertIn("values_jsonb", FeedRecordRow.model_fields)

    def test_prediction_protocol_columns_exist(self):
        required_prediction_fields = {
            "id",
            "input_id",
            "model_id",
            "prediction_config_id",
            "scope_key",
            "scope_jsonb",
            "status",
            "performed_at",
            "resolvable_at",
        }
        self.assertTrue(
            required_prediction_fields.issubset(PredictionRow.model_fields.keys())
        )

    def test_score_columns_exist(self):
        required = {"id", "prediction_id", "result_jsonb", "success", "scored_at"}
        self.assertTrue(required.issubset(ScoreRow.model_fields.keys()))


if __name__ == "__main__":
    unittest.main()

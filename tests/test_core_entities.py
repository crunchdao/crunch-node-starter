import unittest

from crunch_node.entities.model import Model
from crunch_node.entities.prediction import (
    InputRecord,
    PredictionRecord,
    PredictionStatus,
    ScoreRecord,
)


class TestCoreEntities(unittest.TestCase):
    def test_model_overall_score_is_plain_dict(self):
        model = Model(
            id="m1",
            name="alpha",
            player_id="p1",
            player_name="alice",
            deployment_identifier="d1",
            overall_score={
                "metrics": {"wealth": 1000.0, "hit_rate": 0.7},
                "ranking": {"key": "wealth", "direction": "desc", "value": 1000.0},
            },
            meta={"tier": "gold"},
        )
        self.assertEqual(model.overall_score["metrics"]["wealth"], 1000.0)
        self.assertEqual(model.meta["tier"], "gold")

    def test_input_record(self):
        record = InputRecord(id="inp1", raw_data={"symbol": "BTC", "price": 100.0})
        self.assertEqual(record.raw_data["symbol"], "BTC")

    def test_prediction_record_carries_scope_and_output(self):
        prediction = PredictionRecord(
            id="pre1",
            input_id="inp1",
            model_id="m1",
            prediction_config_id="CFG_001",
            scope_key="BTC-60-60",
            scope={"subject": "BTC", "horizon": 3600},
            status=PredictionStatus.PENDING,
            exec_time_ms=12.5,
            inference_output={"distribution": []},
        )
        self.assertEqual(prediction.scope_key, "BTC-60-60")
        self.assertEqual(prediction.scope["subject"], "BTC")
        self.assertIn("distribution", prediction.inference_output)

    def test_score_record(self):
        score = ScoreRecord(
            id="scr1",
            prediction_id="pre1",
            result={"value": 0.42, "pnl": 120.5},
            success=True,
        )
        self.assertEqual(score.result["value"], 0.42)
        self.assertEqual(score.result["pnl"], 120.5)
        self.assertTrue(score.success)


if __name__ == "__main__":
    unittest.main()

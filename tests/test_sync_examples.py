"""Tests for the model-orchestrator sync_examples script."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Import the script directly — it lives outside the Python package
SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scaffold/node/deployment/model-orchestrator-local/config"
)
sys.path.insert(0, str(SCRIPT_PATH))

import sync_examples  # noqa: E402


class TestRewriteImports(unittest.TestCase):
    def test_rewrites_package_cruncher_import(self):
        source = "from starter_challenge.cruncher import BaseClass"
        result = sync_examples.rewrite_imports(source, "starter_challenge")
        self.assertEqual(result, "from cruncher import BaseClass")

    def test_rewrites_package_direct_import(self):
        source = "from starter_challenge import BaseClass"
        result = sync_examples.rewrite_imports(source, "starter_challenge")
        self.assertEqual(result, "from cruncher import BaseClass")

    def test_preserves_unrelated_imports(self):
        source = "from math import sqrt\nimport os"
        result = sync_examples.rewrite_imports(source, "starter_challenge")
        self.assertEqual(result, source)

    def test_handles_multiline(self):
        source = (
            "from __future__ import annotations\n"
            "\n"
            "from starter_challenge.cruncher import BaseClass\n"
            "\n"
            "class MyModel(BaseClass):\n"
            "    pass\n"
        )
        result = sync_examples.rewrite_imports(source, "starter_challenge")
        self.assertIn("from cruncher import BaseClass", result)
        self.assertIn("from __future__ import annotations", result)
        self.assertNotIn("starter_challenge", result)

    def test_different_package_name(self):
        source = "from my_trading_challenge.cruncher import BaseClass"
        result = sync_examples.rewrite_imports(source, "my_trading_challenge")
        self.assertEqual(result, "from cruncher import BaseClass")


class TestSubmissionIdFromFilename(unittest.TestCase):
    def test_standard_example(self):
        self.assertEqual(
            sync_examples.submission_id_from_filename("mean_reversion_tracker.py"),
            "mean-reversion-tracker",
        )

    def test_simple_name(self):
        self.assertEqual(
            sync_examples.submission_id_from_filename("my_model.py"),
            "my-model",
        )


class TestFindExamples(unittest.TestCase):
    def test_excludes_init_and_private(self, tmp_path=None):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "__init__.py").write_text("")
            (d / "_private.py").write_text("")
            (d / "my_model.py").write_text("")
            (d / "other_model.py").write_text("")

            result = sync_examples.find_examples(d)
            names = [p.name for p in result]
            self.assertEqual(names, ["my_model.py", "other_model.py"])


class TestCreateSubmission(unittest.TestCase):
    def test_creates_standalone_submission(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            # Set up paths
            orig_submissions = sync_examples.SUBMISSIONS_DIR
            sync_examples.SUBMISSIONS_DIR = Path(tmpdir) / "submissions"

            try:
                # Create fake challenge files
                challenge = Path(tmpdir) / "challenge"
                pkg = challenge / "my_pkg"
                pkg.mkdir(parents=True)
                tracker = pkg / "cruncher.py"
                tracker.write_text("class BaseClass:\n    pass\n")

                example = pkg / "examples"
                example.mkdir()
                model_file = example / "cool_model.py"
                model_file.write_text(
                    "from my_pkg.cruncher import BaseClass\n\n"
                    "class CoolModel(BaseClass):\n"
                    "    def predict(self, subject, resolve_horizon_seconds, step_seconds):\n"
                    "        return {'value': 0.0}\n"
                )

                sub_id = sync_examples.create_submission(model_file, tracker, "my_pkg")

                self.assertEqual(sub_id, "cool-model")
                sub_dir = sync_examples.SUBMISSIONS_DIR / "cool-model"
                self.assertTrue((sub_dir / "main.py").exists())
                self.assertTrue((sub_dir / "cruncher.py").exists())
                self.assertTrue((sub_dir / "requirements.txt").exists())

                main_content = (sub_dir / "main.py").read_text()
                self.assertIn("from cruncher import BaseClass", main_content)
                self.assertNotIn("my_pkg", main_content)

                cruncher_content = (sub_dir / "cruncher.py").read_text()
                self.assertIn("class BaseClass", cruncher_content)
            finally:
                sync_examples.SUBMISSIONS_DIR = orig_submissions


class TestGenerateModelsYml(unittest.TestCase):
    def test_generates_valid_yaml(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_file = sync_examples.MODELS_FILE
            sync_examples.MODELS_FILE = Path(tmpdir) / "models.dev.yml"

            try:
                sync_examples.generate_models_yml(
                    ["mean-reversion-tracker", "trend-following-tracker"],
                )

                content = sync_examples.MODELS_FILE.read_text()
                self.assertIn("models:", content)
                self.assertIn("submission_id: mean-reversion-tracker", content)
                self.assertIn("submission_id: trend-following-tracker", content)
                self.assertIn('id: "1"', content)
                self.assertIn('id: "2"', content)
                self.assertIn("model_name: mean-reversion", content)
                self.assertIn("model_name: trend-following", content)
            finally:
                sync_examples.MODELS_FILE = orig_file

    def test_empty_submissions(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_file = sync_examples.MODELS_FILE
            sync_examples.MODELS_FILE = Path(tmpdir) / "models.dev.yml"

            try:
                sync_examples.generate_models_yml([])
                content = sync_examples.MODELS_FILE.read_text()
                self.assertIn("models:", content)
                # No model entries
                self.assertNotIn("submission_id:", content)
            finally:
                sync_examples.MODELS_FILE = orig_file


if __name__ == "__main__":
    unittest.main()

"""Tests for the scaffold CLI — workspace creation from templates."""

from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

import crunch_node.cli.scaffold as scaffold_module
from crunch_node.cli.scaffold import (
    _copy_tree,
    _find_templates_dir,
    _is_text_file,
    _to_snake_case,
    list_packs,
    scaffold_workspace,
)

# ── Unit tests ─────────────────────────────────────────────────────────


class TestToSnakeCase:
    def test_kebab_to_snake(self):
        assert _to_snake_case("my-btc-challenge") == "my_btc_challenge"

    def test_single_word(self):
        assert _to_snake_case("challenge") == "challenge"

    def test_already_snake(self):
        assert _to_snake_case("my_challenge") == "my_challenge"


class TestIsTextFile:
    def test_python_file(self):
        assert _is_text_file(Path("foo.py")) is True

    def test_toml_file(self):
        assert _is_text_file(Path("pyproject.toml")) is True

    def test_env_file(self):
        assert _is_text_file(Path(".env")) is True

    def test_local_env(self):
        assert _is_text_file(Path(".local.env")) is True

    def test_env_example(self):
        assert _is_text_file(Path(".env.example")) is True

    def test_makefile(self):
        assert _is_text_file(Path("Makefile")) is True

    def test_dockerfile(self):
        assert _is_text_file(Path("Dockerfile")) is True

    def test_gitignore(self):
        assert _is_text_file(Path(".gitignore")) is True

    def test_markdown(self):
        assert _is_text_file(Path("README.md")) is True

    def test_json(self):
        assert _is_text_file(Path("config.json")) is True

    def test_binary_db(self):
        assert _is_text_file(Path("data.db")) is False

    def test_binary_so(self):
        assert _is_text_file(Path("lib.so")) is False

    def test_binary_dylib(self):
        assert _is_text_file(Path("lib.dylib")) is False


class TestFindTemplatesDir:
    def test_finds_repo_root_in_development(self):
        """In a repo checkout, templates should be found at the repo root."""
        templates_dir = _find_templates_dir()
        assert (templates_dir / "scaffold").is_dir()
        assert (
            templates_dir / "scaffold" / "node" / "config" / "crunch_config.py"
        ).exists()


# ── Copy tree tests ────────────────────────────────────────────────────


class TestCopyTree:
    def test_copies_files_with_replacement(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "hello.txt").write_text("starter-challenge is great")
        (src / "code.py").write_text("from starter_challenge import foo")

        dst = tmp_path / "dst"
        replacements = {
            "starter-challenge": "my-comp",
            "starter_challenge": "my_comp",
        }
        _copy_tree(src, dst, replacements)

        assert (dst / "hello.txt").read_text() == "my-comp is great"
        assert (dst / "code.py").read_text() == "from my_comp import foo"

    def test_renames_directories(self, tmp_path):
        src = tmp_path / "src"
        pkg = src / "starter_challenge"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("# starter_challenge")

        dst = tmp_path / "dst"
        replacements = {
            "starter-challenge": "my-comp",
            "starter_challenge": "my_comp",
        }
        _copy_tree(src, dst, replacements)

        assert (dst / "my_comp").is_dir()
        assert (dst / "my_comp" / "__init__.py").exists()
        assert (dst / "my_comp" / "__init__.py").read_text() == "# my_comp"

    def test_skips_pycache(self, tmp_path):
        src = tmp_path / "src"
        cache = src / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "foo.cpython-312.pyc").write_bytes(b"\x00")
        (src / "real.py").write_text("ok")

        dst = tmp_path / "dst"
        _copy_tree(src, dst, {})

        assert not (dst / "__pycache__").exists()
        assert (dst / "real.py").exists()

    def test_skips_venv(self, tmp_path):
        src = tmp_path / "src"
        venv = src / ".venv"
        venv.mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("home = /usr/bin")
        (src / "real.py").write_text("ok")

        dst = tmp_path / "dst"
        _copy_tree(src, dst, {})

        assert not (dst / ".venv").exists()
        assert (dst / "real.py").exists()

    def test_copies_binary_files_unchanged(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        binary_content = bytes(range(256))
        (src / "data.db").write_bytes(binary_content)

        dst = tmp_path / "dst"
        _copy_tree(src, dst, {"starter": "replaced"})

        assert (dst / "data.db").read_bytes() == binary_content

    def test_preserves_executable_bit(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        script = src / "run.sh"
        script.write_text("#!/bin/bash\necho hello")
        script.chmod(0o755)

        dst = tmp_path / "dst"
        _copy_tree(src, dst, {})

        assert os.access(dst / "run.sh", os.X_OK)


# ── End-to-end scaffold tests ─────────────────────────────────────────


class TestScaffoldWorkspace:
    def test_creates_workspace_basic(self, tmp_path):
        ws = scaffold_workspace(
            "my-test-comp", output_dir=str(tmp_path), clone_webapp=False
        )

        assert ws == tmp_path / "my-test-comp"
        assert ws.is_dir()

        # Directory renamed
        assert (ws / "challenge" / "my_test_comp").is_dir()
        assert not (ws / "challenge" / "starter_challenge").exists()

        # File contents replaced
        node_env = ws / "node" / ".env"
        if node_env.exists():
            content = node_env.read_text()
            assert "my-test-comp" in content
            assert "starter-challenge" not in content

    def test_clones_webapp_and_uses_local_build_context(self, tmp_path, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kwargs):
            calls.append(cmd)
            (tmp_path / "my-webapp-comp" / "webapp").mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(
            scaffold_module,
            "subprocess",
            types.SimpleNamespace(run=fake_run),
            raising=False,
        )

        ws = scaffold_workspace("my-webapp-comp", output_dir=str(tmp_path))

        assert (ws / "webapp").is_dir()
        assert any(
            cmd[:2] == ["git", "clone"]
            and "https://github.com/crunchdao/coordinator-webapp.git" in cmd
            for cmd in calls
        )

        local_env = (ws / "node" / ".local.env").read_text()
        assert "REPORT_UI_BUILD_CONTEXT=../webapp" in local_env

    def test_crunch_config_renamed(self, tmp_path):
        ws = scaffold_workspace(
            "btc-direction", output_dir=str(tmp_path), clone_webapp=False
        )

        config = ws / "node" / "config" / "crunch_config.py"
        assert config.exists()
        content = config.read_text()
        assert "starter_challenge" not in content
        assert "starter-challenge" not in content

    def test_challenge_pyproject_renamed(self, tmp_path):
        ws = scaffold_workspace(
            "eth-signal", output_dir=str(tmp_path), clone_webapp=False
        )

        pyproject = ws / "challenge" / "pyproject.toml"
        assert pyproject.exists()
        content = pyproject.read_text()
        assert 'name = "eth-signal"' in content
        assert "eth_signal" in content
        assert "starter" not in content.lower()

    def test_fails_if_exists(self, tmp_path):
        (tmp_path / "existing").mkdir()
        with pytest.raises(FileExistsError, match="already exists"):
            scaffold_workspace("existing", output_dir=str(tmp_path), clone_webapp=False)

    def test_no_venv_or_pycache_in_output(self, tmp_path):
        ws = scaffold_workspace(
            "clean-check", output_dir=str(tmp_path), clone_webapp=False
        )

        for root, dirs, _files in os.walk(ws):
            assert ".venv" not in dirs, f".venv found in {root}"
            assert "__pycache__" not in dirs, f"__pycache__ found in {root}"
            assert ".pytest_cache" not in dirs, f".pytest_cache found in {root}"


class TestScaffoldWithPack:
    def test_realtime_pack(self, tmp_path):
        ws = scaffold_workspace(
            "pred-test",
            pack="realtime",
            output_dir=str(tmp_path),
            clone_webapp=False,
        )

        config = ws / "node" / "config" / "crunch_config.py"
        assert config.exists()
        content = config.read_text()
        # Prediction pack has its own crunch_config
        assert "starter_challenge" not in content

    def test_trading_pack(self, tmp_path):
        ws = scaffold_workspace(
            "trade-test",
            pack="trading",
            output_dir=str(tmp_path),
            clone_webapp=False,
        )

        cruncher = ws / "challenge" / "trade_test" / "cruncher.py"
        assert cruncher.exists()

        # Trading has leaderboard columns
        cols = (
            ws
            / "node"
            / "deployment"
            / "report-ui"
            / "config"
            / "leaderboard-columns.json"
        )
        assert cols.exists()

    def test_tournament_pack(self, tmp_path):
        ws = scaffold_workspace(
            "tourney-test",
            pack="tournament",
            output_dir=str(tmp_path),
            clone_webapp=False,
        )

        # Tournament pack includes examples
        examples = ws / "challenge" / "tourney_test" / "examples"
        assert examples.is_dir()

    def test_unknown_pack_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown pack"):
            scaffold_workspace(
                "bad-pack",
                pack="nonexistent",
                output_dir=str(tmp_path),
                clone_webapp=False,
            )

    def test_pack_overlay_overwrites_base(self, tmp_path):
        """Pack files should overwrite scaffold base files."""
        ws = scaffold_workspace(
            "overlay-test",
            pack="trading",
            output_dir=str(tmp_path),
            clone_webapp=False,
        )

        # Trading pack overrides scoring.py
        scoring = ws / "challenge" / "overlay_test" / "scoring.py"
        assert scoring.exists()
        content = scoring.read_text()
        # Trading scoring mentions signal or PnL — different from base
        assert "starter_challenge" not in content


class TestListPacks:
    def test_runs_without_error(self, capsys):
        list_packs()
        captured = capsys.readouterr()
        assert "prediction" in captured.out or "trading" in captured.out


# ── CLI entry point test ───────────────────────────────────────────────


class TestCLIEntryPoint:
    def test_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "crunch_node.cli", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "init" in result.stdout

    def test_init_via_cli(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "crunch_node.cli",
                "init",
                "cli-test-comp",
                "-o",
                str(tmp_path),
                "--no-webapp-clone",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert (tmp_path / "cli-test-comp").is_dir()
        assert (tmp_path / "cli-test-comp" / "challenge" / "cli_test_comp").is_dir()

    def test_init_with_pack_via_cli(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "crunch_node.cli",
                "init",
                "cli-pack-test",
                "--pack",
                "trading",
                "-o",
                str(tmp_path),
                "--no-webapp-clone",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert (tmp_path / "cli-pack-test").is_dir()

    def test_list_packs_via_cli(self):
        result = subprocess.run(
            [sys.executable, "-m", "crunch_node.cli", "list-packs"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "crunch-node init" in result.stdout

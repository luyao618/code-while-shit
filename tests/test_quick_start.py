import os
import subprocess
import sys
from pathlib import Path

import pytest

from cws.config import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("FOO_TEST_KEY", "QUOTED_TEST_KEY", "EXPORTED_TEST_KEY", "PREEXISTING_TEST_KEY"):
        monkeypatch.delenv(k, raising=False)
    yield monkeypatch


def test_load_dotenv_basic(tmp_path, clean_env):
    env_file = tmp_path / ".env"
    env_file.write_text("FOO_TEST_KEY=hello\n", encoding="utf-8")
    assert load_dotenv(env_file) is True
    assert os.environ["FOO_TEST_KEY"] == "hello"


def test_load_dotenv_missing_file_returns_false(tmp_path):
    assert load_dotenv(tmp_path / "does_not_exist.env") is False


def test_load_dotenv_respects_existing_env(tmp_path, clean_env):
    clean_env.setenv("PREEXISTING_TEST_KEY", "original")
    env_file = tmp_path / ".env"
    env_file.write_text("PREEXISTING_TEST_KEY=overwritten\n", encoding="utf-8")
    load_dotenv(env_file)
    assert os.environ["PREEXISTING_TEST_KEY"] == "original"


def test_load_dotenv_strips_quotes(tmp_path, clean_env):
    env_file = tmp_path / ".env"
    env_file.write_text('QUOTED_TEST_KEY="value with spaces"\n', encoding="utf-8")
    load_dotenv(env_file)
    assert os.environ["QUOTED_TEST_KEY"] == "value with spaces"


def test_load_dotenv_accepts_export_prefix(tmp_path, clean_env):
    env_file = tmp_path / ".env"
    env_file.write_text("export EXPORTED_TEST_KEY=ok\n", encoding="utf-8")
    load_dotenv(env_file)
    assert os.environ["EXPORTED_TEST_KEY"] == "ok"


def test_load_dotenv_ignores_comments_and_blanks(tmp_path, clean_env):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\n\nFOO_TEST_KEY=v\n", encoding="utf-8")
    load_dotenv(env_file)
    assert os.environ["FOO_TEST_KEY"] == "v"


def _run(args, cwd, env=None):
    env_full = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", "cws", *args],
        capture_output=True,
        text=True,
        env=env_full,
        cwd=str(cwd),
    )


def test_init_creates_global_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    r = _run(["init"], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    config_path = tmp_path / "cfg" / "cws" / "config.toml"
    assert config_path.exists()
    contents = config_path.read_text(encoding="utf-8")
    assert "feishu" in contents
    assert "app_id" in contents


def test_init_does_not_overwrite_existing_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    config_path = tmp_path / "cfg" / "cws" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[feishu]\napp_id = \"existing\"\n", encoding="utf-8")
    r = _run(["init"], cwd=tmp_path)
    assert r.returncode == 0
    # Content should be preserved
    assert "existing" in config_path.read_text(encoding="utf-8")


def test_doctor_without_agent_surveys_all_three(tmp_path):
    r = _run(
        ["doctor"],
        cwd=PROJECT_ROOT,
        env={
            "CWS_RUNTIME_DIR": str(tmp_path),
            "FEISHU_APP_ID": "x",
            "FEISHU_APP_SECRET": "y",
        },
    )
    combined = r.stdout + r.stderr
    for agent in ("codex", "claude-code", "opencode"):
        assert agent in combined

"""Tests for user_config module."""
import pytest
from pathlib import Path


def test_load_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from cws import user_config
    # Reload module state (get_path uses env at call time)
    data = user_config.load()
    assert data == {}


def test_set_get_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from cws import user_config
    user_config.set_value("agent.default", "codex")
    val = user_config.get_value("agent.default")
    assert val == "codex"


def test_unset_removes_key(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from cws import user_config
    user_config.set_value("agent.default", "codex")
    user_config.unset_value("agent.default")
    val = user_config.get_value("agent.default")
    assert val is None


def test_set_rejects_unknown_key(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from cws import user_config
    with pytest.raises(ValueError, match="unknown key"):
        user_config.set_value("feishu.nonexistent_field", "x")


def test_get_path_uses_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "myconfig"))
    from cws import user_config
    p = user_config.get_path()
    assert str(p).startswith(str(tmp_path / "myconfig"))
    assert p.name == "config.toml"


def test_format_for_display(monkeypatch):
    from cws import user_config
    data = {"feishu": {"app_id": "abc", "app_secret": "xyz"}, "agent": {"default": "codex"}}
    output = user_config.format_for_display(data)
    assert "[feishu]" in output
    assert 'app_id = "abc"' in output
    assert "[agent]" in output
    assert 'default = "codex"' in output

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


def test_format_for_display_masks_secrets():
    from cws import user_config
    data = {
        "feishu": {
            "app_id": "cli_a1b2c3d4",
            "app_secret": "secret_long_enough_to_partial_mask",
            "short_token": "tiny",
        }
    }
    output = user_config.format_for_display(data)
    # Non-secret key passes through
    assert 'app_id = "cli_a1b2c3d4"' in output
    # Long secret: first/last 4 visible
    assert 'app_secret = "secr...mask"' in output
    # Short token (≤8): fully masked
    assert 'short_token = "****"' in output
    # The raw secret must NOT appear
    assert "secret_long_enough_to_partial_mask" not in output


def test_format_for_display_no_mask_when_disabled():
    from cws import user_config
    data = {"feishu": {"app_secret": "secret_long_enough_to_partial_mask"}}
    output = user_config.format_for_display(data, mask_secrets=False)
    assert "secret_long_enough_to_partial_mask" in output


def test_save_writes_unmasked_to_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from cws import user_config
    user_config.set_value("feishu.app_secret", "secret_long_enough_to_partial_mask")
    # Reload from disk and confirm full value is preserved
    val = user_config.get_value("feishu.app_secret")
    assert val == "secret_long_enough_to_partial_mask"


def test_log_rotation(tmp_path):
    from cws.__main__ import _rotate_log_if_needed, _LOG_ROTATE_MAX_BYTES
    log = tmp_path / "serve.log"
    log.write_bytes(b"x" * (_LOG_ROTATE_MAX_BYTES + 1))
    _rotate_log_if_needed(log)
    assert not log.exists()
    assert (tmp_path / "serve.log.1").exists()


def test_log_rotation_keeps_max_backups(tmp_path):
    from cws.__main__ import _rotate_log_if_needed, _LOG_ROTATE_MAX_BYTES, _LOG_ROTATE_KEEP
    log = tmp_path / "serve.log"
    # Create existing backups .1, .2, .3
    for i in range(1, _LOG_ROTATE_KEEP + 1):
        (tmp_path / f"serve.log.{i}").write_text(f"backup{i}")
    log.write_bytes(b"y" * (_LOG_ROTATE_MAX_BYTES + 1))
    _rotate_log_if_needed(log)
    # Oldest (.3) should have been dropped, newer ones shifted, current → .1
    assert (tmp_path / "serve.log.1").exists()
    assert (tmp_path / "serve.log.2").exists()
    assert (tmp_path / "serve.log.3").exists()
    # Total backups still capped at KEEP (no .4)
    assert not (tmp_path / "serve.log.4").exists()


def test_log_rotation_below_threshold_noop(tmp_path):
    from cws.__main__ import _rotate_log_if_needed
    log = tmp_path / "serve.log"
    log.write_text("small")
    _rotate_log_if_needed(log)
    assert log.exists()
    assert log.read_text() == "small"
    assert not (tmp_path / "serve.log.1").exists()

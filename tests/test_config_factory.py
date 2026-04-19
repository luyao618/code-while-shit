import argparse
import pytest
from cws.config import AppConfig, ConfigConflictError, CodexAgentConfig, ClaudeCodeAgentConfig, OpencodeAgentConfig


def _args(**kw):
    ns = argparse.Namespace(agent=None, allow_auto_approve=False, force=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_workspace_is_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ns = _args(agent="codex")
    cfg = AppConfig.from_sources(ns, env={})
    assert cfg.workspace == tmp_path.resolve()


def test_default_workspace_property(tmp_path, monkeypatch):
    """Backward-compat: default_workspace == workspace."""
    monkeypatch.chdir(tmp_path)
    ns = _args(agent="codex")
    cfg = AppConfig.from_sources(ns, env={})
    assert cfg.default_workspace == cfg.workspace


def test_agent_defaults_to_claude_code():
    ns = _args()
    cfg = AppConfig.from_sources(ns, env={})
    assert isinstance(cfg.agent, ClaudeCodeAgentConfig)


def test_env_only_agent_via_cws_agent():
    ns = _args()
    cfg = AppConfig.from_sources(ns, env={"CWS_AGENT": "codex", "FEISHU_APP_ID": "a", "FEISHU_APP_SECRET": "b"})
    assert isinstance(cfg.agent, CodexAgentConfig)


def test_claude_code_config():
    ns = _args(agent="claude-code")
    cfg = AppConfig.from_sources(ns, env={})
    assert isinstance(cfg.agent, ClaudeCodeAgentConfig)


def test_opencode_config_allow_auto_approve():
    ns = _args(agent="opencode", allow_auto_approve=True)
    cfg = AppConfig.from_sources(ns, env={})
    assert isinstance(cfg.agent, OpencodeAgentConfig)
    assert cfg.agent.allow_auto_approve is True


def test_unknown_agent_raises():
    ns = _args(agent="bogus")
    with pytest.raises(ConfigConflictError, match="bogus"):
        AppConfig.from_sources(ns, env={})


def test_runtime_dir_is_global(tmp_path, monkeypatch):
    """Runtime dir is a single global location regardless of cwd (singleton serve)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CWS_RUNTIME_DIR", raising=False)
    ns = _args(agent="codex")
    cfg_a = AppConfig.from_sources(ns, env={})

    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    cfg_b = AppConfig.from_sources(ns, env={})

    # Same runtime dir regardless of cwd
    assert cfg_a.runtime_dir == cfg_b.runtime_dir
    assert "cws" in str(cfg_a.runtime_dir)
    assert "runtime" in str(cfg_a.runtime_dir)
    # No workspace-hashed sub-path
    assert str(cfg_a.runtime_dir).endswith("/cws/runtime")


def test_runtime_dir_override_via_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rt = str(tmp_path / "myruntime")
    ns = _args(agent="codex")
    cfg = AppConfig.from_sources(ns, env={"CWS_RUNTIME_DIR": rt})
    assert str(cfg.runtime_dir) == str(tmp_path / "myruntime")

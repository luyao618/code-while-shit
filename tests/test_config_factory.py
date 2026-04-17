import argparse
import pytest
from vcws.config import AppConfig, ConfigConflictError, CodexAgentConfig, ClaudeCodeAgentConfig, OpencodeAgentConfig


def _args(**kw):
    ns = argparse.Namespace(agent=None, workspace=None, allow_auto_approve=False, force=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_cli_beats_env_workspace(tmp_path):
    ns = _args(agent="codex", workspace=str(tmp_path))
    cfg = AppConfig.from_sources(ns, env={})
    assert str(cfg.default_workspace) == str(tmp_path.resolve())


def test_conflict_workspace_raises(tmp_path):
    ns = _args(agent="codex", workspace="/x")
    with pytest.raises(ConfigConflictError, match="workspace"):
        AppConfig.from_sources(ns, env={"CWS_DEFAULT_WORKSPACE": "/y"})


def test_agent_required():
    ns = _args()
    with pytest.raises(ConfigConflictError, match="agent"):
        AppConfig.from_sources(ns, env={})


def test_env_only_agent_via_vcws_agent():
    ns = _args()
    cfg = AppConfig.from_sources(ns, env={"VCWS_AGENT": "codex", "FEISHU_APP_ID": "a", "FEISHU_APP_SECRET": "b"})
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

from unittest.mock import MagicMock
import pytest
from vcws.agents.opencode import (
    OpencodeAgentBackend,
    OpencodeAgentTurn,
    _pick_free_port,
)
from vcws.agents.base import TurnState, CancelNotSupported


class FakeConfig:
    def __init__(self, allow=False, host="127.0.0.1", port=None, command="opencode", startup_timeout_s=5.0):
        self.allow_auto_approve = allow
        self.host = host
        self.port = port
        self.command = command
        self.startup_timeout_s = startup_timeout_s


def test_pick_free_port_is_bindable():
    p = _pick_free_port()
    assert 1024 < p < 65536


def test_capability_banner_reflects_allow_flag():
    backend = OpencodeAgentBackend(FakeConfig(allow=False))
    banner = backend.capability_banner()
    assert "审批不支持" in banner
    backend2 = OpencodeAgentBackend(FakeConfig(allow=True))
    banner2 = backend2.capability_banner()
    assert "已启用" in banner2


def test_approval_wrapper_denies_by_default():
    backend = OpencodeAgentBackend(FakeConfig(allow=False))
    publish = MagicMock()
    wrapped = backend._wrap_approval(lambda req: "approve", publish)
    result = wrapped(MagicMock())
    assert result == "deny"
    # Feishu received explanation
    assert publish.call_count == 1


def test_approval_wrapper_approves_with_flag():
    backend = OpencodeAgentBackend(FakeConfig(allow=True))
    publish = MagicMock()
    wrapped = backend._wrap_approval(lambda req: "deny", publish)
    result = wrapped(MagicMock())
    assert result == "approve"


def test_cancel_lazy_probe_unsupported_raises(monkeypatch):
    backend = OpencodeAgentBackend(FakeConfig())
    monkeypatch.setattr(backend, "probe_cancel_support", lambda: False)
    turn = OpencodeAgentTurn(
        backend=backend,
        conversation=MagicMock(),
        workspace_path="/tmp",
        prompt="hi",
        existing_thread_id=None,
        request_approval=MagicMock(return_value="deny"),
        request_input=MagicMock(),
        publish_status=MagicMock(),
    )
    with pytest.raises(CancelNotSupported):
        turn.cancel()


def test_cancel_lazy_probe_supported_sets_state(monkeypatch):
    backend = OpencodeAgentBackend(FakeConfig())
    monkeypatch.setattr(backend, "probe_cancel_support", lambda: True)
    monkeypatch.setattr(backend, "abort_current", lambda: None)
    turn = OpencodeAgentTurn(
        backend=backend,
        conversation=MagicMock(),
        workspace_path="/tmp",
        prompt="hi",
        existing_thread_id=None,
        request_approval=MagicMock(),
        request_input=MagicMock(),
        publish_status=MagicMock(),
    )
    turn.cancel()
    assert turn.state == TurnState.CANCELLED


def test_exit_no_double_cancel_on_completed():
    backend = OpencodeAgentBackend(FakeConfig())
    turn = OpencodeAgentTurn(
        backend=backend,
        conversation=MagicMock(),
        workspace_path="/tmp",
        prompt="hi",
        existing_thread_id=None,
        request_approval=MagicMock(),
        request_input=MagicMock(),
        publish_status=MagicMock(),
    )
    turn.state = TurnState.COMPLETED
    turn.__exit__(None, None, None)
    # No exception, no state change
    assert turn.state == TurnState.COMPLETED


def test_kill_without_spawn_is_noop():
    backend = OpencodeAgentBackend(FakeConfig())
    backend.kill()  # Should not raise

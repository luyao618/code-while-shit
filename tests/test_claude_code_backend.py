import sys
import pytest
from cws.agents.claude_code import ClaudeCodeAgentTurn, ClaudeCodeImportError


def test_import_error_when_sdk_missing(monkeypatch):
    from cws.agents import claude_code as cc

    def fake_import_that_raises(name, *a, **kw):
        if name == "claude_agent_sdk":
            raise ImportError("not found")
        return __import__(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", fake_import_that_raises)
    # Remove from sys.modules so the import guard triggers
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)
    with pytest.raises(ClaudeCodeImportError):
        cc._import_sdk()


def test_cancel_sets_state_cancelled():
    from unittest.mock import MagicMock
    from cws.agents.base import TurnState

    turn = ClaudeCodeAgentTurn(
        backend=MagicMock(),
        conversation=MagicMock(),
        workspace_path="/tmp",
        prompt="hi",
        existing_thread_id=None,
        request_approval=MagicMock(),
        request_input=MagicMock(),
        publish_status=MagicMock(),
    )
    assert turn.state == TurnState.RUNNING
    turn.cancel()
    assert turn.state == TurnState.CANCELLED
    assert turn._cancel_event.is_set()


def test_exit_no_double_cancel():
    from unittest.mock import MagicMock
    from cws.agents.base import TurnState

    turn = ClaudeCodeAgentTurn(
        backend=MagicMock(),
        conversation=MagicMock(),
        workspace_path="/tmp",
        prompt="hi",
        existing_thread_id=None,
        request_approval=MagicMock(),
        request_input=MagicMock(),
        publish_status=MagicMock(),
    )
    turn.state = TurnState.COMPLETED
    turn.__exit__(None, None, None)  # Should NOT call cancel
    assert not turn._cancel_event.is_set()

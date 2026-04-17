"""Cross-backend parity contract tests.

Each backend implements AgentBackend + AgentTurn Protocol. These tests verify:
- begin_turn() returns a context-manager AgentTurn
- Entering __enter__ sets state = RUNNING
- cancel() on a RUNNING turn transitions to CANCELLED (or raises CancelNotSupported lazily for opencode)
- __exit__ on a non-RUNNING turn does NOT re-invoke cancel
- kill() on backend is safe (idempotent when nothing running)
- Each backend declares agent_type matching its identifier
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from vcws.agents.base import AgentTurn, CancelNotSupported, TurnState
from vcws.agents.claude_code import ClaudeCodeAgentBackend
from vcws.agents.codex import CodexAgentBackend
from vcws.agents.opencode import OpencodeAgentBackend
from vcws.config import ClaudeCodeAgentConfig, CodexAgentConfig, OpencodeAgentConfig
from vcws.models import ConversationRef


def _conversation() -> ConversationRef:
    return ConversationRef(channel="test", account_id="acc", conversation_id="conv", thread_id=None)


# ---------------------------------------------------------------------------
# Backend fixtures — each returns a backend with its network/process surface faked.
# ---------------------------------------------------------------------------


def _codex_backend():
    """Codex backend with JSON-RPC client stubbed out."""
    backend = CodexAgentBackend(CodexAgentConfig())
    # Stub out the client so no real subprocess is spawned.
    backend._client = MagicMock()
    backend._client.start = MagicMock()
    backend._client.notify = MagicMock()
    backend._client.request = MagicMock(
        return_value={"turn": {"id": "t-fake"}, "thread": {"id": "th-fake"}}
    )
    return backend


def _claude_code_backend():
    return ClaudeCodeAgentBackend(ClaudeCodeAgentConfig())


def _opencode_backend():
    return OpencodeAgentBackend(OpencodeAgentConfig())


# ---------------------------------------------------------------------------
# Contract tests — run against each backend
# ---------------------------------------------------------------------------


BACKENDS = [
    pytest.param(_codex_backend, "codex", id="codex"),
    pytest.param(_claude_code_backend, "claude-code", id="claude-code"),
    pytest.param(_opencode_backend, "opencode", id="opencode"),
]

_TURN_KWARGS = dict(
    workspace_path="/tmp",
    prompt="hi",
    existing_thread_id=None,
    request_approval=lambda _req: "deny",
    request_input=lambda _req: "",
    publish_status=lambda _u: None,
)


def _begin(backend):
    return backend.begin_turn(conversation=_conversation(), **_TURN_KWARGS)


@pytest.mark.parametrize("factory,expected_type", BACKENDS)
def test_agent_type_attribute(factory, expected_type):
    backend = factory()
    assert backend.agent_type == expected_type


@pytest.mark.parametrize("factory,expected_type", BACKENDS)
def test_begin_turn_returns_agentturn_compatible(factory, expected_type):
    backend = factory()
    turn = _begin(backend)
    # Structural Protocol check (AgentTurn is runtime_checkable)
    assert isinstance(turn, AgentTurn), f"{expected_type} turn is not AgentTurn-compatible"
    assert hasattr(turn, "state")
    assert hasattr(turn, "kill_event")
    assert isinstance(turn.kill_event, threading.Event)
    assert turn.state == TurnState.RUNNING


@pytest.mark.parametrize("factory,expected_type", BACKENDS)
def test_exit_on_completed_does_not_recancel(factory, expected_type):
    backend = factory()
    turn = _begin(backend)
    with turn as t:
        t.state = TurnState.COMPLETED
    # __exit__ has now been invoked; state should still be COMPLETED (not overwritten)
    assert turn.state == TurnState.COMPLETED


@pytest.mark.parametrize("factory,expected_type", BACKENDS)
def test_cancel_transitions_state_or_raises(factory, expected_type):
    backend = factory()
    turn = _begin(backend)
    if expected_type == "opencode":
        with (
            patch.object(backend, "probe_cancel_support", return_value=True),
            patch.object(backend, "abort_current", return_value=None),
        ):
            turn.cancel()
            assert turn.state == TurnState.CANCELLED
    else:
        turn.cancel()
        # codex will attempt a JSON-RPC call via stubbed client; it should not raise
        assert turn.state == TurnState.CANCELLED


@pytest.mark.parametrize("factory,expected_type", BACKENDS)
def test_kill_is_safe_without_running_process(factory, expected_type):
    backend = factory()
    # Should not raise even if nothing was ever started
    backend.kill()


@pytest.mark.parametrize("factory,expected_type", BACKENDS)
def test_kill_event_is_per_turn(factory, expected_type):
    backend = factory()
    turn_a = _begin(backend)
    turn_b = _begin(backend)
    assert turn_a.kill_event is not turn_b.kill_event
    turn_a.kill_event.set()
    assert turn_a.kill_event.is_set()
    assert not turn_b.kill_event.is_set()


def test_opencode_cancel_lazy_probe_unsupported_raises():
    backend = _opencode_backend()
    with patch.object(backend, "probe_cancel_support", return_value=False):
        turn = _begin(backend)
        with pytest.raises(CancelNotSupported):
            turn.cancel()

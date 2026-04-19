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


def _mk_turn(backend=None, existing_thread_id=None):
    from unittest.mock import MagicMock

    return ClaudeCodeAgentTurn(
        backend=backend or MagicMock(),
        conversation=MagicMock(),
        workspace_path="/tmp",
        prompt="hi",
        existing_thread_id=existing_thread_id,
        request_approval=MagicMock(),
        request_input=MagicMock(),
        publish_status=MagicMock(),
    )


def test_cancel_sets_state_cancelled():
    from cws.agents.base import TurnState
    from unittest.mock import MagicMock

    backend = MagicMock()
    backend._peek_client.return_value = None
    turn = _mk_turn(backend=backend)
    assert turn.state == TurnState.RUNNING
    turn.cancel()
    assert turn.state == TurnState.CANCELLED
    assert turn._cancel_event.is_set()


def test_exit_no_double_cancel():
    from cws.agents.base import TurnState

    turn = _mk_turn()
    turn.state = TurnState.COMPLETED
    turn.__exit__(None, None, None)  # Should NOT call cancel
    assert not turn._cancel_event.is_set()


def test_handle_event_captures_session_id_from_attribute():
    from types import SimpleNamespace

    turn = _mk_turn()
    event = SimpleNamespace(session_id="sess-abc", text="hello")
    turn._handle_event(event, [])
    assert turn._observed_session_id == "sess-abc"

    # Subsequent events with a different id should not overwrite the first one.
    event2 = SimpleNamespace(session_id="sess-xyz", text="more")
    turn._handle_event(event2, [])
    assert turn._observed_session_id == "sess-abc"


def test_handle_event_captures_session_id_from_system_message_data():
    from types import SimpleNamespace

    turn = _mk_turn()
    event = SimpleNamespace(subtype="init", data={"session_id": "sess-init"})
    turn._handle_event(event, [])
    assert turn._observed_session_id == "sess-init"


def test_backend_caches_client_per_conversation_workspace():
    """Backend must reuse the same ClaudeSDKClient across turns for the same
    (conversation, workspace) pair so context persists. This is the core
    behavior change vs. the old per-turn subprocess design."""
    from unittest.mock import MagicMock
    from cws.agents.claude_code import ClaudeCodeAgentBackend
    from cws.models import ConversationRef

    backend = ClaudeCodeAgentBackend(config=MagicMock())
    try:
        conv = ConversationRef("feishu", "default", "chat-1")

        connect_calls = []

        class FakeClient:
            def __init__(self, options=None):
                self.id = id(self)
                self.options = options

            async def connect(self):
                connect_calls.append(self.id)

            async def disconnect(self):
                pass

        class FakeOptions:
            def __init__(self, **kw):
                self.kw = kw

        fake_sdk = MagicMock()
        fake_sdk.ClaudeSDKClient = FakeClient
        fake_sdk.ClaudeAgentOptions = FakeOptions

        c1 = backend._get_or_connect_client(fake_sdk, conv, "/tmp/ws")
        c2 = backend._get_or_connect_client(fake_sdk, conv, "/tmp/ws")
        assert c1 is c2, "expected the same client instance to be reused"
        assert len(connect_calls) == 1, "connect() should only fire once"

        # Different workspace gets a different client.
        c3 = backend._get_or_connect_client(fake_sdk, conv, "/tmp/other")
        assert c3 is not c1
        assert len(connect_calls) == 2
    finally:
        backend._loop.shutdown()


def test_backend_kill_disconnects_clients():
    from unittest.mock import MagicMock
    from cws.agents.claude_code import ClaudeCodeAgentBackend
    from cws.models import ConversationRef

    backend = ClaudeCodeAgentBackend(config=MagicMock())
    try:
        disconnect_calls = []

        class FakeClient:
            def __init__(self, options=None):
                self.options = options

            async def connect(self):
                pass

            async def disconnect(self):
                disconnect_calls.append(1)

        class FakeOptions:
            def __init__(self, **kw):
                pass

        fake_sdk = MagicMock()
        fake_sdk.ClaudeSDKClient = FakeClient
        fake_sdk.ClaudeAgentOptions = FakeOptions

        backend._get_or_connect_client(fake_sdk, ConversationRef("feishu", "default", "a"), "/tmp/ws")
        backend._get_or_connect_client(fake_sdk, ConversationRef("feishu", "default", "b"), "/tmp/ws")
        assert len(backend._clients) == 2

        backend.kill()
        assert backend._clients == {}
        assert len(disconnect_calls) == 2
    finally:
        backend._loop.shutdown()

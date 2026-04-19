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


def test_handle_event_captures_session_id_from_attribute():
    from unittest.mock import MagicMock
    from types import SimpleNamespace

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
    # AssistantMessage / ResultMessage style: session_id as attribute
    event = SimpleNamespace(session_id="sess-abc", text="hello")
    turn._handle_event(event, [])
    assert turn._observed_session_id == "sess-abc"

    # Subsequent events with a different id should not overwrite the first one.
    event2 = SimpleNamespace(session_id="sess-xyz", text="more")
    turn._handle_event(event2, [])
    assert turn._observed_session_id == "sess-abc"


def test_handle_event_captures_session_id_from_system_message_data():
    from unittest.mock import MagicMock
    from types import SimpleNamespace

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
    # SystemMessage("init", data={...}) style
    event = SimpleNamespace(subtype="init", data={"session_id": "sess-init"})
    turn._handle_event(event, [])
    assert turn._observed_session_id == "sess-init"


def test_iter_sdk_passes_resume_when_existing_thread_id_real(monkeypatch):
    import asyncio
    from unittest.mock import MagicMock

    captured: dict = {}

    class FakeOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    async def fake_query(prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options
        if False:
            yield  # make it an async generator

    fake_sdk = MagicMock()
    fake_sdk.query = fake_query
    fake_sdk.ClaudeAgentOptions = FakeOptions

    turn = ClaudeCodeAgentTurn(
        backend=MagicMock(),
        conversation=MagicMock(),
        workspace_path="/tmp/ws",
        prompt="hello",
        existing_thread_id="real-session-id-123",
        request_approval=MagicMock(),
        request_input=MagicMock(),
        publish_status=MagicMock(),
    )

    async def drive():
        async for _ in turn._iter_sdk(fake_sdk):
            pass

    asyncio.run(drive())
    assert captured.get("resume") == "real-session-id-123"
    assert str(captured.get("cwd")) == "/tmp/ws"


def test_iter_sdk_skips_resume_for_placeholder_thread_id():
    import asyncio
    from unittest.mock import MagicMock

    captured: dict = {}

    class FakeOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    async def fake_query(prompt, options):
        if False:
            yield

    fake_sdk = MagicMock()
    fake_sdk.query = fake_query
    fake_sdk.ClaudeAgentOptions = FakeOptions

    turn = ClaudeCodeAgentTurn(
        backend=MagicMock(),
        conversation=MagicMock(),
        workspace_path="/tmp/ws",
        prompt="hello",
        existing_thread_id="cc-1234",  # placeholder, not a real SDK session id
        request_approval=MagicMock(),
        request_input=MagicMock(),
        publish_status=MagicMock(),
    )

    async def drive():
        async for _ in turn._iter_sdk(fake_sdk):
            pass

    asyncio.run(drive())
    assert "resume" not in captured

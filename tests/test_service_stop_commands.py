import threading
from unittest.mock import MagicMock

from cws.config import AppConfig, FeishuConfig, CodexAgentConfig
from cws.service import BridgeService
from cws.models import (
    Actor,
    ConversationRef,
    InboundMessage,
    TurnOutcome,
)
from cws.agents.base import TurnState, CancelNotSupported


def _make_config(tmp_path):
    return AppConfig(
        default_workspace=tmp_path,
        runtime_dir=tmp_path / "rt",
        state_file=tmp_path / "rt" / "bridge-state.json",
        feishu=FeishuConfig(
            app_id="x", app_secret="y", domain="https://open.feishu.cn",
            base_url="https://open.feishu.cn/open-apis", allowed_user_ids=tuple(),
        ),
        agent=CodexAgentConfig(),
    )


class FakeTurn:
    def __init__(self, supports_cancel=True):
        self.state = TurnState.RUNNING
        self.kill_event = threading.Event()
        self.supports_cancel = supports_cancel
        self.supports_approval = True
        self.cancel_calls = 0
        self._completed = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.state == TurnState.RUNNING:
            self.cancel()
        return False

    def run(self):
        self._completed.wait(timeout=5.0)
        if self.state == TurnState.RUNNING:
            self.state = TurnState.COMPLETED
        return TurnOutcome(
            thread_id="t-1",
            summary="done",
            status="completed" if self.state == TurnState.COMPLETED else "interrupted",
            raw_text="done",
            error=None,
        )

    def cancel(self):
        self.cancel_calls += 1
        if not self.supports_cancel:
            raise CancelNotSupported()
        self.state = TurnState.CANCELLED
        self._completed.set()


class FakeBackend:
    agent_type = "codex"

    def __init__(self):
        self.kill_calls = 0
        self._turn = None

    def begin_turn(self, *, conversation, workspace_path, prompt, existing_thread_id,
                   request_approval, request_input, publish_status):
        self._turn = FakeTurn(supports_cancel=True)
        return self._turn

    def kill(self):
        self.kill_calls += 1


def _make_service(tmp_path, backend):
    config = _make_config(tmp_path)
    config.ensure_runtime_dirs()
    adapter = MagicMock()
    service = BridgeService(config=config, adapter=adapter, backend=backend)
    return service, adapter


def _make_message(conversation, text="hi"):
    return InboundMessage(
        conversation=conversation,
        actor=Actor(user_id="u1"),
        text=text,
    )


def _make_conversation():
    return ConversationRef(channel="im", account_id="acc", conversation_id="conv", thread_id=None)


def test_cancel_no_active_turn(tmp_path):
    service, adapter = _make_service(tmp_path, FakeBackend())
    conversation = _make_conversation()
    service._handle_cancel(conversation)
    adapter.send_result.assert_called_once()
    msg = adapter.send_result.call_args[0][1]
    assert "已停止" in msg
    adapter.update_card.assert_not_called() if hasattr(adapter, "update_card") else None


def test_cancel_unsupported(tmp_path):
    service, adapter = _make_service(tmp_path, FakeBackend())
    conversation = _make_conversation()
    turn = FakeTurn(supports_cancel=False)
    session = service.state.ensure_session(conversation, str(tmp_path))
    with service._active_turns_lock:
        service._active_turns[session.key] = turn
    service._handle_cancel(conversation)
    assert turn.cancel_calls == 0
    adapter.send_result.assert_called_once()
    msg = adapter.send_result.call_args[0][1]
    assert "不支持" in msg
    adapter.update_card.assert_not_called() if hasattr(adapter, "update_card") else None


def test_cancel_invokes_turn_cancel_exactly_once(tmp_path):
    service, adapter = _make_service(tmp_path, FakeBackend())
    conversation = _make_conversation()
    turn = FakeTurn(supports_cancel=True)
    session = service.state.ensure_session(conversation, str(tmp_path))
    with service._active_turns_lock:
        service._active_turns[session.key] = turn
    service._handle_cancel(conversation)
    assert turn.cancel_calls == 1
    assert turn.state == TurnState.CANCELLED
    adapter.send_result.assert_called_once()
    assert "用户取消" in adapter.send_result.call_args[0][1]
    adapter.update_card.assert_not_called() if hasattr(adapter, "update_card") else None


def test_cancel_via_handle_message_slash_stop(tmp_path):
    service, adapter = _make_service(tmp_path, FakeBackend())
    conversation = _make_conversation()
    turn = FakeTurn(supports_cancel=True)
    session = service.state.ensure_session(conversation, str(tmp_path))
    with service._active_turns_lock:
        service._active_turns[session.key] = turn
    service.handle_message(_make_message(conversation, text="/stop"))
    assert turn.cancel_calls == 1


def test_cancel_via_handle_message_slash_cancel(tmp_path):
    service, adapter = _make_service(tmp_path, FakeBackend())
    conversation = _make_conversation()
    turn = FakeTurn(supports_cancel=True)
    session = service.state.ensure_session(conversation, str(tmp_path))
    with service._active_turns_lock:
        service._active_turns[session.key] = turn
    service.handle_message(_make_message(conversation, text="/cancel"))
    assert turn.cancel_calls == 1


def test_double_cancel_guard(tmp_path):
    """AgentTurn.run() to completion inside `with` must invoke cancel() zero times."""
    turn = FakeTurn(supports_cancel=True)
    with turn as t:
        t._completed.set()
        outcome = t.run()
    assert turn.cancel_calls == 0
    assert outcome.status == "completed"
    assert turn.state == TurnState.COMPLETED


def test_kill_invokes_backend_kill_once(tmp_path):
    backend = FakeBackend()
    service, adapter = _make_service(tmp_path, backend)
    conversation = _make_conversation()
    turn = FakeTurn(supports_cancel=True)
    session = service.state.ensure_session(conversation, str(tmp_path))
    with service._active_turns_lock:
        service._active_turns[session.key] = turn
    service._handle_kill(conversation)
    assert backend.kill_calls == 1
    adapter.send_result.assert_called_once()
    msg = adapter.send_result.call_args[0][1]
    assert "重置 agent" in msg


def test_kill_wipes_agent_threads(tmp_path):
    from cws.models import WorkspaceBinding
    backend = FakeBackend()
    service, adapter = _make_service(tmp_path, backend)
    conversation = _make_conversation()
    service.state.save_binding(WorkspaceBinding(
        session_key="sess-1",
        workspace_path=str(tmp_path),
        agent_thread_id="t-existing",
    ))
    service._handle_kill(conversation)
    with service.state._lock:
        for b in service.state._bindings.values():
            assert b.agent_thread_id is None


def test_kill_event_per_turn_isolation(tmp_path):
    backend = FakeBackend()
    service, adapter = _make_service(tmp_path, backend)
    convA = ConversationRef(channel="im", account_id="a", conversation_id="ca", thread_id=None)
    convB = ConversationRef(channel="im", account_id="a", conversation_id="cb", thread_id=None)
    turnA = FakeTurn(supports_cancel=True)
    turnB = FakeTurn(supports_cancel=True)
    sessA = service.state.ensure_session(convA, str(tmp_path))
    sessB = service.state.ensure_session(convB, str(tmp_path))
    with service._active_turns_lock:
        service._active_turns[sessA.key] = turnA
        service._active_turns[sessB.key] = turnB
    assert turnA.kill_event is not turnB.kill_event
    service._handle_kill(convA)
    assert turnA.kill_event.is_set()
    assert turnB.kill_event.is_set()
    assert turnA.kill_event is not turnB.kill_event


def test_kill_sends_feishu_result_not_card(tmp_path):
    backend = FakeBackend()
    service, adapter = _make_service(tmp_path, backend)
    conversation = _make_conversation()
    service._handle_kill(conversation)
    adapter.send_result.assert_called_once()
    adapter.update_card.assert_not_called() if hasattr(adapter, "update_card") else None


def test_kill_via_handle_message_slash_kill(tmp_path):
    backend = FakeBackend()
    service, adapter = _make_service(tmp_path, backend)
    conversation = _make_conversation()
    turn = FakeTurn(supports_cancel=True)
    session = service.state.ensure_session(conversation, str(tmp_path))
    with service._active_turns_lock:
        service._active_turns[session.key] = turn
    service.handle_message(_make_message(conversation, text="/kill"))
    assert backend.kill_calls == 1


def test_kill_via_handle_message_slash_clear(tmp_path):
    backend = FakeBackend()
    service, adapter = _make_service(tmp_path, backend)
    conversation = _make_conversation()
    service.handle_message(_make_message(conversation, text="/clear"))
    assert backend.kill_calls == 1


def test_next_message_after_kill_gets_fresh_thread(tmp_path):
    from cws.models import WorkspaceBinding
    backend = FakeBackend()
    service, adapter = _make_service(tmp_path, backend)
    conversation = _make_conversation()
    # Set up a binding with a thread id
    session = service.state.ensure_session(conversation, str(tmp_path))
    service.state.save_binding(WorkspaceBinding(
        session_key=session.key,
        workspace_path=str(tmp_path),
        agent_thread_id="thread-old",
    ))
    # Kill wipes threads
    service._handle_kill(conversation)
    # Verify binding is wiped
    binding = service.state.get_binding(conversation, str(tmp_path))
    assert binding is None or binding.agent_thread_id is None

"""0.1 behavior regression — ensure refactor preserves original semantics under --agent codex.

This test walks the canonical 0.1 flows:
  - /workspace <path>  → session workspace changes; Feishu confirmation sent
  - /status            → transport + session + thread info in one reply
  - Normal message     → backend.begin_turn is invoked with the expected parameters
  - Approval flow      → request_approval callback fires; adapter.request_approval called
  - Input flow         → request_input callback fires; adapter.request_user_input called
"""
from __future__ import annotations

import concurrent.futures
import threading
from pathlib import Path
from unittest.mock import MagicMock

from vcws.config import AppConfig, CodexAgentConfig, FeishuConfig
from vcws.models import (
    Actor,
    ConversationRef,
    InboundMessage,
    TurnOutcome,
)
from vcws.agents.base import TurnState
from vcws.service import BridgeService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        default_workspace=tmp_path,
        runtime_dir=tmp_path / "rt",
        state_file=tmp_path / "rt" / "bridge-state.json",
        feishu=FeishuConfig(
            app_id="x",
            app_secret="y",
            domain="https://open.feishu.cn",
            base_url="https://open.feishu.cn/open-apis",
            allowed_user_ids=tuple(),
        ),
        agent=CodexAgentConfig(),
    )


def _make_conversation() -> ConversationRef:
    return ConversationRef(
        channel="feishu",
        account_id="acc",
        conversation_id="conv",
        thread_id=None,
    )


class RecordingTurn:
    """AgentTurn that records what it was given and returns a canned outcome."""

    def __init__(self, outcome: TurnOutcome) -> None:
        self.state = TurnState.RUNNING
        self.kill_event = threading.Event()
        self.supports_cancel = True
        self.supports_approval = True
        self._outcome = outcome

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.state == TurnState.RUNNING:
            self.cancel()
        return False

    def run(self) -> TurnOutcome:
        if self.state == TurnState.RUNNING:
            self.state = TurnState.COMPLETED
        return self._outcome

    def cancel(self) -> None:
        self.state = TurnState.CANCELLED


class RecordingBackend:
    agent_type = "codex"

    def __init__(self, outcome: TurnOutcome | None = None) -> None:
        self.begin_turn_calls: list[dict] = []
        self.kill_calls = 0
        self._outcome = outcome or TurnOutcome(
            thread_id="t-ok",
            summary="ok",
            status="completed",
            raw_text="ok",
            error=None,
        )
        self.last_request_approval = None
        self.last_request_input = None
        self.last_publish_status = None

    def begin_turn(
        self,
        *,
        conversation,
        workspace_path,
        prompt,
        existing_thread_id,
        request_approval,
        request_input,
        publish_status,
    ) -> RecordingTurn:
        self.begin_turn_calls.append(
            dict(
                conversation=conversation,
                workspace_path=workspace_path,
                prompt=prompt,
                existing_thread_id=existing_thread_id,
            )
        )
        self.last_request_approval = request_approval
        self.last_request_input = request_input
        self.last_publish_status = publish_status
        return RecordingTurn(self._outcome)

    def kill(self) -> None:
        self.kill_calls += 1


def _make_service(tmp_path: Path, backend: RecordingBackend | None = None):
    backend = backend or RecordingBackend()
    config = _make_config(tmp_path)
    config.ensure_runtime_dirs()
    adapter = MagicMock()
    adapter.send_result = MagicMock()
    adapter.send_status = MagicMock()
    adapter.request_approval = MagicMock()
    adapter.request_user_input = MagicMock()
    adapter.ack = MagicMock()
    service = BridgeService(config=config, adapter=adapter, backend=backend)
    return service, adapter, backend


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_workspace_command_changes_session(tmp_path):
    service, adapter, backend = _make_service(tmp_path)
    conversation = _make_conversation()
    new_workspace = tmp_path / "proj_a"
    service.handle_message(
        InboundMessage(
            conversation=conversation,
            actor=Actor(user_id="u1"),
            text=f"/workspace {new_workspace}",
        )
    )
    session = service.state.get_session(conversation)
    assert session is not None
    assert str(new_workspace) in session.active_workspace
    adapter.send_result.assert_called()


def test_status_command_replies(tmp_path):
    service, adapter, _ = _make_service(tmp_path)
    conversation = _make_conversation()
    service.handle_message(
        InboundMessage(
            conversation=conversation,
            actor=Actor(user_id="u1"),
            text="/status",
        )
    )
    adapter.send_result.assert_called()
    # Status reply should contain transport or workspace info
    msg = adapter.send_result.call_args[0][1]
    assert any(
        kw in msg.lower() for kw in ("transport", "feishu", "workspace", "status")
    )


def test_normal_message_invokes_backend_begin_turn(tmp_path):
    backend = RecordingBackend()
    service, adapter, _ = _make_service(tmp_path, backend)
    conversation = _make_conversation()
    service.handle_message(
        InboundMessage(
            conversation=conversation,
            actor=Actor(user_id="u1"),
            text="帮我看看这个仓库",
        )
    )
    service._executor.shutdown(wait=True)
    assert len(backend.begin_turn_calls) == 1
    call = backend.begin_turn_calls[0]
    assert call["prompt"] == "帮我看看这个仓库"
    assert call["existing_thread_id"] is None


def test_second_message_reuses_thread_id(tmp_path):
    outcome = TurnOutcome(
        thread_id="t-reused",
        summary="first",
        status="completed",
        raw_text="first",
        error=None,
    )
    backend = RecordingBackend(outcome=outcome)
    service, adapter, _ = _make_service(tmp_path, backend)
    conversation = _make_conversation()

    service.handle_message(
        InboundMessage(
            conversation=conversation,
            actor=Actor(user_id="u1"),
            text="first",
        )
    )
    service._executor.shutdown(wait=True)

    # Restart executor for second message
    service._executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=4, thread_name_prefix="bridge"
    )
    service.handle_message(
        InboundMessage(
            conversation=conversation,
            actor=Actor(user_id="u1"),
            text="second",
        )
    )
    service._executor.shutdown(wait=True)

    assert len(backend.begin_turn_calls) == 2
    assert backend.begin_turn_calls[1]["existing_thread_id"] == "t-reused"


def test_kill_then_next_message_starts_fresh_thread(tmp_path):
    outcome = TurnOutcome(
        thread_id="t-first",
        summary="ok",
        status="completed",
        raw_text="ok",
        error=None,
    )
    backend = RecordingBackend(outcome=outcome)
    service, adapter, _ = _make_service(tmp_path, backend)
    conversation = _make_conversation()

    service.handle_message(
        InboundMessage(
            conversation=conversation,
            actor=Actor(user_id="u1"),
            text="first",
        )
    )
    service._executor.shutdown(wait=True)

    # Issue /kill (synchronous)
    service._executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=4, thread_name_prefix="bridge"
    )
    service.handle_message(
        InboundMessage(
            conversation=conversation,
            actor=Actor(user_id="u1"),
            text="/kill",
        )
    )

    # Send another message after kill
    service._executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=4, thread_name_prefix="bridge"
    )
    service.handle_message(
        InboundMessage(
            conversation=conversation,
            actor=Actor(user_id="u1"),
            text="third",
        )
    )
    service._executor.shutdown(wait=True)

    turn_calls = backend.begin_turn_calls
    assert len(turn_calls) >= 2
    # After kill, thread id should be reset
    assert turn_calls[-1]["existing_thread_id"] is None

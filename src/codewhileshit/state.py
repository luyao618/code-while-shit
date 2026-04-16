from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ConversationRef, ConversationSession, FeishuTransportState, PendingInteraction, WorkspaceBinding


@dataclass(frozen=True)
class StateSnapshot:
    sessions: dict[str, ConversationSession]
    bindings: dict[str, WorkspaceBinding]
    pending: dict[str, PendingInteraction]
    transport: FeishuTransportState


class StateStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.RLock()
        self._sessions: dict[str, ConversationSession] = {}
        self._bindings: dict[str, WorkspaceBinding] = {}
        self._pending: dict[str, PendingInteraction] = {}
        self._transport = FeishuTransportState()
        self._load()

    def snapshot(self) -> StateSnapshot:
        with self._lock:
            return StateSnapshot(
                dict(self._sessions),
                dict(self._bindings),
                dict(self._pending),
                FeishuTransportState.from_dict(self._transport.to_dict()),
            )

    def ensure_session(self, conversation: ConversationRef, default_workspace: str) -> ConversationSession:
        default_workspace = self._normalize_workspace(default_workspace)
        with self._lock:
            key = conversation.session_key
            session = self._sessions.get(key)
            if session is None:
                session = ConversationSession(
                    channel=conversation.channel,
                    account_id=conversation.account_id,
                    conversation_id=conversation.conversation_id,
                    thread_id=conversation.thread_id,
                    active_workspace=default_workspace,
                )
                self._sessions[key] = session
                self._bindings[conversation.binding_key(default_workspace)] = WorkspaceBinding(
                    session_key=key,
                    workspace_path=default_workspace,
                )
                self._save()
            return session

    def save_session(self, session: ConversationSession) -> None:
        session.active_workspace = self._normalize_workspace(session.active_workspace)
        with self._lock:
            self._sessions[session.key] = session
            self._bindings.setdefault(
                f"{session.key}@{session.active_workspace}",
                WorkspaceBinding(session_key=session.key, workspace_path=session.active_workspace),
            )
            self._save()

    def get_session(self, conversation: ConversationRef) -> ConversationSession | None:
        with self._lock:
            return self._sessions.get(conversation.session_key)

    def ensure_binding(self, conversation: ConversationRef, workspace_path: str) -> WorkspaceBinding:
        workspace_path = self._normalize_workspace(workspace_path)
        with self._lock:
            key = conversation.binding_key(workspace_path)
            binding = self._bindings.get(key)
            if binding is None:
                binding = WorkspaceBinding(session_key=conversation.session_key, workspace_path=workspace_path)
                self._bindings[key] = binding
                self._save()
            return binding

    def get_binding(self, conversation: ConversationRef, workspace_path: str) -> WorkspaceBinding | None:
        workspace_path = self._normalize_workspace(workspace_path)
        with self._lock:
            return self._bindings.get(conversation.binding_key(workspace_path))

    def save_binding(self, binding: WorkspaceBinding) -> None:
        binding = WorkspaceBinding(
            session_key=binding.session_key,
            workspace_path=self._normalize_workspace(binding.workspace_path),
            codex_thread_id=binding.codex_thread_id,
        )
        with self._lock:
            self._bindings[binding.key] = binding
            self._save()

    def set_pending(self, pending: PendingInteraction) -> None:
        with self._lock:
            self._pending[pending.request_id] = pending
            session = self._sessions.get(pending.session_key)
            if session:
                session.pending_request_id = pending.request_id
                session.state = "waiting_approval" if pending.kind == "approval" else "waiting_input"
                session.last_status = pending.prompt
                session.recovery_note = None
            self._save()

    def get_pending(self, request_id: str) -> PendingInteraction | None:
        with self._lock:
            return self._pending.get(request_id)

    def get_transport_state(self) -> FeishuTransportState:
        with self._lock:
            return FeishuTransportState.from_dict(self._transport.to_dict())

    def update_transport_state(
        self,
        *,
        mode: str | None = None,
        status: str | None = None,
        last_connected_at: str | None = None,
        last_disconnected_at: str | None = None,
        last_error: str | None = None,
        reconnect_attempts: int | None = None,
    ) -> FeishuTransportState:
        with self._lock:
            if mode is not None:
                self._transport.mode = mode
            if status is not None:
                self._transport.status = status
            if last_connected_at is not None:
                self._transport.last_connected_at = last_connected_at
            if last_disconnected_at is not None:
                self._transport.last_disconnected_at = last_disconnected_at
            if last_error is not None:
                self._transport.last_error = last_error
            if reconnect_attempts is not None:
                self._transport.reconnect_attempts = reconnect_attempts
            self._save()
            return FeishuTransportState.from_dict(self._transport.to_dict())

    def should_accept_transport_event(self, kind: str, key: str | None, *, ttl_seconds: int = 86400) -> bool:
        normalized_key = (key or "").strip()
        if not normalized_key:
            return True
        now = datetime.now(UTC)
        threshold = now.timestamp() - ttl_seconds
        entry_key = f"{kind}:{normalized_key}"
        with self._lock:
            self._transport.processed_event_keys = {
                existing: seen_at
                for existing, seen_at in self._transport.processed_event_keys.items()
                if self._timestamp_to_epoch(seen_at) >= threshold
            }
            if entry_key in self._transport.processed_event_keys:
                return False
            self._transport.processed_event_keys[entry_key] = now.isoformat()
            self._save()
            return True

    def clear_pending(self, request_id: str, *, status: str = "resolved") -> PendingInteraction | None:
        with self._lock:
            pending = self._pending.pop(request_id, None)
            if pending is None:
                return None
            session = self._sessions.get(pending.session_key)
            if session:
                session.pending_request_id = None
                if session.state in {"waiting_approval", "waiting_input"}:
                    session.state = "running"
                session.last_status = status
                session.recovery_note = None
            self._save()
            return PendingInteraction(
                request_id=pending.request_id,
                kind=pending.kind,
                session_key=pending.session_key,
                conversation=pending.conversation,
                title=pending.title,
                prompt=pending.prompt,
                created_at=pending.created_at,
                command=pending.command,
                codex_thread_id=pending.codex_thread_id,
                codex_turn_id=pending.codex_turn_id,
                codex_item_id=pending.codex_item_id,
                metadata=dict(pending.metadata),
                status=status,
            )

    def pending_for_conversation(self, conversation: ConversationRef) -> PendingInteraction | None:
        with self._lock:
            session = self._sessions.get(conversation.session_key)
            if not session or not session.pending_request_id:
                return None
            pending = self._pending.get(session.pending_request_id)
            if pending and pending.status == "pending":
                return pending
            return None

    def recover_orphans(self) -> None:
        with self._lock:
            for pending in self._pending.values():
                if pending.status == "pending":
                    pending.metadata.setdefault("recovery_reason", "service restart")
            for session in self._sessions.values():
                if session.state == "running":
                    session.state = "failed"
                    session.last_status = "Service restarted while a turn was in progress."
                    session.recovery_note = "服务重启中断了上一轮执行；请重新发起该任务。"
                if session.state == "waiting_approval":
                    session.last_status = "服务已重启：待确认操作仍保留，收到你的确认后会尝试在原线程继续执行。"
                    session.recovery_note = "服务已重启：待确认操作仍保留，收到你的确认后会尝试在原线程继续执行。"
                if session.state == "waiting_input":
                    session.last_status = "服务已重启：待补充信息仍保留，收到你的回复后会尝试在原线程继续执行。"
                    session.recovery_note = "服务已重启：待补充信息仍保留，收到你的回复后会尝试在原线程继续执行。"
            self._save()

    def _load(self) -> None:
        if not self._path.exists():
            return
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        self._sessions = {
            key: ConversationSession.from_dict(value)
            for key, value in payload.get("sessions", {}).items()
        }
        self._bindings = {
            key: WorkspaceBinding.from_dict(value)
            for key, value in payload.get("bindings", {}).items()
        }
        self._pending = {
            key: PendingInteraction.from_dict(value)
            for key, value in payload.get("pending", {}).items()
        }
        transport_payload = payload.get("transport")
        if isinstance(transport_payload, dict):
            self._transport = FeishuTransportState.from_dict(transport_payload)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "updated_at": datetime.now(UTC).isoformat(),
            "sessions": {key: session.to_dict() for key, session in self._sessions.items()},
            "bindings": {key: binding.to_dict() for key, binding in self._bindings.items()},
            "pending": {key: pending.to_dict() for key, pending in self._pending.items()},
            "transport": self._transport.to_dict(),
        }
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _normalize_workspace(workspace_path: str) -> str:
        return str(Path(workspace_path).expanduser().resolve())

    @staticmethod
    def _timestamp_to_epoch(value: str) -> float:
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return 0.0

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SessionState = Literal["idle", "running", "waiting_approval", "waiting_input", "failed"]
PendingKind = Literal["approval", "user_input"]
SubmissionDecision = Literal["approve", "deny"]
TransportStatus = Literal["stopped", "connecting", "connected", "reconnecting", "failed"]
ProgressMilestone = Literal["accepted", "running", "waiting_approval", "waiting_input", "completed", "failed"]
ApprovalCardStatus = Literal["pending", "approved", "denied", "expired", "duplicate", "error"]


@dataclass(frozen=True)
class ConversationRef:
    channel: str
    account_id: str
    conversation_id: str
    thread_id: str | None = None

    @property
    def session_key(self) -> str:
        suffix = f":{self.thread_id}" if self.thread_id else ""
        return f"{self.channel}:{self.account_id}:{self.conversation_id}{suffix}"

    def binding_key(self, workspace_path: str) -> str:
        return f"{self.session_key}@{workspace_path}"


@dataclass(frozen=True)
class Actor:
    user_id: str
    display_name: str | None = None
    chat_type: str | None = None


@dataclass(frozen=True)
class InboundMessage:
    conversation: ConversationRef
    actor: Actor
    text: str
    source_message_id: str | None = None
    reply_to_message_id: str | None = None


@dataclass(frozen=True)
class PendingSubmission:
    conversation: ConversationRef
    actor: Actor
    request_id: str
    kind: PendingKind
    text: str | None = None
    decision: SubmissionDecision | None = None
    codex_thread_id: str | None = None
    codex_turn_id: str | None = None
    codex_item_id: str | None = None
    open_message_id: str | None = None


class ProgressUpdate(str):
    __slots__ = ("milestone", "summary", "detail")

    milestone: ProgressMilestone
    summary: str
    detail: str | None

    def __new__(
        cls,
        milestone: ProgressMilestone,
        summary: str,
        detail: str | None = None,
    ) -> "ProgressUpdate":
        instance = str.__new__(cls, summary)
        instance.milestone = milestone
        instance.summary = summary
        instance.detail = detail
        return instance


@dataclass
class ConversationSession:
    channel: str
    account_id: str
    conversation_id: str
    thread_id: str | None
    active_workspace: str
    state: SessionState = "idle"
    pending_request_id: str | None = None
    last_status: str | None = None
    last_source_message_id: str | None = None
    progress_message_id: str | None = None
    progress_milestone: ProgressMilestone | None = None
    recovery_note: str | None = None

    @property
    def key(self) -> str:
        suffix = f":{self.thread_id}" if self.thread_id else ""
        return f"{self.channel}:{self.account_id}:{self.conversation_id}{suffix}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ConversationSession":
        return cls(**payload)


@dataclass
class WorkspaceBinding:
    session_key: str
    workspace_path: str
    agent_thread_id: str | None = None

    @property
    def key(self) -> str:
        return f"{self.session_key}@{self.workspace_path}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkspaceBinding":
        return cls(**payload)


@dataclass
class ApprovalRequest:
    request_id: str
    conversation: ConversationRef
    workspace_path: str
    method: str
    command: str | None = None
    cwd: str | None = None
    reason: str | None = None
    item_id: str | None = None
    turn_id: str | None = None
    codex_thread_id: str | None = None
    grant_root: str | None = None
    file_paths: list[str] = field(default_factory=list)
    permissions: dict[str, Any] = field(default_factory=dict)


@dataclass
class InputRequest:
    request_id: str
    conversation: ConversationRef
    questions: list[dict[str, Any]]
    item_id: str | None = None
    turn_id: str | None = None
    codex_thread_id: str | None = None

    def prompt_text(self) -> str:
        rendered: list[str] = []
        for index, question in enumerate(self.questions, start=1):
            label = question.get("question") or question.get("label") or question.get("prompt")
            if isinstance(label, str) and label.strip():
                rendered.append(f"{index}. {label.strip()}")
        return "\n".join(rendered) if rendered else "请补充执行所需信息。"


@dataclass
class PendingInteraction:
    request_id: str
    kind: PendingKind
    session_key: str
    conversation: ConversationRef
    title: str
    prompt: str
    created_at: str
    command: str | None = None
    codex_thread_id: str | None = None
    codex_turn_id: str | None = None
    codex_item_id: str | None = None
    approval_message_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["conversation"] = asdict(self.conversation)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PendingInteraction":
        payload = dict(payload)
        payload["conversation"] = ConversationRef(**payload["conversation"])
        return cls(**payload)


@dataclass(frozen=True)
class TurnOutcome:
    thread_id: str
    summary: str
    status: Literal["completed", "failed", "interrupted"]
    raw_text: str = ""
    error: str | None = None


@dataclass
class FeishuTransportState:
    mode: str = "websocket"
    status: TransportStatus = "stopped"
    last_connected_at: str | None = None
    last_disconnected_at: str | None = None
    last_error: str | None = None
    reconnect_attempts: int = 0
    processed_event_keys: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FeishuTransportState":
        return cls(**payload)

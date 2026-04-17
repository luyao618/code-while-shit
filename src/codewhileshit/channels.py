from __future__ import annotations

import abc
from dataclasses import dataclass

from .models import ApprovalCardStatus, ConversationRef, ProgressUpdate


@dataclass(frozen=True)
class ApprovalPrompt:
    request_id: str
    title: str
    prompt: str
    command: str | None = None
    reason: str | None = None
    cwd: str | None = None
    method: str | None = None
    codex_thread_id: str | None = None
    codex_turn_id: str | None = None
    codex_item_id: str | None = None


@dataclass(frozen=True)
class InputPrompt:
    request_id: str
    title: str
    prompt: str
    codex_thread_id: str | None = None
    codex_turn_id: str | None = None
    codex_item_id: str | None = None


class ChannelAdapter(abc.ABC):
    @abc.abstractmethod
    def send_status(self, conversation: ConversationRef, text: str) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def send_result(self, conversation: ConversationRef, text: str) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def request_approval(self, conversation: ConversationRef, prompt: ApprovalPrompt) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def request_user_input(self, conversation: ConversationRef, prompt: InputPrompt) -> None:
        raise NotImplementedError

    def acknowledge_message(self, conversation: ConversationRef, *, source_message_id: str | None) -> bool:
        return False

    def upsert_progress(
        self,
        conversation: ConversationRef,
        update: ProgressUpdate,
        *,
        message_id: str | None = None,
        reply_to_message_id: str | None = None,
        source_message_id: str | None = None,
    ) -> str | None:
        detail = f"\n{update.detail}" if update.detail else ""
        self.send_status(conversation, f"{update.summary}{detail}")
        return None

    def resolve_approval(
        self,
        conversation: ConversationRef,
        prompt: ApprovalPrompt,
        *,
        message_id: str | None,
        status: ApprovalCardStatus,
        detail: str | None = None,
    ) -> bool:
        return False

from __future__ import annotations

import abc
from dataclasses import dataclass

from .models import ConversationRef


@dataclass(frozen=True)
class ApprovalPrompt:
    request_id: str
    title: str
    prompt: str
    command: str | None = None
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

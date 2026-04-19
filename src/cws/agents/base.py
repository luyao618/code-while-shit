from __future__ import annotations

import threading
from enum import Enum
from typing import TYPE_CHECKING, ClassVar, Callable, Iterator, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..models import ApprovalRequest, ConversationRef, InputRequest, TurnOutcome


class TurnState(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    KILLED = "killed"


class CancelNotSupported(Exception):
    pass


@runtime_checkable
class AgentTurn(Protocol):
    state: TurnState
    kill_event: threading.Event
    supports_cancel: bool
    supports_approval: bool

    def __enter__(self) -> "AgentTurn":
        ...

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        ...

    def run(self) -> "TurnOutcome":
        ...

    def cancel(self) -> None:
        ...

    def deltas(self) -> Iterator[str]:
        ...


@runtime_checkable
class AgentBackend(Protocol):
    agent_type: ClassVar[str]

    def begin_turn(
        self,
        conversation: "ConversationRef",
        workspace_path: str,
        prompt: str,
        existing_thread_id: str | None,
        request_approval: Callable[["ApprovalRequest"], str],
        request_input: Callable[["InputRequest"], str],
        publish_status: Callable[[str], None],
    ) -> AgentTurn:
        ...

    def kill(self) -> None:
        ...

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable

from ..models import (
    ApprovalRequest,
    ConversationRef,
    InputRequest,
    ProgressUpdate,
    TurnOutcome,
)
from .base import AgentBackend, AgentTurn, CancelNotSupported, TurnState

JsonDict = dict[str, Any]


class ClaudeCodeImportError(ImportError):
    def __init__(self):
        super().__init__(
            "claude-agent-sdk is not installed. "
            "Install with: pip install 'vibe-coding-while-shit[claude]' "
            "or: pip install claude-agent-sdk"
        )


def _import_sdk():
    try:
        import claude_agent_sdk  # noqa: F401

        return claude_agent_sdk
    except ImportError as e:
        raise ClaudeCodeImportError() from e


class ClaudeCodeAgentTurn(AgentTurn):
    agent_type = "claude-code"
    supports_cancel = True
    supports_approval = True

    CANCEL_LATENCY_S = 3.0

    def __init__(
        self,
        *,
        backend: "ClaudeCodeAgentBackend",
        conversation: ConversationRef,
        workspace_path: str,
        prompt: str,
        existing_thread_id: str | None,
        request_approval: Callable[[ApprovalRequest], str],
        request_input: Callable[[InputRequest], str],
        publish_status: Callable[[Any], None],
    ):
        self._backend = backend
        self._conversation = conversation
        self._workspace_path = workspace_path
        self._prompt = prompt
        self._existing_thread_id = existing_thread_id
        self._request_approval = request_approval
        self._request_input = request_input
        self._publish_status = publish_status
        self.state = TurnState.RUNNING
        self.kill_event = threading.Event()
        self._cancel_event = threading.Event()
        self._summary_chunks: list[str] = []

    def __enter__(self) -> "ClaudeCodeAgentTurn":
        self.state = TurnState.RUNNING
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.state == TurnState.RUNNING:
            try:
                self.cancel()
            except CancelNotSupported:
                pass
        return False

    def cancel(self) -> None:
        if self.state != TurnState.RUNNING:
            return
        self.state = TurnState.CANCELLED
        self._cancel_event.set()

    def deltas(self):
        # Optional; streaming already piped through publish_status
        raise NotImplementedError("Use publish_status for streaming")

    def run(self) -> TurnOutcome:
        sdk = _import_sdk()
        text_parts: list[str] = []
        thread_id = self._existing_thread_id or f"cc-{id(self)}"

        self._publish_status(ProgressUpdate("running", "处理中：claude-code 正在执行任务。"))

        nonlocal_status: list[tuple[str, str | None]] = []

        async def _drive() -> str:
            status = "completed"
            error = None
            try:
                async for event in self._iter_sdk(sdk):
                    if self._cancel_event.is_set():
                        status = "interrupted"
                        break
                    self._handle_event(event, text_parts)
                self._publish_status(
                    ProgressUpdate(
                        "completed" if status == "completed" else "failed",
                        "已完成" if status == "completed" else "已中断",
                    )
                )
            except Exception as e:
                status = "failed"
                error = str(e)
                self._publish_status(ProgressUpdate("failed", f"失败：{e}"))
            nonlocal_status.append((status, error))
            return "".join(text_parts)

        loop = asyncio.new_event_loop()
        summary: str = ""
        try:
            main_task = loop.create_task(_drive())

            def _watch():
                if self._cancel_event.wait(timeout=None):
                    if not main_task.done():
                        loop.call_soon_threadsafe(main_task.cancel)

            t = threading.Thread(target=_watch, daemon=True)
            t.start()
            try:
                summary = loop.run_until_complete(main_task)
            except asyncio.CancelledError:
                summary = "".join(text_parts)
        finally:
            try:
                loop.close()
            except Exception:
                pass

        status, error = nonlocal_status[0] if nonlocal_status else ("completed", None)
        if self.state == TurnState.CANCELLED or status == "interrupted":
            outcome_status = "interrupted"
        elif status == "failed":
            outcome_status = "failed"
        else:
            outcome_status = "completed"
            self.state = TurnState.COMPLETED

        return TurnOutcome(
            thread_id=thread_id,
            summary=summary.strip() or ("执行完成。" if outcome_status == "completed" else "执行结束。"),
            status=outcome_status,
            raw_text=summary,
            error=error,
        )

    async def _iter_sdk(self, sdk):
        """Iterate SDK events. Handles both streaming async generator and compat fallback."""
        query = getattr(sdk, "query", None)
        if query is not None:
            opts_cls = getattr(sdk, "ClaudeAgentOptions", None)
            options = opts_cls(cwd=self._workspace_path) if opts_cls else None
            async for msg in query(prompt=self._prompt, options=options):
                yield msg
            return
        # Fallback: try ClaudeSDKClient async context manager
        client_cls = getattr(sdk, "ClaudeSDKClient", None)
        if client_cls is not None:
            async with client_cls() as client:
                await client.query(self._prompt)
                async for msg in client.receive_response():
                    yield msg
            return
        raise RuntimeError(
            "claude_agent_sdk surface not recognized; expected query() or ClaudeSDKClient"
        )

    def _handle_event(self, event: Any, text_parts: list[str]) -> None:
        text = None
        if hasattr(event, "text"):
            text = event.text
        elif hasattr(event, "content"):
            c = event.content
            if isinstance(c, str):
                text = c
            elif isinstance(c, list):
                text = "".join(
                    getattr(b, "text", "")
                    if hasattr(b, "text")
                    else str(b.get("text", ""))
                    if isinstance(b, dict)
                    else ""
                    for b in c
                )
        elif isinstance(event, dict):
            text = event.get("text") or event.get("content")
        if text:
            text_parts.append(text)
            self._publish_status(ProgressUpdate("running", text))


class ClaudeCodeAgentBackend(AgentBackend):
    agent_type = "claude-code"

    def __init__(self, config):
        self._config = config

    def begin_turn(
        self,
        conversation: ConversationRef,
        workspace_path: str,
        prompt: str,
        existing_thread_id: str | None,
        request_approval: Callable[[ApprovalRequest], str],
        request_input: Callable[[InputRequest], str],
        publish_status: Callable[[Any], None],
    ) -> ClaudeCodeAgentTurn:
        return ClaudeCodeAgentTurn(
            backend=self,
            conversation=conversation,
            workspace_path=workspace_path,
            prompt=prompt,
            existing_thread_id=existing_thread_id,
            request_approval=request_approval,
            request_input=request_input,
            publish_status=publish_status,
        )

    def kill(self) -> None:
        # Claude-code has no persistent subprocess under our control; nothing to kill.
        return None

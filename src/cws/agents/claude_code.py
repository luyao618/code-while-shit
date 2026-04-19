from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
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
            "Install with: pip install 'code-while-shit[claude]' "
            "or: pip install claude-agent-sdk"
        )


def _import_sdk():
    try:
        import claude_agent_sdk  # noqa: F401

        return claude_agent_sdk
    except ImportError as e:
        raise ClaudeCodeImportError() from e


class _LoopThread:
    """Owns a dedicated asyncio loop on a background thread.

    `ClaudeSDKClient` requires that connect()/query()/disconnect() all execute
    in the same async runtime context (it holds a persistent anyio task group
    from connect to disconnect). Our service spawns turns from worker threads
    and previously created a fresh `asyncio.new_event_loop()` per turn, which
    made cross-turn client reuse impossible. This helper hosts a single loop
    that lives for the backend's lifetime so one `ClaudeSDKClient` per
    conversation can stay connected and accumulate context across turns.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()

    def _ensure_started(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._thread is not None and self._thread.is_alive():
                return self._loop
            loop = asyncio.new_event_loop()
            self._loop = loop

            def _run() -> None:
                asyncio.set_event_loop(loop)
                self._ready.set()
                try:
                    loop.run_forever()
                finally:
                    try:
                        loop.close()
                    except Exception:
                        pass

            self._ready.clear()
            self._thread = threading.Thread(
                target=_run, name="claude-code-sdk-loop", daemon=True
            )
            self._thread.start()
            self._ready.wait()
            return loop

    def run(self, coro) -> Any:
        loop = self._ensure_started()
        fut: Future = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result()

    def submit(self, coro) -> Future:
        loop = self._ensure_started()
        return asyncio.run_coroutine_threadsafe(coro, loop)

    def shutdown(self) -> None:
        with self._lock:
            loop = self._loop
            thread = self._thread
            self._loop = None
            self._thread = None
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)


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
        # session_id observed from the SDK during this turn; persisted as
        # the binding's agent_thread_id (mostly for diagnostics now).
        self._observed_session_id: str | None = None
        # Future representing the in-flight receive_response loop on the SDK
        # loop thread. Used by cancel() to interrupt the client.
        self._drain_future: Future | None = None

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
        # Ask the SDK client to interrupt the in-flight turn so receive_response
        # finishes promptly instead of waiting for the model to finish.
        client = self._backend._peek_client(self._conversation)
        if client is not None:
            try:
                self._backend._loop.submit(client.interrupt())
            except Exception:
                pass

    def deltas(self):
        # Optional; streaming already piped through publish_status
        raise NotImplementedError("Use publish_status for streaming")

    def run(self) -> TurnOutcome:
        sdk = _import_sdk()
        text_parts: list[str] = []

        self._publish_status(ProgressUpdate("running", "处理中：claude-code 正在执行任务。"))

        status: str = "completed"
        error: str | None = None
        try:
            client = self._backend._get_or_connect_client(sdk, self._conversation, self._workspace_path)

            async def _drive() -> None:
                await client.query(self._prompt)
                async for event in client.receive_response():
                    if self._cancel_event.is_set():
                        return
                    self._handle_event(event, text_parts)

            self._drain_future = self._backend._loop.submit(_drive())
            try:
                self._drain_future.result()
            except Exception as exc:
                # If cancelled via interrupt() the SDK may raise. Distinguish
                # by checking our cancel flag.
                if self._cancel_event.is_set():
                    status = "interrupted"
                else:
                    status = "failed"
                    error = str(exc)
                    self._publish_status(ProgressUpdate("failed", f"失败：{exc}"))
            else:
                if self._cancel_event.is_set():
                    status = "interrupted"
        except Exception as exc:
            # Connection failure or import-time error.
            status = "failed"
            error = str(exc)
            self._publish_status(ProgressUpdate("failed", f"失败：{exc}"))

        # Map to AgentTurn outcome status and emit a final progress update so
        # the Feishu card flips out of "running".
        if self.state == TurnState.CANCELLED or status == "interrupted":
            outcome_status = "interrupted"
            self._publish_status(ProgressUpdate("failed", "已中断"))
        elif status == "failed":
            outcome_status = "failed"
        else:
            outcome_status = "completed"
            self.state = TurnState.COMPLETED
            self._publish_status(ProgressUpdate("completed", "已完成"))

        summary = "".join(text_parts)
        # thread_id is informational only when using the persistent client —
        # context lives in the live ClaudeSDKClient, not in --resume. We
        # persist the observed session_id so /status can show something
        # meaningful and so debugging / migration paths have an id to use.
        thread_id = self._observed_session_id or self._existing_thread_id or f"cc-{id(self)}"
        return TurnOutcome(
            thread_id=thread_id,
            summary=summary.strip() or ("执行完成。" if outcome_status == "completed" else "执行结束。"),
            status=outcome_status,
            raw_text=summary,
            error=error,
        )

    def _handle_event(self, event: Any, text_parts: list[str]) -> None:
        # Capture session_id from any event that carries one.
        if self._observed_session_id is None:
            sid = getattr(event, "session_id", None)
            if not sid and isinstance(event, dict):
                sid = event.get("session_id")
            if not sid:
                data = getattr(event, "data", None)
                if isinstance(data, dict):
                    sid = data.get("session_id")
            if isinstance(sid, str) and sid.strip():
                self._observed_session_id = sid.strip()

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
        # One persistent ClaudeSDKClient per (conversation_key, workspace_path).
        # Keyed by string so we don't pin ConversationRef instances. The client
        # is responsible for retaining conversation context across turns — no
        # --resume needed because the underlying CLI subprocess never exits
        # between turns.
        self._clients: dict[str, Any] = {}
        self._clients_lock = threading.Lock()
        self._loop = _LoopThread()

    @staticmethod
    def _client_key(conversation: ConversationRef, workspace_path: str) -> str:
        return f"{conversation.channel}:{conversation.account_id}:{conversation.conversation_id}@{workspace_path}"

    def _peek_client(self, conversation: ConversationRef) -> Any | None:
        # Best-effort lookup used by cancel(); workspace is unknown here so we
        # match by conversation prefix and return the first hit.
        prefix = f"{conversation.channel}:{conversation.account_id}:{conversation.conversation_id}@"
        with self._clients_lock:
            for key, client in self._clients.items():
                if key.startswith(prefix):
                    return client
        return None

    def _get_or_connect_client(self, sdk, conversation: ConversationRef, workspace_path: str) -> Any:
        key = self._client_key(conversation, workspace_path)
        with self._clients_lock:
            existing = self._clients.get(key)
            if existing is not None:
                return existing

        opts_cls = getattr(sdk, "ClaudeAgentOptions", None)
        client_cls = getattr(sdk, "ClaudeSDKClient", None)
        if client_cls is None:
            raise RuntimeError(
                "claude_agent_sdk.ClaudeSDKClient not available; persistent "
                "session mode requires a recent claude-agent-sdk."
            )
        options = opts_cls(cwd=workspace_path) if opts_cls else None

        async def _connect():
            client = client_cls(options=options) if options is not None else client_cls()
            await client.connect()
            return client

        client = self._loop.run(_connect())

        with self._clients_lock:
            # Race: another turn may have just connected one; prefer the
            # earliest by disconnecting our duplicate.
            existing = self._clients.get(key)
            if existing is not None:
                # Throw away the one we just made.
                async def _disc(c):
                    try:
                        await c.disconnect()
                    except Exception:
                        pass
                try:
                    self._loop.submit(_disc(client))
                except Exception:
                    pass
                return existing
            self._clients[key] = client
            return client

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
        # /kill or /clear: drop all live clients so the next turn starts a
        # fresh CLI subprocess (and therefore a fresh conversation).
        with self._clients_lock:
            clients = list(self._clients.values())
            self._clients.clear()

        async def _disconnect_all():
            for c in clients:
                try:
                    await c.disconnect()
                except Exception:
                    pass

        try:
            self._loop.run(_disconnect_all())
        except Exception:
            pass

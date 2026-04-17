from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from .config import CodexConfig
from .models import ApprovalRequest, ConversationRef, InputRequest, TurnOutcome

JsonDict = dict[str, Any]
RequestHandler = Callable[[JsonDict], Any | None]
NotificationHandler = Callable[[JsonDict], None]


class CodexBackend(Protocol):
    def process_turn(
        self,
        conversation: ConversationRef,
        workspace_path: str,
        prompt: str,
        existing_thread_id: str | None,
        request_approval: Callable[[ApprovalRequest], str],
        request_input: Callable[[InputRequest], str],
        publish_status: Callable[[str], None],
    ) -> TurnOutcome:
        ...


class CodexRpcError(RuntimeError):
    pass


class CodexAppServerClient:
    def __init__(self, config: CodexConfig):
        self._config = config
        self._process: subprocess.Popen[str] | None = None
        self._pending: dict[int, queue.Queue[Any]] = {}
        self._request_handlers: list[RequestHandler] = []
        self._notification_handlers: list[NotificationHandler] = []
        self._lock = threading.RLock()
        self._next_id = 1
        self._reader_thread: threading.Thread | None = None

    def start(self) -> None:
        if self._process is not None:
            return
        self._process = subprocess.Popen(
            [self._config.command, *self._config.app_server_args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._reader_thread = threading.Thread(target=self._reader_loop, name="codex-app-server-reader", daemon=True)
        self._reader_thread.start()
        self.initialize()

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()

    def initialize(self) -> JsonDict:
        return self.request(
            "initialize",
            {
                "clientInfo": {"name": "code-while-shit", "title": "code-while-shit", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )

    def request(self, method: str, params: JsonDict | None = None, timeout: float = 30.0) -> Any:
        self.start()
        assert self._process and self._process.stdin
        request_id = self._next_request_id()
        waiter: queue.Queue[Any] = queue.Queue(maxsize=1)
        with self._lock:
            self._pending[request_id] = waiter
        self._process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}) + "\n")
        self._process.stdin.flush()
        try:
            result = waiter.get(timeout=timeout)
        except queue.Empty as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            raise TimeoutError(f"{method} timed out") from exc
        if isinstance(result, Exception):
            raise result
        return result

    def notify(self, method: str, params: JsonDict | None = None) -> None:
        self.start()
        assert self._process and self._process.stdin
        self._process.stdin.write(json.dumps({"method": method, "params": params}) + "\n")
        self._process.stdin.flush()

    def add_request_handler(self, handler: RequestHandler) -> Callable[[], None]:
        self._request_handlers.append(handler)
        return lambda: self._request_handlers.remove(handler)

    def add_notification_handler(self, handler: NotificationHandler) -> Callable[[], None]:
        self._notification_handlers.append(handler)
        return lambda: self._notification_handlers.remove(handler)

    def _next_request_id(self) -> int:
        with self._lock:
            current = self._next_id
            self._next_id += 1
        return current

    def _reader_loop(self) -> None:
        assert self._process and self._process.stdout and self._process.stdin
        for line in self._process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in message and "method" not in message:
                self._handle_response(message)
            elif "id" in message and "method" in message:
                threading.Thread(target=self._handle_server_request, args=(message,), daemon=True).start()
            elif "method" in message:
                for handler in list(self._notification_handlers):
                    handler(message)

    def _handle_response(self, response: JsonDict) -> None:
        with self._lock:
            waiter = self._pending.pop(response["id"], None)
        if waiter is None:
            return
        if response.get("error"):
            waiter.put(CodexRpcError(response["error"].get("message", "request failed")))
            return
        waiter.put(response.get("result"))

    def _handle_server_request(self, request: JsonDict) -> None:
        result: Any = None
        handled = False
        for handler in list(self._request_handlers):
            candidate = handler(request)
            if candidate is not None:
                result = candidate
                handled = True
                break
        if not handled:
            result = {}
        assert self._process and self._process.stdin
        self._process.stdin.write(json.dumps({"id": request["id"], "result": result}) + "\n")
        self._process.stdin.flush()


@dataclass
class TurnTracker:
    thread_id: str
    turn_id: str | None = None
    summary_chunks: list[str] = field(default_factory=list)
    completion: threading.Event = field(default_factory=threading.Event)
    outcome: TurnOutcome | None = None
    buffered_notifications: list[JsonDict] = field(default_factory=list)
    last_status: str | None = None

    def append_text(self, delta: str) -> None:
        if delta:
            self.summary_chunks.append(delta)

    @property
    def text(self) -> str:
        return "".join(self.summary_chunks).strip()


class TurnMilestoneUpdate(str):
    __slots__ = ("milestone", "text")

    milestone: str
    text: str

    def __new__(cls, milestone: str, text: str) -> "TurnMilestoneUpdate":
        instance = str.__new__(cls, text)
        instance.milestone = milestone
        instance.text = text
        return instance


class CodexAppServerBackend:
    def __init__(self, config: CodexConfig, client: CodexAppServerClient | None = None):
        self._config = config
        self._client = client or CodexAppServerClient(config)
        self._turn_lock = threading.RLock()

    def process_turn(
        self,
        conversation: ConversationRef,
        workspace_path: str,
        prompt: str,
        existing_thread_id: str | None,
        request_approval: Callable[[ApprovalRequest], str],
        request_input: Callable[[InputRequest], str],
        publish_status: Callable[[str], None],
    ) -> TurnOutcome:
        with self._turn_lock:
            self._client.start()
            thread_id = self._start_or_resume_thread(existing_thread_id, workspace_path)
            tracker = TurnTracker(thread_id=thread_id)
            request_cleanup = self._client.add_request_handler(
                lambda request: self._handle_server_request(
                    request,
                    conversation=conversation,
                    workspace_path=workspace_path,
                    tracker=tracker,
                    request_approval=request_approval,
                    request_input=request_input,
                    publish_status=publish_status,
                )
            )
            notification_cleanup = self._client.add_notification_handler(
                lambda notification: self._handle_notification(notification, tracker, publish_status)
            )
            try:
                publish_status(TurnMilestoneUpdate("running", "处理中：Codex 正在执行任务。"))
                turn_result = self._client.request(
                    "turn/start",
                    {
                        "threadId": thread_id,
                        "input": [{"type": "text", "text": prompt}],
                        "cwd": workspace_path,
                        "approvalPolicy": self._config.approval_policy,
                        "approvalsReviewer": self._config.approvals_reviewer,
                        "model": self._config.model,
                        **({"serviceTier": self._config.service_tier} if self._config.service_tier else {}),
                    },
                    timeout=60.0,
                )
                tracker.turn_id = turn_result["turn"]["id"]
                for buffered in list(tracker.buffered_notifications):
                    self._handle_notification(buffered, tracker, publish_status)
                if not tracker.completion.wait(timeout=300.0):
                    raise TimeoutError("turn did not complete in time")
                if tracker.outcome is None:
                    raise RuntimeError("turn completed without outcome")
                return tracker.outcome
            finally:
                request_cleanup()
                notification_cleanup()

    def _start_or_resume_thread(self, existing_thread_id: str | None, workspace_path: str) -> str:
        if existing_thread_id:
            try:
                response = self._client.request(
                    "thread/resume",
                    {
                        "threadId": existing_thread_id,
                        "model": self._config.model,
                        "approvalPolicy": self._config.approval_policy,
                        "approvalsReviewer": self._config.approvals_reviewer,
                        "sandbox": self._config.sandbox,
                    },
                    timeout=30.0,
                )
                return response["thread"]["id"]
            except Exception:
                pass
        response = self._client.request(
            "thread/start",
            {
                "model": self._config.model,
                "cwd": workspace_path,
                "approvalPolicy": self._config.approval_policy,
                "approvalsReviewer": self._config.approvals_reviewer,
                "sandbox": self._config.sandbox,
                "serviceName": "code-while-shit",
                "experimentalRawEvents": True,
                "persistExtendedHistory": True,
            },
            timeout=30.0,
        )
        return response["thread"]["id"]

    def _handle_notification(self, notification: JsonDict, tracker: TurnTracker, publish_status: Callable[[str], None]) -> None:
        method = notification.get("method")
        params = notification.get("params") or {}
        if tracker.turn_id is None:
            tracker.buffered_notifications.append(notification)
            return
        if params.get("threadId") and params.get("threadId") != tracker.thread_id:
            return
        turn_id = params.get("turnId") or (params.get("turn") or {}).get("id")
        if turn_id and turn_id != tracker.turn_id:
            return
        if method == "item/agentMessage/delta":
            tracker.append_text(str(params.get("delta") or ""))
            return
        if method in {"thread/statusChanged", "turn/statusChanged"}:
            update = self._status_update_from_notification(params)
            status_key = f"{update.milestone}:{update.text}" if update else None
            if update and status_key != tracker.last_status:
                tracker.last_status = status_key
                publish_status(update)
            return
        if method == "turn/completed":
            turn = params.get("turn") or {}
            summary = self._extract_summary(turn, tracker.text)
            status = turn.get("status") or "completed"
            error = None
            if status == "failed":
                error = ((turn.get("error") or {}).get("message") if isinstance(turn.get("error"), dict) else None) or summary or "Codex turn failed"
            tracker.outcome = TurnOutcome(
                thread_id=tracker.thread_id,
                summary=summary or ("执行完成。" if status == "completed" else "执行结束。"),
                status=status if status in {"completed", "failed", "interrupted"} else "completed",
                raw_text=tracker.text,
                error=error,
            )
            tracker.completion.set()

    def _status_update_from_notification(self, params: JsonDict) -> TurnMilestoneUpdate | None:
        status = (
            params.get("status")
            or (params.get("thread") or {}).get("status")
            or (params.get("turn") or {}).get("status")
        )
        if not isinstance(status, str):
            return None
        normalized = status.strip().lower()
        if normalized in {"running", "in_progress", "working"}:
            return TurnMilestoneUpdate("running", "处理中：Codex 正在执行任务。")
        if normalized in {"waiting_input", "needs_input"}:
            return TurnMilestoneUpdate("waiting_input", "等待补充信息：Codex 需要你的进一步说明。")
        if normalized in {"waiting_approval", "needs_approval"}:
            return TurnMilestoneUpdate("waiting_approval", "等待确认：Codex 需要你的确认。")
        if normalized in {"completed", "done"}:
            return TurnMilestoneUpdate("completed", "已完成：Codex 已结束当前执行。")
        if normalized in {"failed", "error"}:
            return TurnMilestoneUpdate("failed", "失败：Codex 执行未完成。")
        return None

    def _extract_summary(self, turn: JsonDict, fallback: str) -> str:
        items = turn.get("items") or []
        if isinstance(items, list):
            for item in reversed(items):
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "agentMessage" and isinstance(item.get("text"), str) and item.get("text", "").strip():
                    return item["text"].strip()
        return fallback.strip()

    def _handle_server_request(
        self,
        request: JsonDict,
        *,
        conversation: ConversationRef,
        workspace_path: str,
        tracker: TurnTracker,
        request_approval: Callable[[ApprovalRequest], str],
        request_input: Callable[[InputRequest], str],
        publish_status: Callable[[str], None],
    ) -> Any | None:
        method = request.get("method")
        params = request.get("params") or {}
        if tracker.turn_id and params.get("turnId") and params.get("turnId") != tracker.turn_id:
            return None
        if method == "item/tool/requestUserInput":
            publish_status(TurnMilestoneUpdate("waiting_input", "等待补充信息：Codex 需要你的进一步说明。"))
            input_request = InputRequest(
                request_id=str(request["id"]),
                conversation=conversation,
                questions=list(params.get("questions") or []),
                item_id=params.get("itemId"),
                turn_id=params.get("turnId"),
                codex_thread_id=params.get("threadId") or tracker.thread_id,
            )
            answer = request_input(input_request)
            return {"answers": self._answer_payload(input_request.questions, answer)}
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "item/permissions/requestApproval",
        }:
            approval = ApprovalRequest(
                request_id=str(request["id"]),
                conversation=conversation,
                workspace_path=workspace_path,
                method=method,
                command=params.get("command"),
                cwd=params.get("cwd"),
                reason=params.get("reason"),
                item_id=params.get("itemId"),
                turn_id=params.get("turnId"),
                codex_thread_id=params.get("threadId") or tracker.thread_id,
                grant_root=params.get("grantRoot"),
                file_paths=list(params.get("filePaths") or params.get("paths") or []),
                permissions=dict(params.get("permissions") or {}),
            )
            decision = request_approval(approval)
            publish_status(TurnMilestoneUpdate("running", "处理中：已收到你的确认，继续执行。"))
            return self._approval_response(method, params, decision)
        return None

    def _approval_response(self, method: str, params: JsonDict, decision: str) -> JsonDict:
        approved = decision == "approve"
        if method == "item/commandExecution/requestApproval":
            return {"decision": "allow_once" if approved else "deny"}
        if method == "item/fileChange/requestApproval":
            return {"decision": "accept" if approved else "decline"}
        requested = params.get("permissions") or {}
        return {"permissions": requested if approved else {}, "scope": "turn"}

    def _answer_payload(self, questions: list[dict[str, Any]], answer: str) -> dict[str, str]:
        if not questions:
            return {"response": answer}
        first = questions[0]
        key = str(first.get("id") or "response")
        return {key: answer}

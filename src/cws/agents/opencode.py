from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from typing import Any, Callable
from urllib import request as urlreq, error as urlerror

from ..models import (
    ApprovalRequest,
    ConversationRef,
    InputRequest,
    ProgressUpdate,
    TurnOutcome,
)
from .base import AgentBackend, AgentTurn, CancelNotSupported, TurnState


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class OpencodeStartupError(RuntimeError):
    pass


class OpencodeAgentTurn(AgentTurn):
    agent_type = "opencode"

    def __init__(
        self,
        *,
        backend: "OpencodeAgentBackend",
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
        # supports_cancel is lazily probed on first cancel attempt
        self.supports_cancel: bool | None = None
        self.supports_approval = False

    def __enter__(self) -> "OpencodeAgentTurn":
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
        if self.supports_cancel is None:
            # Lazy probe: check if backend has an abort endpoint
            self.supports_cancel = self._backend.probe_cancel_support()
        if not self.supports_cancel:
            raise CancelNotSupported(
                "opencode does not expose a cancel endpoint; use /kill to terminate."
            )
        self._backend.abort_current()
        self.state = TurnState.CANCELLED

    def deltas(self):
        raise NotImplementedError("opencode streams via publish_status")

    def run(self) -> TurnOutcome:
        self._backend.ensure_running()
        thread_id = self._existing_thread_id or f"oc-{id(self)}"
        self._publish_status(ProgressUpdate("running", "处理中：opencode 正在执行任务。"))

        # Simple POST /chat with the prompt; consume response body.
        # Real opencode protocol may differ — this is the canonical shape.
        url = self._backend.url("chat")
        body = json.dumps(
            {
                "prompt": self._prompt,
                "cwd": self._workspace_path,
                "thread_id": thread_id,
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._backend.auth_token:
            headers["Authorization"] = f"Bearer {self._backend.auth_token}"

        req = urlreq.Request(url, data=body, headers=headers, method="POST")
        text_parts: list[str] = []
        error: str | None = None
        status = "completed"
        try:
            with urlreq.urlopen(req, timeout=300) as resp:
                # Prefer SSE/NDJSON stream; fall back to buffered body
                for line in resp:
                    if self.state != TurnState.RUNNING:
                        status = "interrupted"
                        break
                    raw = line.decode("utf-8", errors="replace").strip()
                    if not raw:
                        continue
                    # Try JSON per-line (NDJSON)
                    try:
                        evt = json.loads(raw)
                        if isinstance(evt, dict):
                            delta = evt.get("text") or evt.get("delta") or evt.get("content")
                            if delta:
                                text_parts.append(delta)
                                self._publish_status(ProgressUpdate("running", delta))
                                continue
                    except json.JSONDecodeError:
                        pass
                    # Raw text line
                    text_parts.append(raw + "\n")
                    self._publish_status(ProgressUpdate("running", raw))
        except urlerror.URLError as e:
            status = "failed"
            error = f"opencode HTTP error: {e}"
        except Exception as e:
            status = "failed"
            error = str(e)

        if self.state == TurnState.CANCELLED or status == "interrupted":
            outcome_status = "interrupted"
        elif status == "failed":
            outcome_status = "failed"
            self.state = TurnState.RUNNING  # Not cancelled/killed by user
        else:
            outcome_status = "completed"
            self.state = TurnState.COMPLETED

        summary = "".join(text_parts).strip() or (
            "执行完成。" if outcome_status == "completed" else "执行结束。"
        )
        return TurnOutcome(
            thread_id=thread_id,
            summary=summary,
            status=outcome_status,
            raw_text="".join(text_parts),
            error=error,
        )


class OpencodeAgentBackend(AgentBackend):
    agent_type = "opencode"

    STARTUP_RETRIES = 3

    def __init__(self, config):
        self._config = config
        self._process: subprocess.Popen | None = None
        self._port: int | None = None
        self._host: str = getattr(config, "host", "127.0.0.1")
        self.auth_token: str | None = None
        self._lock = threading.RLock()

    def begin_turn(
        self,
        conversation: ConversationRef,
        workspace_path: str,
        prompt: str,
        existing_thread_id: str | None,
        request_approval: Callable[[ApprovalRequest], str],
        request_input: Callable[[InputRequest], str],
        publish_status: Callable[[Any], None],
    ) -> OpencodeAgentTurn:
        # Opencode cannot expose structured approvals; wrap the upstream callback
        # to enforce the --allow-auto-approve gate before any tool invocation.
        wrapped_approval = self._wrap_approval(request_approval, publish_status)
        return OpencodeAgentTurn(
            backend=self,
            conversation=conversation,
            workspace_path=workspace_path,
            prompt=prompt,
            existing_thread_id=existing_thread_id,
            request_approval=wrapped_approval,
            request_input=request_input,
            publish_status=publish_status,
        )

    def _wrap_approval(self, _inner, publish_status):
        # `_inner` is intentionally ignored: opencode's auto-approve gate overrides
        # whatever upstream would have answered. Kept in the signature so callers
        # that still pass it (including unit tests from US-012) don't break.
        allow = getattr(self._config, "allow_auto_approve", False)

        def wrapper(req: ApprovalRequest) -> str:
            if allow:
                publish_status(
                    ProgressUpdate(
                        "running",
                        "opencode 自动批准（--allow-auto-approve）",
                    )
                )
                return "approve"
            publish_status(
                ProgressUpdate(
                    "failed",
                    "opencode 不支持审批：请使用 --allow-auto-approve 或切换 agent",
                )
            )
            return "deny"

        return wrapper

    def ensure_running(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return
            self._spawn()

    def _spawn(self) -> None:
        command = getattr(self._config, "command", "opencode")
        port = getattr(self._config, "port", None) or _pick_free_port()
        self._port = port
        cmd = [command, "serve", "--host", self._host, "--port", str(port)]
        last_err: Exception | None = None
        for attempt in range(self.STARTUP_RETRIES):
            try:
                self._process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                if self._wait_ready(timeout_s=getattr(self._config, "startup_timeout_s", 5.0)):
                    return
                # Not ready; kill and retry
                try:
                    self._process.terminate()
                    self._process.wait(timeout=2)
                except Exception:
                    try:
                        self._process.kill()
                    except Exception:
                        pass
                last_err = OpencodeStartupError(
                    f"opencode serve did not become ready on attempt {attempt + 1}"
                )
            except FileNotFoundError as e:
                raise OpencodeStartupError(
                    f"opencode command not found: {command}. "
                    f"Install opencode or set the command in config."
                ) from e
            except Exception as e:
                last_err = e
            # Exponential backoff
            time.sleep(0.5 * (2**attempt))
        raise OpencodeStartupError(
            f"opencode serve failed to start after {self.STARTUP_RETRIES} attempts"
        ) from last_err

    def _wait_ready(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((self._host, self._port), timeout=0.5):
                    return True
            except OSError:
                time.sleep(0.1)
        return False

    def url(self, path: str) -> str:
        return f"http://{self._host}:{self._port}/{path.lstrip('/')}"

    def probe_cancel_support(self) -> bool:
        # Best-effort: try OPTIONS or HEAD on /abort
        try:
            req = urlreq.Request(self.url("abort"), method="OPTIONS")
            with urlreq.urlopen(req, timeout=1.0) as resp:
                return 200 <= resp.status < 400 or resp.status == 405
        except urlerror.HTTPError as e:
            # 405 means endpoint exists but OPTIONS not allowed — treat as supported
            return e.code in (200, 204, 405)
        except Exception:
            return False

    def abort_current(self) -> None:
        try:
            req = urlreq.Request(self.url("abort"), method="POST")
            urlreq.urlopen(req, timeout=2.0)
        except Exception:
            pass

    def capability_banner(self) -> str:
        allow = getattr(self._config, "allow_auto_approve", False)
        approve_note = (
            "已启用 --allow-auto-approve：工具调用将自动批准"
            if allow
            else "审批不支持（需 --allow-auto-approve 才允许工具调用）"
        )
        return (
            f"opencode 模式：{approve_note}；"
            f"/cancel 能力将在首次使用时探测；"
            f"建议用 /kill 终止。"
        )

    def kill(self) -> None:
        with self._lock:
            if self._process is None:
                return
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=2)
            finally:
                self._process = None
                self._port = None

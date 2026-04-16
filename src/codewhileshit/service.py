from __future__ import annotations

import concurrent.futures
import threading
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from .channels import ApprovalPrompt, ChannelAdapter, InputPrompt
from .codex_app_server import CodexAppServerBackend, CodexBackend
from .config import AppConfig
from .models import (
    Actor,
    ApprovalRequest,
    ConversationRef,
    ConversationSession,
    InboundMessage,
    InputRequest,
    PendingInteraction,
    PendingSubmission,
    WorkspaceBinding,
)
from .policy import ApprovalPolicy
from .state import StateStore


class BridgeService:
    def __init__(
        self,
        config: AppConfig,
        adapter: ChannelAdapter,
        backend: CodexBackend | None = None,
        state_store: StateStore | None = None,
        policy: ApprovalPolicy | None = None,
    ):
        self.config = config
        self.adapter = adapter
        self.state = state_store or StateStore(config.state_file)
        self.policy = policy or ApprovalPolicy()
        self.backend = backend or CodexAppServerBackend(config.codex)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="bridge")
        self._conversation_locks: dict[str, threading.Lock] = {}
        self._pending_events: dict[str, tuple[threading.Event, dict[str, str]]] = {}
        self._pending_lock = threading.RLock()
        self.state.recover_orphans()

    def update_transport_state(self, **updates: object) -> None:
        self.state.update_transport_state(**updates)

    def should_accept_transport_event(self, kind: str, key: str | None) -> bool:
        return self.state.should_accept_transport_event(kind, key)

    def handle_message(self, message: InboundMessage) -> None:
        if not self._is_allowed(message.actor):
            self.adapter.send_result(message.conversation, "当前用户未在 allowlist 中，已拒绝执行。")
            return
        session = self.state.get_session(message.conversation)
        pending = self.state.pending_for_conversation(message.conversation)
        if pending is not None:
            submission = self._submission_from_message(message, pending)
            if submission is None:
                self.adapter.send_status(message.conversation, "当前正在等待确认或补充信息，请先完成该步骤。")
                return
            self.handle_submission(submission)
            return
        if message.text.strip().startswith("/workspace "):
            self._handle_workspace_switch(message)
            return
        if message.text.strip() == "/status":
            self._handle_status(message.conversation)
            return
        if session and session.recovery_note:
            self.state.save_session(
                replace(session, state="idle", last_status=session.recovery_note, recovery_note=None)
            )
            self.adapter.send_result(message.conversation, session.recovery_note)
            return
        session = session or self.state.ensure_session(message.conversation, str(self.config.default_workspace))
        if session.state == "running":
            self.adapter.send_status(message.conversation, "已有任务在执行，请等待当前任务结束。")
            return
        self._executor.submit(self._run_turn, message, session)

    def handle_submission(self, submission: PendingSubmission) -> None:
        if not self._is_allowed(submission.actor):
            self.adapter.send_result(submission.conversation, "当前用户未在 allowlist 中，已拒绝处理该确认。")
            return
        pending = self.state.get_pending(submission.request_id)
        if pending is None or pending.status != "pending":
            target = pending.conversation if pending is not None else submission.conversation
            self.adapter.send_result(target, "这条确认/补充信息已经过期或不存在。")
            return
        if not self._submission_matches_pending(submission, pending):
            self.adapter.send_result(pending.conversation, "这条确认缺少匹配的上下文信息，请重新触发该操作。")
            return
        with self._pending_lock:
            waiter = self._pending_events.get(submission.request_id)
        if waiter is None:
            session = self.state.get_session(pending.conversation)
            if session is None:
                self.adapter.send_result(pending.conversation, "等待中的执行上下文已经失效，请重新发起任务。")
                return
            self.state.clear_pending(submission.request_id, status="recovered")
            self.adapter.send_status(pending.conversation, "服务已重启：已收到你的回复，正在原线程尝试恢复执行。")
            self._executor.submit(self._resume_pending_turn, session, pending, submission)
            return
        event, slot = waiter
        if submission.kind == "approval":
            slot["value"] = submission.decision or "deny"
        else:
            slot["value"] = (submission.text or "").strip()
        self.state.clear_pending(submission.request_id)
        event.set()
        self.adapter.send_status(pending.conversation, "已收到你的回复，继续执行。")

    def _run_turn(self, message: InboundMessage, session: ConversationSession) -> None:
        lock = self._conversation_locks.setdefault(session.key, threading.Lock())
        with lock:
            session = replace(
                self.state.ensure_session(message.conversation, session.active_workspace),
                state="running",
                last_status="处理中",
                last_source_message_id=message.source_message_id,
            )
            self.state.save_session(session)
            binding = self.state.ensure_binding(message.conversation, session.active_workspace)
            try:
                def publish_status(text: str) -> None:
                    current = self.state.get_session(message.conversation) or session
                    next_state = current.state
                    if "等待确认" in text:
                        next_state = "waiting_approval"
                    elif "等待补充" in text:
                        next_state = "waiting_input"
                    elif "处理" in text or "继续执行" in text:
                        next_state = "running"
                    self.state.save_session(replace(current, state=next_state, last_status=text))
                    self.adapter.send_status(message.conversation, text)

                outcome = self.backend.process_turn(
                    conversation=message.conversation,
                    workspace_path=session.active_workspace,
                    prompt=message.text.strip(),
                    existing_thread_id=binding.codex_thread_id,
                    request_approval=lambda request: self._request_approval(session, message.actor, request),
                    request_input=lambda request: self._request_input(session, message.actor, request),
                    publish_status=publish_status,
                )
                final_session = replace(
                    session,
                    state="idle" if outcome.status == "completed" else "failed",
                    last_status=outcome.status,
                    pending_request_id=None,
                )
                self.state.save_session(final_session)
                self.state.save_binding(WorkspaceBinding(session_key=session.key, workspace_path=session.active_workspace, codex_thread_id=outcome.thread_id))
                if outcome.status == "completed":
                    self.adapter.send_result(message.conversation, outcome.summary)
                else:
                    self.adapter.send_result(message.conversation, outcome.error or outcome.summary)
            except Exception as exc:
                errored = replace(session, state="failed", last_status=str(exc), pending_request_id=None)
                self.state.save_session(errored)
                self.adapter.send_result(message.conversation, f"执行失败：{exc}")

    def _request_approval(self, session: ConversationSession, actor: Actor, request: ApprovalRequest) -> str:
        decision = self.policy.evaluate(request)
        if decision.action == "auto-approve":
            return "approve"
        request_id = request.request_id or f"approval-{uuid.uuid4().hex}"
        pending = PendingInteraction(
            request_id=request_id,
            kind="approval",
            session_key=session.key,
            conversation=request.conversation,
            title="需要确认的操作",
            prompt=self._render_approval_prompt(request, decision.reason),
            created_at=datetime.now(UTC).isoformat(),
            command=request.command,
            codex_thread_id=request.codex_thread_id,
            codex_turn_id=request.turn_id,
            codex_item_id=request.item_id,
            metadata={"reason": decision.reason, "actor_user_id": actor.user_id},
        )
        self.state.set_pending(pending)
        self.adapter.request_approval(
            request.conversation,
            ApprovalPrompt(
                request_id=request_id,
                title=pending.title,
                prompt=pending.prompt,
                command=request.command,
                codex_thread_id=request.codex_thread_id,
                codex_turn_id=request.turn_id,
                codex_item_id=request.item_id,
            ),
        )
        return self._wait_for_pending_value(request_id, default="deny")

    def _request_input(self, session: ConversationSession, actor: Actor, request: InputRequest) -> str:
        request_id = request.request_id or f"input-{uuid.uuid4().hex}"
        pending = PendingInteraction(
            request_id=request_id,
            kind="user_input",
            session_key=session.key,
            conversation=request.conversation,
            title="Codex 需要补充信息",
            prompt=request.prompt_text(),
            created_at=datetime.now(UTC).isoformat(),
            codex_thread_id=request.codex_thread_id,
            codex_turn_id=request.turn_id,
            codex_item_id=request.item_id,
            metadata={"questions": request.questions, "actor_user_id": actor.user_id},
        )
        self.state.set_pending(pending)
        self.adapter.request_user_input(
            request.conversation,
            InputPrompt(
                request_id=request_id,
                title=pending.title,
                prompt=pending.prompt,
                codex_thread_id=request.codex_thread_id,
                codex_turn_id=request.turn_id,
                codex_item_id=request.item_id,
            ),
        )
        return self._wait_for_pending_value(request_id, default="")

    def _wait_for_pending_value(self, request_id: str, default: str) -> str:
        event = threading.Event()
        slot: dict[str, str] = {}
        with self._pending_lock:
            self._pending_events[request_id] = (event, slot)
        try:
            if not event.wait(timeout=300):
                pending = self.state.get_pending(request_id)
                if pending is not None:
                    pending.status = "expired"
                return default
            return slot.get("value", default)
        finally:
            with self._pending_lock:
                self._pending_events.pop(request_id, None)

    def _resume_pending_turn(
        self,
        session: ConversationSession,
        pending: PendingInteraction,
        submission: PendingSubmission,
    ) -> None:
        response = submission.decision or submission.text or ""
        if pending.kind == "approval":
            prompt = (
                "The bridge service restarted while waiting for a user approval request.\n"
                f"User decision: {response}.\n"
                f"Original request summary:\n{pending.prompt}\n\n"
                "Continue the task on this thread using that decision. "
                "If a sensitive action is still needed, request fresh approval."
            )
        else:
            prompt = (
                "The bridge service restarted while waiting for user input.\n"
                f"User answer: {response}\n"
                f"Original question summary:\n{pending.prompt}\n\n"
                "Continue the task on this thread with this new information."
            )
        recovery_message = InboundMessage(
            conversation=pending.conversation,
            actor=submission.actor,
            text=prompt,
        )
        recovered_session = replace(session, recovery_note=None)
        self._run_turn(recovery_message, recovered_session)

    def _render_approval_prompt(self, request: ApprovalRequest, reason: str) -> str:
        parts = [f"原因：{reason}"]
        if request.command:
            parts.append(f"命令：`{request.command}`")
        if request.cwd:
            parts.append(f"cwd：`{request.cwd}`")
        if request.grant_root:
            parts.append(f"路径：`{request.grant_root}`")
        if request.reason:
            parts.append(f"Codex 描述：{request.reason}")
        parts.append("确认后将继续执行；拒绝则本轮任务停止。")
        return "\n".join(parts)

    def _is_allowed(self, actor: Actor) -> bool:
        allowed = self.config.feishu.allowed_user_ids
        return not allowed or actor.user_id in allowed

    def _handle_workspace_switch(self, message: InboundMessage) -> None:
        requested = message.text.strip().split(" ", 1)[1].strip()
        workspace = str(Path(requested).expanduser().resolve())
        Path(workspace).mkdir(parents=True, exist_ok=True)
        session = self.state.ensure_session(message.conversation, workspace)
        updated = replace(session, active_workspace=workspace, state="idle", last_status=f"workspace -> {workspace}")
        self.state.save_session(updated)
        self.state.ensure_binding(message.conversation, workspace)
        self.adapter.send_result(message.conversation, f"已切换工作目录到：{workspace}")

    def _handle_status(self, conversation: ConversationRef) -> None:
        session = self.state.get_session(conversation)
        transport = self.state.get_transport_state()
        status = [f"Feishu Transport：{transport.mode} / {transport.status}"]
        if transport.last_connected_at:
            status.append(f"上次连接：{transport.last_connected_at}")
        if transport.last_disconnected_at:
            status.append(f"上次断开：{transport.last_disconnected_at}")
        if transport.last_error:
            status.append(f"Transport Error：{transport.last_error}")
        if session is None:
            status.append("当前会话还没有活跃的 Codex 线程。")
            self.adapter.send_result(conversation, "\n".join(status))
            return
        binding = self.state.get_binding(conversation, session.active_workspace)
        status.extend([f"状态：{session.state}", f"工作目录：{session.active_workspace}"])
        if binding and binding.codex_thread_id:
            status.append(f"Codex Thread：{binding.codex_thread_id}")
        if session.last_status:
            status.append(f"最近事件：{session.last_status}")
        if session.recovery_note:
            status.append(f"恢复提示：{session.recovery_note}")
        self.adapter.send_result(conversation, "\n".join(status))

    def _submission_from_message(self, message: InboundMessage, pending: PendingInteraction) -> PendingSubmission | None:
        text = message.text.strip()
        if pending.kind == "approval":
            lowered = text.lower()
            if lowered in {"yes", "y", "approve", "同意", "确认", "允许"}:
                return PendingSubmission(
                    message.conversation,
                    message.actor,
                    pending.request_id,
                    "approval",
                    decision="approve",
                    codex_thread_id=pending.codex_thread_id,
                    codex_turn_id=pending.codex_turn_id,
                    codex_item_id=pending.codex_item_id,
                )
            if lowered in {"no", "n", "deny", "拒绝", "取消"}:
                return PendingSubmission(
                    message.conversation,
                    message.actor,
                    pending.request_id,
                    "approval",
                    decision="deny",
                    codex_thread_id=pending.codex_thread_id,
                    codex_turn_id=pending.codex_turn_id,
                    codex_item_id=pending.codex_item_id,
                )
            return None
        return PendingSubmission(
            message.conversation,
            message.actor,
            pending.request_id,
            "user_input",
            text=text,
            codex_thread_id=pending.codex_thread_id,
            codex_turn_id=pending.codex_turn_id,
            codex_item_id=pending.codex_item_id,
        )

    @staticmethod
    def _submission_matches_pending(submission: PendingSubmission, pending: PendingInteraction) -> bool:
        expected_actor = str(pending.metadata.get("actor_user_id") or "")
        if expected_actor and submission.actor.user_id != expected_actor:
            return False
        if submission.conversation.account_id != pending.conversation.account_id:
            return False
        if submission.conversation.conversation_id != pending.conversation.conversation_id:
            return False
        pending_thread = pending.conversation.thread_id or ""
        submission_thread = submission.conversation.thread_id or ""
        if pending_thread != submission_thread:
            return False
        if submission.codex_thread_id and pending.codex_thread_id and submission.codex_thread_id != pending.codex_thread_id:
            return False
        if submission.codex_turn_id and pending.codex_turn_id and submission.codex_turn_id != pending.codex_turn_id:
            return False
        if submission.codex_item_id and pending.codex_item_id and submission.codex_item_id != pending.codex_item_id:
            return False
        return True

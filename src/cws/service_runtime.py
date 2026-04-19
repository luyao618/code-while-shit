from __future__ import annotations

import inspect
from dataclasses import replace
from typing import Any

from .channels import ApprovalPrompt, ChannelAdapter
from .models import (
    ApprovalCardStatus,
    ConversationRef,
    ConversationSession,
    InboundMessage,
    PendingInteraction,
    ProgressUpdate,
)
from .state import StateStore


def replace_session(session: ConversationSession, **changes: object) -> ConversationSession:
    return replace(session, **changes)


def extract_message_handle(result: object) -> str | None:
    if isinstance(result, str) and result.strip():
        return result.strip()
    if isinstance(result, dict):
        for key in ("message_id", "id", "handle"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def call_with_supported_kwargs(method: Any, *args: object, **kwargs: object) -> Any:
    signature = inspect.signature(method)
    supports_var_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    filtered = {
        key: value
        for key, value in kwargs.items()
        if (supports_var_kwargs or key in signature.parameters)
        and (value is not None or key in signature.parameters)
    }
    return method(*args, **filtered)


def session_state_for_milestone(milestone: str, fallback: str) -> str:
    if milestone in {"accepted", "thinking", "running"}:
        return "running"
    if milestone == "waiting_approval":
        return "waiting_approval"
    if milestone == "waiting_input":
        return "waiting_input"
    if milestone == "completed":
        return "idle"
    if milestone == "failed":
        return "failed"
    return fallback


def milestone_text(milestone: str, text: str | None = None) -> str:
    if text and text.strip():
        return text.strip()
    defaults = {
        "accepted": "已收到：消息已进入处理队列。",
        "thinking": "正在思考：Codex 正在分析你的请求。",
        "running": "正在处理：Codex 正在执行任务。",
        "waiting_input": "等待补充信息：Codex 需要你的进一步说明。",
        "waiting_approval": "等待确认：Codex 需要你的确认。",
        "completed": "已完成：Codex 已结束当前执行。",
        "failed": "失败：Codex 执行未完成。",
    }
    return defaults.get(milestone, "正在处理：Codex 正在执行任务。")


def normalize_progress_update(update: object) -> tuple[str, str]:
    if isinstance(update, ProgressUpdate):
        text = update.summary
        if update.detail:
            text = f"{text}\n{update.detail}"
        return update.milestone, text
    if isinstance(update, str):
        text = update.strip()
        lowered = text.lower()
        if "已收到" in text:
            return "accepted", text
        if "等待确认" in text:
            return "waiting_approval", text
        if "等待补充" in text:
            return "waiting_input", text
        if "已完成" in text:
            return "completed", text
        if "失败" in text:
            return "failed", text
        if "思考" in text or "thinking" in lowered:
            return "thinking", text
        if "处理" in text or "继续执行" in text or "running" in lowered:
            return "running", text
        return "running", text or milestone_text("running")
    return "running", milestone_text("running")


def final_progress_text(status: str, detail: str | None = None) -> str:
    if status == "completed":
        return "已完成：Codex 已结束当前执行。"
    if status == "interrupted":
        return "已停止：Codex 中断了当前执行。"
    return detail.strip() if detail and detail.strip() else "失败：Codex 执行未完成。"


class ProgressSurfaceManager:
    def __init__(self, state: StateStore, adapter: ChannelAdapter):
        self._state = state
        self._adapter = adapter

    def attempt_ack(self, message: InboundMessage) -> bool:
        if not message.source_message_id:
            return False
        try:
            return bool(
                call_with_supported_kwargs(
                    self._adapter.acknowledge_message,
                    message.conversation,
                    source_message_id=message.source_message_id,
                )
            )
        except Exception:
            return False

    def publish(
        self,
        conversation: ConversationRef,
        session: ConversationSession,
        milestone: str,
        text: str,
        *,
        final: bool = False,
        detail: str | None = None,
    ) -> ConversationSession:
        existing_handle = getattr(session, "progress_message_id", None)
        if (
            getattr(session, "progress_milestone", None) == milestone
            and session.last_status == text
            and not final
            and not detail
        ):
            self._state.save_session(session)
            return session
        updated = replace_session(
            session,
            progress_milestone=milestone,
            last_status=text,
            state=session_state_for_milestone(milestone, session.state),
        )
        self._state.save_session(updated)
        progress = ProgressUpdate(milestone, text, detail)
        result = call_with_supported_kwargs(
            self._adapter.upsert_progress,
            conversation,
            progress,
            message_id=existing_handle,
            reply_to_message_id=updated.last_source_message_id,
            source_message_id=updated.last_source_message_id,
        )
        handle = extract_message_handle(result)
        if handle:
            updated = replace_session(updated, progress_message_id=handle)
        elif existing_handle:
            updated = replace_session(updated, progress_message_id=existing_handle)
        self._state.save_session(updated)
        return updated

    def resolve_pending_surface(self, pending: PendingInteraction, status: str, detail: str) -> None:
        approval_handle = getattr(pending, "approval_message_id", None)
        prompt = ApprovalPrompt(
            request_id=pending.request_id,
            title=pending.title,
            prompt=pending.prompt,
            command=pending.command,
            reason=str(pending.metadata.get("reason") or "") or None,
            codex_thread_id=pending.codex_thread_id,
            codex_turn_id=pending.codex_turn_id,
            codex_item_id=pending.codex_item_id,
        )
        normalized_status: ApprovalCardStatus = "approved" if status == "approve" else "denied"
        call_with_supported_kwargs(
            self._adapter.resolve_approval,
            pending.conversation,
            prompt,
            message_id=approval_handle,
            status=normalized_status,
            detail=detail,
        )

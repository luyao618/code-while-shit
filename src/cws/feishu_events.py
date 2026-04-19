from __future__ import annotations

import json
from typing import Any

from .models import Actor, ConversationRef, InboundMessage, PendingSubmission


def _event_attr(value: Any, *path: str) -> Any:
    current = value
    for name in path:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(name)
        else:
            current = getattr(current, name, None)
    return current


def _parse_message_event(event: Any) -> InboundMessage | None:
    content = _event_attr(event, "event", "message", "content") or "{}"
    text = _extract_text(str(content))
    if not text:
        return None
    conversation = ConversationRef(
        channel="feishu",
        account_id="default",
        conversation_id=str(_event_attr(event, "event", "message", "chat_id") or ""),
        thread_id=_event_attr(event, "event", "message", "thread_id")
        or _event_attr(event, "event", "message", "root_id"),
    )
    return InboundMessage(
        conversation=conversation,
        actor=Actor(
            user_id=str(
                _event_attr(event, "event", "sender", "sender_id", "open_id")
                or _event_attr(event, "event", "sender", "sender_id", "user_id")
                or ""
            ),
            display_name=None,
            chat_type=_event_attr(event, "event", "message", "chat_type"),
        ),
        text=text,
        source_message_id=_event_attr(event, "event", "message", "message_id"),
        reply_to_message_id=_event_attr(event, "event", "message", "parent_id")
        or _event_attr(event, "event", "message", "message_id"),
    )


def _message_dedupe_key(event: Any) -> str | None:
    return _event_attr(event, "header", "event_id") or _event_attr(event, "event", "message", "message_id")


def _card_action_dedupe_key(event: Any) -> str | None:
    header_key = _event_attr(event, "header", "event_id")
    if header_key:
        return str(header_key)
    action_token = _event_attr(event, "event", "token")
    open_message_id = _event_attr(event, "event", "context", "open_message_id")
    request_id = _event_attr(event, "event", "action", "value", "request_id")
    combo = ":".join(str(part) for part in [action_token, open_message_id, request_id] if part)
    return combo or None


def _extract_text(content: str) -> str:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content.strip()
    if isinstance(payload, dict):
        text = payload.get("text")
        if isinstance(text, str):
            return text.strip()
    return ""


def _parse_card_action_submission(event: Any) -> PendingSubmission | None:
    value = _event_attr(event, "event", "action", "value")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {"request_id": value}
    if not isinstance(value, dict):
        return None
    request_id = value.get("request_id")
    decision = value.get("decision")
    if not isinstance(request_id, str) or decision not in {"approve", "deny"}:
        return None
    conversation = ConversationRef(
        channel="feishu",
        account_id=str(value.get("account_id") or "default"),
        conversation_id=str(
            value.get("conversation_id")
            or _event_attr(event, "event", "context", "open_chat_id")
            or "interactive"
        ),
        thread_id=str(value.get("thread_id")) if value.get("thread_id") else None,
    )
    return PendingSubmission(
        conversation=conversation,
        actor=Actor(
            user_id=str(
                _event_attr(event, "event", "operator", "open_id")
                or _event_attr(event, "event", "operator", "user_id")
                or ""
            )
        ),
        request_id=request_id,
        kind="approval",
        decision=decision,
        codex_thread_id=str(value.get("codex_thread_id")) if value.get("codex_thread_id") else None,
        codex_turn_id=str(value.get("codex_turn_id")) if value.get("codex_turn_id") else None,
        codex_item_id=str(value.get("codex_item_id")) if value.get("codex_item_id") else None,
        open_message_id=str(_event_attr(event, "event", "context", "open_message_id") or "") or None,
    )

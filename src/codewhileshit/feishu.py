from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Callable

from .channels import ApprovalPrompt, ChannelAdapter, InputPrompt
from .config import AppConfig, FeishuConfig
from .models import Actor, ConversationRef, InboundMessage, PendingSubmission


def lark_sdk_available() -> bool:
    try:
        _load_lark_sdk()
        return True
    except ImportError:
        return False


@lru_cache(maxsize=1)
def _load_lark_sdk() -> dict[str, Any]:
    from lark_oapi.core.enum import LogLevel
    from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
    from lark_oapi.ws.client import Client as FeishuWSClient

    return {
        "LogLevel": LogLevel,
        "EventDispatcherHandler": EventDispatcherHandler,
        "FeishuWSClient": FeishuWSClient,
        "P2CardActionTriggerResponse": P2CardActionTriggerResponse,
    }


class FeishuApiClient:
    def __init__(self, config: FeishuConfig):
        self._config = config
        self._token_lock = threading.RLock()
        self._tenant_token: str | None = None
        self._expires_at = 0.0

    def send_text(self, conversation: ConversationRef, text: str) -> None:
        content = json.dumps({"text": text}, ensure_ascii=False)
        self._send_message(conversation, "text", content)

    def send_card(self, conversation: ConversationRef, card: dict[str, Any]) -> None:
        content = json.dumps(card, ensure_ascii=False)
        self._send_message(conversation, "interactive", content)

    def _send_message(self, conversation: ConversationRef, msg_type: str, content: str) -> None:
        token = self._tenant_access_token()
        payload = {
            "receive_id": conversation.conversation_id,
            "msg_type": msg_type,
            "content": content,
        }
        query = urllib.parse.urlencode({"receive_id_type": "chat_id"})
        url = f"{self._config.base_url}/im/v1/messages?{query}"
        request = urllib.request.Request(
            url,
            method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        self._read_json(request)

    def _tenant_access_token(self) -> str:
        with self._token_lock:
            now = time.time()
            if self._tenant_token and now < self._expires_at - 30:
                return self._tenant_token
            if not self._config.app_id or not self._config.app_secret:
                raise RuntimeError("Feishu app credentials are required for outbound API calls")
            request = urllib.request.Request(
                f"{self._config.base_url}/auth/v3/tenant_access_token/internal",
                method="POST",
                data=json.dumps(
                    {"app_id": self._config.app_id, "app_secret": self._config.app_secret},
                    ensure_ascii=False,
                ).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            payload = self._read_json(request)
            token = payload["tenant_access_token"]
            expires_in = int(payload.get("expire", 7200))
            self._tenant_token = token
            self._expires_at = now + expires_in
            return token

    @staticmethod
    def _read_json(request: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Feishu API error {exc.code}: {detail}") from exc


class FeishuAdapter(ChannelAdapter):
    def __init__(self, client: FeishuApiClient):
        self._client = client

    def send_status(self, conversation: ConversationRef, text: str) -> None:
        self._client.send_text(conversation, f"[状态] {text}")

    def send_result(self, conversation: ConversationRef, text: str) -> None:
        self._client.send_text(conversation, text)

    def request_approval(self, conversation: ConversationRef, prompt: ApprovalPrompt) -> None:
        context = {
            "request_id": prompt.request_id,
            "decision": "approve",
            "conversation_id": conversation.conversation_id,
            "account_id": conversation.account_id,
            "thread_id": conversation.thread_id,
            "codex_thread_id": prompt.codex_thread_id,
            "codex_turn_id": prompt.codex_turn_id,
            "codex_item_id": prompt.codex_item_id,
        }
        deny_context = {**context, "decision": "deny"}
        card = {
            "schema": "2.0",
            "config": {"width_mode": "fill"},
            "header": {"title": {"tag": "plain_text", "content": prompt.title}, "template": "orange"},
            "body": {
                "elements": [
                    {"tag": "markdown", "content": prompt.prompt},
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "确认"},
                                "type": "primary",
                                "value": json.dumps(context, ensure_ascii=False),
                            },
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "拒绝"},
                                "value": json.dumps(deny_context, ensure_ascii=False),
                            },
                        ],
                    },
                ]
            },
        }
        self._client.send_card(conversation, card)

    def request_user_input(self, conversation: ConversationRef, prompt: InputPrompt) -> None:
        self._client.send_text(
            conversation,
            f"[需要补充信息] {prompt.title}\n{prompt.prompt}\n\n请直接回复你的答案。",
        )


class FeishuWebSocketGateway:
    def __init__(
        self,
        config: AppConfig,
        on_message: Callable[[InboundMessage], None],
        on_submission: Callable[[PendingSubmission], None],
        on_transport_state: Callable[..., Any],
        accept_transport_event: Callable[[str, str | None], bool],
    ):
        self._config = config
        self._on_message = on_message
        self._on_submission = on_submission
        self._on_transport_state = on_transport_state
        self._accept_transport_event = accept_transport_event
        self._client: Any | None = None

    def serve_forever(self) -> None:
        if not self._config.feishu.app_id or not self._config.feishu.app_secret:
            raise RuntimeError("Feishu websocket mode requires FEISHU_APP_ID and FEISHU_APP_SECRET")
        sdk = _load_lark_sdk()
        EventDispatcherHandler = sdk["EventDispatcherHandler"]
        FeishuWSClient = sdk["FeishuWSClient"]
        LogLevel = sdk["LogLevel"]

        gateway = self

        class TrackingFeishuWSClient(FeishuWSClient):
            async def _connect(self_inner):
                gateway._publish_transport_state(status="connecting")
                await super()._connect()
                gateway._publish_transport_state(
                    status="connected",
                    last_connected_at=_utc_now(),
                    last_error="",
                )

            async def _try_connect(self_inner, cnt: int):
                gateway._publish_transport_state(status="reconnecting", reconnect_attempts=cnt + 1)
                return await super()._try_connect(cnt)

            async def _disconnect(self_inner):
                await super()._disconnect()
                gateway._publish_transport_state(status="stopped", last_disconnected_at=_utc_now())

        dispatcher = (
            EventDispatcherHandler.builder("", "", LogLevel.INFO)
            .register_p2_im_message_receive_v1(self._handle_message_event)
            .register_p2_card_action_trigger(self._handle_card_action_event)
            .build()
        )
        self._publish_transport_state(status="connecting")
        self._client = TrackingFeishuWSClient(
            self._config.feishu.app_id,
            self._config.feishu.app_secret,
            event_handler=dispatcher,
            domain=self._config.feishu.domain,
            auto_reconnect=True,
        )
        try:
            self._client.start()
        except Exception as exc:  # pragma: no cover - exercised in smoke/manual usage
            self._publish_transport_state(
                status="failed",
                last_disconnected_at=_utc_now(),
                last_error=str(exc),
            )
            raise RuntimeError(f"Feishu websocket transport failed: {exc}") from exc

    def shutdown(self) -> None:
        self._publish_transport_state(status="stopped", last_disconnected_at=_utc_now())

    def _handle_message_event(self, event: Any) -> None:
        self._publish_transport_state(status="connected", last_error="")
        dedupe_key = _message_dedupe_key(event)
        if not self._accept_transport_event("message", dedupe_key):
            return
        message = _parse_message_event(event)
        if message is None:
            return
        self._on_message(message)

    def _handle_card_action_event(self, event: Any) -> Any:
        self._publish_transport_state(status="connected", last_error="")
        dedupe_key = _card_action_dedupe_key(event)
        if not self._accept_transport_event("card_action", dedupe_key):
            return _card_action_response("info", "该操作已处理。")
        submission = _parse_card_action_submission(event)
        if submission is None:
            return _card_action_response("error", "卡片动作无效。")
        try:
            self._on_submission(submission)
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            self._publish_transport_state(status="failed", last_error=str(exc))
            return _card_action_response("error", f"处理失败：{exc}")
        return _card_action_response("success", "已提交")

    def _publish_transport_state(self, **updates: Any) -> None:
        self._on_transport_state(mode="websocket", **updates)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _card_action_response(level: str, content: str) -> Any:
    response_type = _load_lark_sdk()["P2CardActionTriggerResponse"]
    return response_type({"toast": {"type": level, "content": content}})


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
    )

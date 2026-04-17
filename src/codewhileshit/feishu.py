from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Callable

from .channels import ApprovalPrompt, ChannelAdapter, InputPrompt
from .config import AppConfig, FeishuConfig
from .feishu_cards import _build_approval_card, _build_progress_card
from .feishu_events import (
    _card_action_dedupe_key,
    _extract_text,
    _message_dedupe_key,
    _parse_card_action_submission,
    _parse_message_event,
)
from .models import (
    ApprovalCardStatus,
    ConversationRef,
    InboundMessage,
    PendingSubmission,
    ProgressUpdate,
)

__all__ = [
    "FeishuAdapter",
    "FeishuApiClient",
    "FeishuWebSocketGateway",
    "lark_sdk_available",
    "_build_approval_card",
    "_build_progress_card",
    "_extract_text",
    "_parse_card_action_submission",
    "_parse_message_event",
]


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

    def send_text(
        self,
        conversation: ConversationRef,
        text: str,
        *,
        reply_to_message_id: str | None = None,
    ) -> str | None:
        content = json.dumps({"text": text}, ensure_ascii=False)
        return self._send_message(conversation, "text", content, reply_to_message_id=reply_to_message_id)

    def send_card(
        self,
        conversation: ConversationRef,
        card: dict[str, Any],
        *,
        reply_to_message_id: str | None = None,
    ) -> str | None:
        content = json.dumps(card, ensure_ascii=False)
        return self._send_message(conversation, "interactive", content, reply_to_message_id=reply_to_message_id)

    def update_text(self, message_id: str, text: str) -> bool:
        return self._patch_message(message_id, json.dumps({"text": text}, ensure_ascii=False))

    def update_card(self, message_id: str, card: dict[str, Any]) -> bool:
        return self._patch_message(message_id, json.dumps(card, ensure_ascii=False))

    def add_reaction(self, message_id: str, emoji_type: str = "OK") -> bool:
        self._request_json(
            method="POST",
            path=f"/im/v1/messages/{urllib.parse.quote(message_id, safe='')}/reactions",
            payload={"reaction_type": {"emoji_type": emoji_type}},
        )
        return True

    def _send_message(
        self,
        conversation: ConversationRef,
        msg_type: str,
        content: str,
        *,
        reply_to_message_id: str | None = None,
    ) -> str | None:
        payload = {
            "msg_type": msg_type,
            "content": content,
            "uuid": f"cws-{uuid.uuid4()}",
        }
        if reply_to_message_id:
            payload["reply_in_thread"] = False
            response = self._request_json(
                method="POST",
                path=f"/im/v1/messages/{urllib.parse.quote(reply_to_message_id, safe='')}/reply",
                payload=payload,
            )
        else:
            response = self._request_json(
                method="POST",
                path="/im/v1/messages",
                payload={**payload, "receive_id": conversation.conversation_id},
                query={"receive_id_type": "chat_id"},
            )
        return _extract_message_id(response)

    def _patch_message(self, message_id: str, content: str) -> bool:
        self._request_json(
            method="PATCH",
            path=f"/im/v1/messages/{urllib.parse.quote(message_id, safe='')}",
            payload={"content": content},
        )
        return True

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = self._tenant_access_token()
        encoded_query = f"?{urllib.parse.urlencode(query)}" if query else ""
        request = urllib.request.Request(
            f"{self._config.base_url}{path}{encoded_query}",
            method=method,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        response = self._read_json(request)
        code = response.get("code")
        if isinstance(code, int) and code != 0:
            raise RuntimeError(f"Feishu API error {code}: {response.get('msg') or response}")
        return response

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

    def acknowledge_message(self, conversation: ConversationRef, *, source_message_id: str | None) -> bool:
        if not source_message_id:
            return False
        try:
            return self._client.add_reaction(source_message_id)
        except RuntimeError:
            return False

    def upsert_progress(
        self,
        conversation: ConversationRef,
        update: ProgressUpdate,
        *,
        message_id: str | None = None,
        reply_to_message_id: str | None = None,
        source_message_id: str | None = None,
    ) -> str | None:
        card = _build_progress_card(update)
        try:
            if message_id:
                self._client.update_card(message_id, card)
                return message_id
            anchor = reply_to_message_id or source_message_id
            return self._client.send_card(conversation, card, reply_to_message_id=anchor)
        except RuntimeError:
            fallback = f"{update.summary}\n{update.detail}" if update.detail else update.summary
            try:
                self._client.send_text(
                    conversation,
                    fallback,
                    reply_to_message_id=reply_to_message_id or source_message_id,
                )
            except RuntimeError:
                return None
            return None

    def request_approval(self, conversation: ConversationRef, prompt: ApprovalPrompt) -> str | None:
        return self._client.send_card(
            conversation,
            _build_approval_card(prompt, conversation=conversation, status="pending"),
        )

    def resolve_approval(
        self,
        conversation: ConversationRef,
        prompt: ApprovalPrompt,
        *,
        message_id: str | None,
        status: ApprovalCardStatus,
        detail: str | None = None,
    ) -> bool:
        if not message_id:
            return False
        try:
            return self._client.update_card(
                message_id,
                _build_approval_card(prompt, conversation=conversation, status=status, detail=detail),
            )
        except RuntimeError:
            return False

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


def _extract_message_id(response: dict[str, Any]) -> str | None:
    data = response.get("data")
    if isinstance(data, dict):
        if isinstance(data.get("message_id"), str):
            return data["message_id"]
        message = data.get("message")
        if isinstance(message, dict) and isinstance(message.get("message_id"), str):
            return message["message_id"]
    if isinstance(response.get("message_id"), str):
        return response["message_id"]
    return None

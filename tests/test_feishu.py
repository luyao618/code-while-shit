from __future__ import annotations

import json
import unittest

from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger
from cws.models import PendingSubmission

from cws.feishu import (
    FeishuWebSocketGateway,
    _build_approval_card,
    _build_progress_card,
    _extract_text,
    _parse_card_action_submission,
    _parse_message_event,
)
from cws.channels import ApprovalPrompt
from cws.models import ConversationRef, ProgressUpdate

HAS_APPROVAL_MESSAGE_HANDLE = "open_message_id" in PendingSubmission.__dataclass_fields__


class FeishuWebSocketParsingTests(unittest.TestCase):
    def test_parses_message_event_payload(self) -> None:
        event = P2ImMessageReceiveV1(
            {
                "header": {"event_id": "evt-1"},
                "event": {
                    "sender": {"sender_id": {"open_id": "ou_123"}},
                    "message": {
                        "chat_id": "chat_1",
                        "message_id": "msg_1",
                        "chat_type": "p2p",
                        "content": json.dumps({"text": "hello"}),
                    },
                },
            }
        )
        message = _parse_message_event(event)
        self.assertIsNotNone(message)
        self.assertEqual(message.text, "hello")
        self.assertEqual(message.conversation.conversation_id, "chat_1")

    def test_parses_card_action_submission(self) -> None:
        event = P2CardActionTrigger(
            {
                "header": {"event_id": "evt-2"},
                "event": {
                    "operator": {"open_id": "ou_123"},
                    "action": {
                        "value": {
                            "request_id": "req-1",
                            "decision": "approve",
                            "conversation_id": "chat_1",
                            "codex_thread_id": "thread-1",
                            "codex_turn_id": "turn-1",
                            "codex_item_id": "item-1",
                        }
                    },
                    "context": {"open_chat_id": "chat_1", "open_message_id": "om_1"},
                },
            }
        )
        submission = _parse_card_action_submission(event)
        self.assertIsNotNone(submission)
        self.assertEqual(submission.request_id, "req-1")
        self.assertEqual(submission.decision, "approve")
        self.assertEqual(submission.conversation.conversation_id, "chat_1")
        self.assertEqual(submission.codex_thread_id, "thread-1")
        self.assertEqual(submission.codex_turn_id, "turn-1")
        self.assertEqual(submission.codex_item_id, "item-1")

    @unittest.skipUnless(
        HAS_APPROVAL_MESSAGE_HANDLE,
        "Feishu 0.1 approval-card message correlation is not available yet",
    )
    def test_parses_card_action_submission_open_message_id(self) -> None:
        event = P2CardActionTrigger(
            {
                "header": {"event_id": "evt-2b"},
                "event": {
                    "operator": {"open_id": "ou_123"},
                    "action": {
                        "value": {
                            "request_id": "req-1",
                            "decision": "approve",
                            "conversation_id": "chat_1",
                        }
                    },
                    "context": {"open_chat_id": "chat_1", "open_message_id": "om_approval_1"},
                },
            }
        )
        submission = _parse_card_action_submission(event)
        self.assertIsNotNone(submission)
        self.assertEqual(submission.open_message_id, "om_approval_1")

    def test_extract_text_falls_back_for_plain_content(self) -> None:
        self.assertEqual(_extract_text(json.dumps({"text": "hello"})), "hello")
        self.assertEqual(_extract_text("raw message"), "raw message")

    def test_build_progress_card_uses_raw_elements_shape(self) -> None:
        card = _build_progress_card(ProgressUpdate("running", "处理中：Agent 正在执行任务。"))
        self.assertNotIn("schema", card)
        self.assertNotIn("body", card)
        self.assertEqual(card["config"], {"wide_screen_mode": True})
        self.assertIn("elements", card)
        self.assertIsInstance(card["elements"], list)

    def test_build_approval_card_uses_raw_elements_shape(self) -> None:
        prompt = ApprovalPrompt(
            request_id="req-1",
            title="需要确认的操作",
            prompt="请确认",
            command="rm -rf /tmp/demo",
            reason="危险操作",
            cwd="/tmp/demo",
            method="item/commandExecution/requestApproval",
        )
        conversation = ConversationRef("feishu", "default", "chat-1")
        card = _build_approval_card(prompt, conversation=conversation, status="pending")
        self.assertNotIn("schema", card)
        self.assertNotIn("body", card)
        self.assertEqual(card["config"], {"wide_screen_mode": True})
        self.assertIn("elements", card)
        action = next(element for element in card["elements"] if element.get("tag") == "action")
        self.assertEqual(action["actions"][0]["value"]["request_id"], "req-1")


class FeishuWebSocketGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.messages = []
        self.submissions = []
        self.transport_updates = []
        self.seen_keys = set()
        self.gateway = FeishuWebSocketGateway(
            config=type("Cfg", (), {"feishu": type("Feishu", (), {"app_id": "cli_x", "app_secret": "secret", "domain": "https://open.feishu.cn"})(),})(),
            on_message=self.messages.append,
            on_submission=self.submissions.append,
            on_transport_state=lambda **updates: self.transport_updates.append(updates),
            accept_transport_event=self._accept_once,
        )

    def _accept_once(self, kind: str, key: str | None) -> bool:
        token = (kind, key)
        if token in self.seen_keys:
            return False
        self.seen_keys.add(token)
        return True

    def test_gateway_dedupes_duplicate_message_event(self) -> None:
        event = P2ImMessageReceiveV1(
            {
                "header": {"event_id": "evt-1"},
                "event": {
                    "sender": {"sender_id": {"open_id": "ou_123"}},
                    "message": {
                        "chat_id": "chat_1",
                        "message_id": "msg_1",
                        "chat_type": "p2p",
                        "content": json.dumps({"text": "hello"}),
                    },
                },
            }
        )
        self.gateway._handle_message_event(event)
        self.gateway._handle_message_event(event)
        self.assertEqual(len(self.messages), 1)

    def test_gateway_returns_info_toast_for_duplicate_card_action(self) -> None:
        event = P2CardActionTrigger(
            {
                "header": {"event_id": "evt-2"},
                "event": {
                    "operator": {"open_id": "ou_123"},
                    "action": {"value": {"request_id": "req-1", "decision": "approve", "conversation_id": "chat_1"}},
                    "context": {"open_chat_id": "chat_1", "open_message_id": "om_1"},
                    "token": "token-1",
                },
            }
        )
        first = self.gateway._handle_card_action_event(event)
        second = self.gateway._handle_card_action_event(event)
        self.assertEqual(len(self.submissions), 1)
        self.assertEqual(first.toast.content, "已提交")
        self.assertEqual(second.toast.content, "该操作已处理。")

    def test_gateway_returns_error_toast_for_invalid_card_action(self) -> None:
        event = P2CardActionTrigger(
            {
                "header": {"event_id": "evt-3"},
                "event": {
                    "operator": {"open_id": "ou_123"},
                    "action": {"value": {"request_id": "req-1"}},
                    "context": {"open_chat_id": "chat_1", "open_message_id": "om_1"},
                },
            }
        )
        response = self.gateway._handle_card_action_event(event)
        self.assertEqual(response.toast.content, "卡片动作无效。")
        self.assertEqual(self.submissions, [])

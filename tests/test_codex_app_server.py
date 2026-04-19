from __future__ import annotations

import queue
import unittest

from cws.codex_app_server import CodexAppServerBackend
from cws.config import CodexConfig
from cws.models import ConversationRef

try:
    from cws.models import ProgressUpdate
except ImportError:  # pragma: no cover - feature-gated below
    ProgressUpdate = None

HAS_PROGRESS_UPDATES = ProgressUpdate is not None


class FakeClient:
    def __init__(self) -> None:
        self.request_handlers = []
        self.notification_handlers = []
        self.thread_counter = 0
        self.requests = []

    def start(self) -> None:
        return None

    def add_request_handler(self, handler):
        self.request_handlers.append(handler)
        return lambda: self.request_handlers.remove(handler)

    def add_notification_handler(self, handler):
        self.notification_handlers.append(handler)
        return lambda: self.notification_handlers.remove(handler)

    def request(self, method, params=None, timeout=30.0):
        self.requests.append((method, params))
        if method == "initialize":
            return {"userAgent": "fake"}
        if method == "thread/start":
            self.thread_counter += 1
            return {"thread": {"id": f"thread-{self.thread_counter}"}}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}
        if method == "turn/start":
            turn_id = "turn-1"
            for handler in list(self.request_handlers):
                response = handler(
                    {
                        "id": "approval-1",
                        "method": "item/commandExecution/requestApproval",
                        "params": {
                            "threadId": params["threadId"],
                            "turnId": turn_id,
                            "command": "git reset --hard",
                            "cwd": params["cwd"],
                            "itemId": "item-1",
                        },
                    }
                )
                self.last_approval_response = response
            for handler in list(self.notification_handlers):
                handler({"method": "thread/statusChanged", "params": {"threadId": params["threadId"], "turnId": turn_id, "status": "waiting_approval"}})
                handler({"method": "item/agentMessage/delta", "params": {"threadId": params["threadId"], "turnId": turn_id, "delta": "done"}})
                handler({"method": "turn/completed", "params": {"threadId": params["threadId"], "turn": {"id": turn_id, "status": "completed", "items": [{"type": "agentMessage", "text": "sorted script ready"}]}}})
            return {"turn": {"id": turn_id}}
        raise AssertionError(f"Unexpected method: {method}")


class CodexBackendTests(unittest.TestCase):
    def test_process_turn_bridges_approval_and_returns_summary(self) -> None:
        backend = CodexAppServerBackend(
            CodexConfig("codex", ("app-server",), "gpt-5.4", "on-request", "user", "workspace-write", None),
            client=FakeClient(),
        )
        statuses: queue.Queue[str] = queue.Queue()
        outcome = backend.process_turn(
            conversation=ConversationRef("feishu", "default", "chat"),
            workspace_path="/tmp/project",
            prompt="build a sorter",
            existing_thread_id=None,
            request_approval=lambda request: "deny" if request.command == "git reset --hard" else "approve",
            request_input=lambda request: "python",
            publish_status=statuses.put,
        )
        self.assertEqual(outcome.summary, "sorted script ready")
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(backend._client.last_approval_response["decision"], "deny")
        observed_statuses = list(statuses.queue)
        self.assertIn("处理中：Codex 正在执行任务。", observed_statuses)
        self.assertIn("等待确认：Codex 需要你的确认。", observed_statuses)

    @unittest.skipUnless(
        HAS_PROGRESS_UPDATES,
        "Normalized progress updates are not available yet",
    )
    def test_process_turn_emits_normalized_progress_updates(self) -> None:
        backend = CodexAppServerBackend(
            CodexConfig("codex", ("app-server",), "gpt-5.4", "on-request", "user", "workspace-write", None),
            client=FakeClient(),
        )
        statuses: queue.Queue[ProgressUpdate] = queue.Queue()
        backend.process_turn(
            conversation=ConversationRef("feishu", "default", "chat"),
            workspace_path="/tmp/project",
            prompt="build a sorter",
            existing_thread_id=None,
            request_approval=lambda request: "approve",
            request_input=lambda request: "python",
            publish_status=statuses.put,
        )

        observed = list(statuses.queue)
        self.assertGreaterEqual(len(observed), 2)
        self.assertTrue(all(isinstance(update, ProgressUpdate) for update in observed[:2]))
        self.assertEqual(observed[0].milestone, "running")
        self.assertEqual(observed[1].milestone, "waiting_approval")
        self.assertTrue(all(update.summary for update in observed[:2]))

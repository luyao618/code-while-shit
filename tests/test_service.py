from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from cws.channels import ApprovalPrompt, ChannelAdapter, InputPrompt
from cws.config import AppConfig, CodexAgentConfig, FeishuConfig
from cws.agents import AgentConfig
from cws.models import Actor, ApprovalRequest, ConversationRef, ConversationSession, InboundMessage, InputRequest, PendingInteraction, PendingSubmission, TurnOutcome, WorkspaceBinding
from cws.service import BridgeService
from cws.state import StateStore

HAS_PROGRESS_SURFACE_STATE = all(
    field in ConversationSession.__dataclass_fields__
    for field in ("progress_message_id", "progress_milestone")
)
HAS_APPROVAL_MESSAGE_HANDLE = (
    "approval_message_id" in PendingInteraction.__dataclass_fields__
    and "open_message_id" in PendingSubmission.__dataclass_fields__
)


class FakeAdapter(ChannelAdapter):
    def __init__(self) -> None:
        self.statuses = []
        self.results = []
        self.approvals = []
        self.inputs = []
        self.approval_message_id: str | None = None

    def send_status(self, conversation: ConversationRef, text: str) -> None:
        self.statuses.append((conversation, text))

    def send_result(self, conversation: ConversationRef, text: str) -> None:
        self.results.append((conversation, text))

    def request_approval(self, conversation: ConversationRef, prompt: ApprovalPrompt) -> str | None:
        self.approvals.append((conversation, prompt))
        return self.approval_message_id

    def request_user_input(self, conversation: ConversationRef, prompt: InputPrompt) -> None:
        self.inputs.append((conversation, prompt))


class FakeBackend:
    def __init__(self) -> None:
        self.turns = []

    def begin_turn(self, conversation, workspace_path, prompt, existing_thread_id, request_approval, request_input, publish_status):
        return _FakeTurn(self, conversation, workspace_path, prompt, existing_thread_id, request_approval, request_input, publish_status)

    def process_turn(self, conversation, workspace_path, prompt, existing_thread_id, request_approval, request_input, publish_status):
        self.turns.append((conversation, workspace_path, prompt, existing_thread_id))
        publish_status("处理中：Agent 正在执行任务。")
        answer = request_input(
            InputRequest(
                "input-1",
                conversation,
                [{"id": "language", "question": "什么语言？"}],
                "tool-1",
                turn_id="turn-1",
                codex_thread_id=existing_thread_id or "thread-1",
            )
        )
        decision = request_approval(
            ApprovalRequest(
                "approval-1",
                conversation,
                workspace_path,
                "item/commandExecution/requestApproval",
                command="git push --force",
                cwd=workspace_path,
                item_id="cmd-1",
                turn_id="turn-1",
                codex_thread_id=existing_thread_id or "thread-1",
            )
        )
        return TurnOutcome(thread_id=existing_thread_id or "thread-1", summary=f"done with {answer} / {decision}", status="completed")


class _FakeTurn:
    supports_cancel = False
    supports_approval = True

    def __init__(self, backend, conversation, workspace_path, prompt, existing_thread_id, request_approval, request_input, publish_status):
        from cws.agents.base import TurnState
        self._backend = backend
        self._args = (conversation, workspace_path, prompt, existing_thread_id, request_approval, request_input, publish_status)
        self.state = TurnState.RUNNING
        import threading
        self.kill_event = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def run(self):
        from cws.agents.base import TurnState
        outcome = self._backend.process_turn(*self._args)
        self.state = TurnState.COMPLETED
        return outcome

    def cancel(self):
        pass

    def deltas(self):
        raise NotImplementedError


def build_config(tmp: str) -> AppConfig:
    return AppConfig(
        workspace=Path(tmp) / "workspace",
        runtime_dir=Path(tmp) / "runtime",
        state_file=Path(tmp) / "runtime" / "state.json",
        feishu=FeishuConfig(None, None, "https://open.feishu.cn", "https://example.test", (),),
        agent=AgentConfig("codex", CodexAgentConfig()),
    )


class BridgeServiceTests(unittest.TestCase):
    def test_message_flow_with_pending_input_and_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(tmp)
            config.ensure_runtime_dirs()
            adapter = FakeAdapter()
            store = StateStore(config.state_file)
            backend = FakeBackend()
            service = BridgeService(config, adapter, backend=backend, state_store=store)
            conversation = ConversationRef("feishu", "default", "chat")
            actor = Actor("user-1")
            service.handle_message(InboundMessage(conversation, actor, "build a sorter"))

            deadline = time.time() + 5
            while not adapter.inputs and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(adapter.inputs)
            self.assertEqual(adapter.inputs[-1][1].codex_thread_id, "thread-1")
            self.assertEqual(adapter.inputs[-1][1].codex_turn_id, "turn-1")
            self.assertEqual(adapter.inputs[-1][1].codex_item_id, "tool-1")
            service.handle_message(InboundMessage(conversation, actor, "python"))

            deadline = time.time() + 5
            while not adapter.approvals and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(adapter.approvals)
            self.assertEqual(adapter.approvals[-1][1].codex_thread_id, "thread-1")
            self.assertEqual(adapter.approvals[-1][1].codex_turn_id, "turn-1")
            self.assertEqual(adapter.approvals[-1][1].codex_item_id, "cmd-1")
            service.handle_message(InboundMessage(conversation, actor, "approve"))

            deadline = time.time() + 5
            while not any("done with python / approve" in s[1] for s in adapter.statuses) and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(
                any("done with python / approve" in s[1] for s in adapter.statuses),
                f"final reply not found in statuses: {adapter.statuses!r}",
            )

    def test_workspace_switch_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(tmp)
            adapter = FakeAdapter()
            service = BridgeService(config, adapter, backend=FakeBackend(), state_store=StateStore(config.state_file))
            conversation = ConversationRef("feishu", "default", "chat")
            actor = Actor("user-1")
            alt = Path(tmp) / "alt-workspace"
            service.handle_message(InboundMessage(conversation, actor, f"/workspace {alt}"))
            self.assertIn("已切换工作目录", adapter.results[-1][1])
            service.handle_message(InboundMessage(conversation, actor, "/status"))
            self.assertIn("工作目录", adapter.results[-1][1])
            self.assertIn("Feishu Transport", adapter.results[-1][1])


class AdditionalBridgeServiceTests(unittest.TestCase):
    def test_request_approval_persists_adapter_message_handle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(tmp)
            store = StateStore(config.state_file)
            adapter = FakeAdapter()
            adapter.approval_message_id = "om_approval_1"
            service = BridgeService(config, adapter, backend=FakeBackend(), state_store=store)
            conversation = ConversationRef("feishu", "default", "chat")
            session = store.ensure_session(conversation, str(config.default_workspace))
            observed: dict[str, str] = {}

            def respond() -> None:
                deadline = time.time() + 5
                while time.time() < deadline:
                    pending = store.get_pending("approval-1")
                    if pending is not None and pending.approval_message_id == "om_approval_1":
                        observed["approval_message_id"] = pending.approval_message_id
                        break
                    time.sleep(0.01)
                service.handle_message(InboundMessage(conversation, Actor("user-1"), "deny"))

            threading.Thread(target=respond, daemon=True).start()
            decision = service._request_approval(
                session,
                Actor("user-1"),
                ApprovalRequest(
                    "approval-1",
                    conversation,
                    str(config.default_workspace),
                    "item/commandExecution/requestApproval",
                    command="git push --force",
                    cwd=str(config.default_workspace),
                    item_id="cmd-1",
                    turn_id="turn-1",
                    codex_thread_id="thread-1",
                ),
            )

            self.assertEqual(decision, "deny")
            self.assertEqual(observed["approval_message_id"], "om_approval_1")

    def test_allowlist_rejects_unknown_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(tmp)
            config = AppConfig(
                workspace=config.default_workspace,
                runtime_dir=config.runtime_dir,
                state_file=config.state_file,
                feishu=FeishuConfig(None, None, "https://open.feishu.cn", "https://example.test", ("owner",),),
                agent=config.agent,
            )
            adapter = FakeAdapter()
            service = BridgeService(config, adapter, backend=FakeBackend(), state_store=StateStore(config.state_file))
            conversation = ConversationRef("feishu", "default", "chat")
            service.handle_message(InboundMessage(conversation, Actor("guest"), "hello"))
            self.assertIn("allowlist", adapter.results[-1][1])

    def test_thread_reuse_between_turns(self) -> None:
        class ReuseBackend(FakeBackend):
            def process_turn(self, conversation, workspace_path, prompt, existing_thread_id, request_approval, request_input, publish_status):
                self.turns.append(existing_thread_id)
                return TurnOutcome(thread_id=existing_thread_id or "thread-1", summary="ok", status="completed")

        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(tmp)
            adapter = FakeAdapter()
            backend = ReuseBackend()
            service = BridgeService(config, adapter, backend=backend, state_store=StateStore(config.state_file))
            conversation = ConversationRef("feishu", "default", "chat")
            actor = Actor("user-1")
            service.handle_message(InboundMessage(conversation, actor, "first"))
            deadline = time.time() + 5
            while len(adapter.results) < 1 and time.time() < deadline:
                time.sleep(0.01)
            service.handle_message(InboundMessage(conversation, actor, "second"))
            deadline = time.time() + 5
            while len(adapter.results) < 2 and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(backend.turns, [None, "thread-1"])

    def test_busy_conversation_reports_status(self) -> None:
        class SlowBackend(FakeBackend):
            def process_turn(self, conversation, workspace_path, prompt, existing_thread_id, request_approval, request_input, publish_status):
                publish_status("处理中：任务较长")
                time.sleep(0.2)
                return TurnOutcome(thread_id="thread-1", summary="ok", status="completed")

        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(tmp)
            adapter = FakeAdapter()
            service = BridgeService(config, adapter, backend=SlowBackend(), state_store=StateStore(config.state_file))
            conversation = ConversationRef("feishu", "default", "chat")
            actor = Actor("user-1")
            service.handle_message(InboundMessage(conversation, actor, "first"))
            time.sleep(0.05)
            service.handle_message(InboundMessage(conversation, actor, "second"))
            self.assertIn("已有任务在执行", adapter.statuses[-1][1])

    def test_service_recovery_preserves_pending_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(tmp)
            store = StateStore(config.state_file)
            conversation = ConversationRef("feishu", "default", "chat")
            session = store.ensure_session(conversation, str(config.default_workspace))
            session.state = "waiting_approval"
            session.pending_request_id = "req-1"
            store.save_session(session)
            store.set_pending(
                PendingInteraction(
                    request_id="req-1",
                    kind="approval",
                    session_key=session.key,
                    conversation=conversation,
                    title="Approve",
                    prompt="please approve",
                    created_at="2026-04-16T00:00:00+00:00",
                )
            )
            adapter = FakeAdapter()
            service = BridgeService(config, adapter, backend=FakeBackend(), state_store=store)
            recovered = service.state.get_session(conversation)
            self.assertEqual(recovered.state, "waiting_approval")
            self.assertIn("待确认操作仍保留", recovered.last_status)
            self.assertIsNotNone(recovered.recovery_note)

    def test_pending_reply_after_restart_resumes_turn_on_same_thread(self) -> None:
        class RecoveryBackend(FakeBackend):
            def process_turn(self, conversation, workspace_path, prompt, existing_thread_id, request_approval, request_input, publish_status):
                self.turns.append((prompt, existing_thread_id))
                publish_status("处理中：Agent 正在执行任务。")
                return TurnOutcome(thread_id=existing_thread_id or "thread-1", summary="ok", status="completed")

        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(tmp)
            store = StateStore(config.state_file)
            conversation = ConversationRef("feishu", "default", "chat")
            session = store.ensure_session(conversation, str(config.default_workspace))
            store.save_binding(WorkspaceBinding(session.key, str(config.default_workspace), "thread-1"))
            pending = PendingInteraction(
                request_id="req-1",
                kind="user_input",
                session_key=session.key,
                conversation=conversation,
                title="Question",
                prompt="补充说明",
                created_at="2026-04-16T00:00:00+00:00",
                codex_thread_id="thread-1",
                codex_turn_id="turn-1",
                codex_item_id="item-1",
            )
            store.set_pending(pending)
            adapter = FakeAdapter()
            backend = RecoveryBackend()
            service = BridgeService(config, adapter, backend=backend, state_store=store)
            actor = Actor("user-1")

            service.handle_message(InboundMessage(conversation, actor, "python"))
            deadline = time.time() + 5
            while not backend.turns and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(backend.turns[0][1], "thread-1")
            self.assertIn("python", backend.turns[0][0])
            self.assertTrue(any("服务已重启：已收到你的回复" in text for _, text in adapter.statuses))

    def test_workspace_switch_back_reuses_original_thread_binding(self) -> None:
        class WorkspaceAwareBackend(FakeBackend):
            def process_turn(self, conversation, workspace_path, prompt, existing_thread_id, request_approval, request_input, publish_status):
                token = existing_thread_id or f"thread-for-{Path(workspace_path).name}"
                self.turns.append((workspace_path, existing_thread_id, token))
                return TurnOutcome(thread_id=token, summary=token, status="completed")

        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(tmp)
            adapter = FakeAdapter()
            backend = WorkspaceAwareBackend()
            service = BridgeService(config, adapter, backend=backend, state_store=StateStore(config.state_file))
            conversation = ConversationRef("feishu", "default", "chat")
            actor = Actor("user-1")
            default_ws = str(config.default_workspace)
            alt_ws = str(Path(tmp) / "alt")

            service.handle_message(InboundMessage(conversation, actor, "first"))
            deadline = time.time() + 5
            while len(adapter.results) < 1 and time.time() < deadline:
                time.sleep(0.01)
            service.handle_message(InboundMessage(conversation, actor, f"/workspace {alt_ws}"))
            service.handle_message(InboundMessage(conversation, actor, "second"))
            deadline = time.time() + 5
            while len(adapter.results) < 3 and time.time() < deadline:
                time.sleep(0.01)
            service.handle_message(InboundMessage(conversation, actor, f"/workspace {default_ws}"))
            service.handle_message(InboundMessage(conversation, actor, "third"))
            deadline = time.time() + 5
            while len(adapter.results) < 5 and time.time() < deadline:
                time.sleep(0.01)

            self.assertEqual(backend.turns[0][1], None)
            self.assertEqual(backend.turns[1][1], None)
            self.assertEqual(backend.turns[2][1], "thread-for-workspace")

    def test_submission_with_mismatched_correlation_ids_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(tmp)
            store = StateStore(config.state_file)
            adapter = FakeAdapter()
            service = BridgeService(config, adapter, backend=FakeBackend(), state_store=store)
            conversation = ConversationRef("feishu", "default", "chat")
            session = store.ensure_session(conversation, str(config.default_workspace))
            store.set_pending(
                PendingInteraction(
                    request_id="approval-1",
                    kind="approval",
                    session_key=session.key,
                    conversation=conversation,
                    title="Approve",
                    prompt="please approve",
                    created_at="2026-04-16T00:00:00+00:00",
                    codex_thread_id="thread-1",
                    codex_turn_id="turn-1",
                    codex_item_id="item-1",
                )
            )
            submission = PendingSubmission(
                conversation=conversation,
                actor=Actor("user-1"),
                request_id="approval-1",
                kind="approval",
                decision="approve",
                codex_thread_id="thread-1",
                codex_turn_id="turn-1",
                codex_item_id="wrong-item",
            )

            service.handle_submission(submission)
            self.assertIn("缺少匹配的上下文信息", adapter.results[-1][1])

    def test_submission_with_wrong_conversation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(tmp)
            store = StateStore(config.state_file)
            adapter = FakeAdapter()
            service = BridgeService(config, adapter, backend=FakeBackend(), state_store=store)
            conversation = ConversationRef("feishu", "default", "chat")
            session = store.ensure_session(conversation, str(config.default_workspace))
            store.set_pending(
                PendingInteraction(
                    request_id="approval-2",
                    kind="approval",
                    session_key=session.key,
                    conversation=conversation,
                    title="Approve",
                    prompt="please approve",
                    created_at="2026-04-16T00:00:00+00:00",
                )
            )
            submission = PendingSubmission(
                conversation=ConversationRef("feishu", "default", "other-chat"),
                actor=Actor("user-1"),
                request_id="approval-2",
                kind="approval",
                decision="approve",
            )

            service.handle_submission(submission)
            self.assertIn("缺少匹配的上下文信息", adapter.results[-1][1])

    def test_submission_from_different_operator_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(tmp)
            store = StateStore(config.state_file)
            adapter = FakeAdapter()
            service = BridgeService(config, adapter, backend=FakeBackend(), state_store=store)
            conversation = ConversationRef("feishu", "default", "chat")
            session = store.ensure_session(conversation, str(config.default_workspace))
            store.set_pending(
                PendingInteraction(
                    request_id="approval-3",
                    kind="approval",
                    session_key=session.key,
                    conversation=conversation,
                    title="Approve",
                    prompt="please approve",
                    created_at="2026-04-16T00:00:00+00:00",
                    metadata={"actor_user_id": "owner"},
                )
            )
            submission = PendingSubmission(
                conversation=conversation,
                actor=Actor("guest"),
                request_id="approval-3",
                kind="approval",
                decision="approve",
            )

            service.handle_submission(submission)
            self.assertIn("缺少匹配的上下文信息", adapter.results[-1][1])

    def test_handle_submission_rejects_non_allowlisted_actor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(tmp)
            config = AppConfig(
                workspace=config.default_workspace,
                runtime_dir=config.runtime_dir,
                state_file=config.state_file,
                feishu=FeishuConfig(None, None, "https://open.feishu.cn", "https://example.test", ("owner",),),
                agent=config.agent,
            )
            adapter = FakeAdapter()
            service = BridgeService(config, adapter, backend=FakeBackend(), state_store=StateStore(config.state_file))
            conversation = ConversationRef("feishu", "default", "chat")
            session = service.state.ensure_session(conversation, str(config.default_workspace))
            pending = PendingInteraction(
                request_id="req-x",
                kind="approval",
                session_key=session.key,
                conversation=conversation,
                title="Approve",
                prompt="please approve",
                created_at="2026-04-16T00:00:00+00:00",
            )
            service.state.set_pending(pending)
            service.handle_submission(PendingSubmission(conversation, Actor("guest"), "req-x", "approval", decision="approve"))
            self.assertIn("allowlist", adapter.results[-1][1])

    @unittest.skipUnless(
        HAS_APPROVAL_MESSAGE_HANDLE,
        "Approval-card message correlation is not available yet",
    )
    def test_submission_with_matching_open_message_id_is_accepted(self) -> None:
        conversation = ConversationRef("feishu", "default", "chat")
        pending = PendingInteraction(
            request_id="approval-4",
            kind="approval",
            session_key=conversation.session_key,
            conversation=conversation,
            title="Approve",
            prompt="please approve",
            created_at="2026-04-16T00:00:00+00:00",
            approval_message_id="om_approval_1",
        )
        submission = PendingSubmission(
            conversation=conversation,
            actor=Actor("user-1"),
            request_id="approval-4",
            kind="approval",
            decision="approve",
            open_message_id="om_approval_1",
        )

        self.assertTrue(BridgeService._submission_matches_pending(submission, pending))

    @unittest.skipUnless(
        HAS_APPROVAL_MESSAGE_HANDLE,
        "Approval-card message correlation is not available yet",
    )
    def test_submission_with_mismatched_open_message_id_is_rejected(self) -> None:
        conversation = ConversationRef("feishu", "default", "chat")
        pending = PendingInteraction(
            request_id="approval-5",
            kind="approval",
            session_key=conversation.session_key,
            conversation=conversation,
            title="Approve",
            prompt="please approve",
            created_at="2026-04-16T00:00:00+00:00",
            approval_message_id="om_approval_1",
        )
        submission = PendingSubmission(
            conversation=conversation,
            actor=Actor("user-1"),
            request_id="approval-5",
            kind="approval",
            decision="approve",
            open_message_id="om_other",
        )

        self.assertFalse(BridgeService._submission_matches_pending(submission, pending))

    @unittest.skipUnless(
        HAS_PROGRESS_SURFACE_STATE and HAS_APPROVAL_MESSAGE_HANDLE,
        "Persisted turn UX handles are not available yet",
    )
    def test_state_store_round_trip_preserves_turn_ux_handles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(tmp)
            store = StateStore(config.state_file)
            conversation = ConversationRef("feishu", "default", "chat")
            session = store.ensure_session(conversation, str(config.default_workspace))
            session.progress_message_id = "om_progress_1"
            session.progress_milestone = "waiting_approval"
            store.save_session(session)
            store.set_pending(
                PendingInteraction(
                    request_id="approval-6",
                    kind="approval",
                    session_key=session.key,
                    conversation=conversation,
                    title="Approve",
                    prompt="please approve",
                    created_at="2026-04-16T00:00:00+00:00",
                    approval_message_id="om_approval_1",
                )
            )

            reloaded = StateStore(config.state_file)
            recovered_session = reloaded.get_session(conversation)
            recovered_pending = reloaded.get_pending("approval-6")

            self.assertIsNotNone(recovered_session)
            self.assertIsNotNone(recovered_pending)
            self.assertEqual(recovered_session.progress_message_id, "om_progress_1")
            self.assertEqual(recovered_session.progress_milestone, "waiting_approval")
            self.assertEqual(recovered_pending.approval_message_id, "om_approval_1")

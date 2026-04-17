from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vcws.models import ConversationRef, PendingInteraction
from vcws.state import StateStore


class StateStoreTests(unittest.TestCase):
    def test_persists_sessions_and_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            conversation = ConversationRef("feishu", "default", "chat")
            session = store.ensure_session(conversation, "/tmp/workspace")
            pending = PendingInteraction(
                request_id="req-1",
                kind="approval",
                session_key=session.key,
                conversation=conversation,
                title="Approve",
                prompt="please approve",
                created_at="2026-04-16T00:00:00+00:00",
            )
            store.set_pending(pending)

            reloaded = StateStore(path)
            self.assertIsNotNone(reloaded.get_session(conversation))
            self.assertIsNotNone(reloaded.get_pending("req-1"))

    def test_recover_orphans_marks_sessions_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            conversation = ConversationRef("feishu", "default", "chat")
            session = store.ensure_session(conversation, "/tmp/workspace")
            session.state = "running"
            store.save_session(session)
            store.recover_orphans()
            self.assertEqual(store.get_session(conversation).state, "failed")


    def test_recover_orphans_keeps_pending_records_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            conversation = ConversationRef("feishu", "default", "chat")
            session = store.ensure_session(conversation, "/tmp/workspace")
            pending = PendingInteraction(
                request_id="req-2",
                kind="approval",
                session_key=session.key,
                conversation=conversation,
                title="Approve",
                prompt="please approve",
                created_at="2026-04-16T00:00:00+00:00",
            )
            store.set_pending(pending)
            store.recover_orphans()
            recovered = store.get_pending("req-2")
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered.status, "pending")
            self.assertEqual(store.get_session(conversation).state, "waiting_approval")
            self.assertIsNotNone(store.get_session(conversation).recovery_note)

    def test_transport_state_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            store.update_transport_state(status="connected", last_connected_at="2026-04-16T00:00:00+00:00")
            reloaded = StateStore(path)
            transport = reloaded.get_transport_state()
            self.assertEqual(transport.status, "connected")
            self.assertEqual(transport.last_connected_at, "2026-04-16T00:00:00+00:00")

    def test_transport_event_dedupe_persists_across_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            self.assertTrue(store.should_accept_transport_event("message", "evt-1"))
            self.assertFalse(store.should_accept_transport_event("message", "evt-1"))
            reloaded = StateStore(path)
            self.assertFalse(reloaded.should_accept_transport_event("message", "evt-1"))

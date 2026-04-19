import json
from pathlib import Path

from cws.state import StateStore


def _write_legacy_state(path: Path, thread_id: str = "t-123") -> None:
    data = {
        "sessions": {},
        "bindings": {
            "sess1::ws/abs": {
                "session_key": "sess1",
                "workspace_path": "/abs",
                "codex_thread_id": thread_id,
            }
        },
        "pending": {},
        "transport": {"mode": "websocket", "status": "initial"},
    }
    path.write_text(json.dumps(data, indent=2))


def test_migration_renames_codex_thread_id(tmp_path: Path):
    state_file = tmp_path / "bridge-state.json"
    _write_legacy_state(state_file, thread_id="t-abc")
    _store = StateStore(state_file)  # Load triggers migration
    data = json.loads(state_file.read_text())
    binding_key = next(iter(data["bindings"]))
    binding = data["bindings"][binding_key]
    assert "codex_thread_id" not in binding
    assert binding["agent_thread_id"] == "t-abc"
    assert (tmp_path / "bridge-state.json.bak").exists()


def test_migration_idempotent(tmp_path: Path):
    state_file = tmp_path / "bridge-state.json"
    _write_legacy_state(state_file, thread_id="t-xyz")
    StateStore(state_file)  # First load migrates
    mtime1 = state_file.stat().st_mtime
    bak_mtime1 = (tmp_path / "bridge-state.json.bak").stat().st_mtime
    StateStore(state_file)  # Second load — should be noop
    mtime2 = state_file.stat().st_mtime
    bak_mtime2 = (tmp_path / "bridge-state.json.bak").stat().st_mtime
    assert mtime1 == mtime2, "state file was rewritten on idempotent load"
    assert bak_mtime1 == bak_mtime2, ".bak was overwritten on idempotent load"


def test_no_migration_when_no_legacy_field(tmp_path: Path):
    state_file = tmp_path / "bridge-state.json"
    data = {
        "sessions": {},
        "bindings": {
            "sess1::ws/abs": {
                "session_key": "sess1",
                "workspace_path": "/abs",
                "agent_thread_id": "t-new",
            }
        },
        "pending": {},
        "transport": {"mode": "websocket", "status": "initial"},
    }
    state_file.write_text(json.dumps(data))
    StateStore(state_file)
    assert not (tmp_path / "bridge-state.json.bak").exists()

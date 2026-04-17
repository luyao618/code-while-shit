import os
from pathlib import Path
import pytest
from vcws.lockfile import acquire, LockAcquireError


def test_acquire_empty_dir(tmp_path: Path):
    lock = acquire(tmp_path, agent_type="codex", workspace=str(tmp_path))
    assert (tmp_path / "serve.lock").exists()
    lock.release()
    assert not (tmp_path / "serve.lock").exists()


def test_acquire_refuses_live_pid(tmp_path: Path):
    lock_file = tmp_path / "serve.lock"
    lock_file.write_text(f"{os.getpid()}\ncodex\n{tmp_path}")
    with pytest.raises(LockAcquireError, match="another serve is running"):
        acquire(tmp_path, agent_type="codex", workspace=str(tmp_path))


def test_acquire_refuses_stale_pid_without_force(tmp_path: Path):
    lock_file = tmp_path / "serve.lock"
    # Find an unused PID by incrementing from max to very large and checking; simpler: use a PID we know is dead.
    # PID 1 is init on unix, always alive. We need a guaranteed-dead pid. Use a very large number.
    dead_pid = 2_000_000
    lock_file.write_text(f"{dead_pid}\ncodex\n{tmp_path}")
    with pytest.raises(LockAcquireError, match="stale serve.lock"):
        acquire(tmp_path, agent_type="codex", workspace=str(tmp_path))


def test_acquire_stale_pid_with_force(tmp_path: Path):
    lock_file = tmp_path / "serve.lock"
    dead_pid = 2_000_000
    lock_file.write_text(f"{dead_pid}\ncodex\n{tmp_path}")
    lock = acquire(tmp_path, agent_type="codex", workspace=str(tmp_path), force=True)
    assert lock.pid == os.getpid()
    assert lock_file.exists()
    lock.release()


def test_acquire_stale_pid_with_env(tmp_path: Path, monkeypatch):
    lock_file = tmp_path / "serve.lock"
    dead_pid = 2_000_000
    lock_file.write_text(f"{dead_pid}\ncodex\n{tmp_path}")
    monkeypatch.setenv("VCWS_TAKEOVER_STALE", "1")
    lock = acquire(tmp_path, agent_type="codex", workspace=str(tmp_path))
    lock.release()


def test_release_idempotent(tmp_path: Path):
    lock = acquire(tmp_path, agent_type="codex", workspace=str(tmp_path))
    lock.release()
    lock.release()  # Second call is noop; should not raise
    assert not (tmp_path / "serve.lock").exists()


def test_release_does_not_delete_others_lock(tmp_path: Path):
    # Acquire our lock
    lock = acquire(tmp_path, agent_type="codex", workspace=str(tmp_path))
    # Simulate someone else overwriting (shouldn't happen but defensive)
    (tmp_path / "serve.lock").write_text(f"{os.getpid() + 1}\nother\nelsewhere")
    lock.release()
    # The file should still exist because PID doesn't match
    assert (tmp_path / "serve.lock").exists()

from __future__ import annotations
import os
import errno
import time
import atexit
import signal
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

SERVE_LOCK_FILENAME = "serve.lock"


class LockAcquireError(RuntimeError):
    """Lockfile acquire failed — another process holds the lock or stale-lock takeover refused."""


@dataclass
class LockInfo:
    pid: int
    agent_type: str
    workspace: str


@dataclass
class Lock:
    path: Path
    pid: int
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        try:
            # Only delete if the PID inside matches ours (avoid deleting someone else's lock)
            if self.path.exists():
                current = self._read(self.path)
                if current and current.pid == self.pid:
                    self.path.unlink()
        except OSError:
            pass
        self._released = True

    @staticmethod
    def read(path: Path) -> Optional[LockInfo]:
        """Public API: parse a lockfile, returning None if missing/corrupt."""
        try:
            content = path.read_text().strip()
            parts = content.split("\n", 2)
            pid = int(parts[0])
            agent = parts[1] if len(parts) > 1 else ""
            workspace = parts[2] if len(parts) > 2 else ""
            return LockInfo(pid=pid, agent_type=agent, workspace=workspace)
        except (OSError, ValueError, IndexError):
            return None

    # Backwards-compat alias (kept private to avoid churn elsewhere).
    _read = read


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # EPERM means the PID exists but we can't signal it — still alive
        return True
    except OSError as e:
        if e.errno == errno.ESRCH:
            return False
        return True
    return True


# Backwards-compat alias.
_pid_alive = pid_alive


def acquire(
    runtime_dir: Path,
    *,
    agent_type: str,
    workspace: str,
    force: bool = False,
) -> Lock:
    """Acquire the serve lockfile. Raises LockAcquireError on contention.

    Behavior:
    - Fresh acquire on empty dir: writes PID atomically and returns Lock.
    - Existing lockfile with LIVE PID: always refuse (LockAcquireError with message including PID).
    - Existing lockfile with STALE PID: refuse unless force=True or env VCWS_TAKEOVER_STALE=1.
      On takeover, emit WARN log with old PID + lockfile mtime.
    """
    runtime_dir.mkdir(parents=True, exist_ok=True)
    lock_path = runtime_dir / "serve.lock"
    my_pid = os.getpid()
    content = f"{my_pid}\n{agent_type}\n{workspace}"

    env_force = os.environ.get("VCWS_TAKEOVER_STALE") == "1"
    take_stale = force or env_force

    # Try atomic create
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        existing = Lock._read(lock_path)
        if existing is None:
            # Corrupt lock file — treat as stale
            if not take_stale:
                raise LockAcquireError(
                    f"serve.lock exists but is unreadable at {lock_path}. "
                    f"Use --force or VCWS_TAKEOVER_STALE=1 to take over."
                )
            log.warning("Corrupt lockfile at %s — forcing takeover", lock_path)
            lock_path.unlink()
            return acquire(runtime_dir, agent_type=agent_type, workspace=workspace, force=True)
        if _pid_alive(existing.pid):
            raise LockAcquireError(
                f"another serve is running: pid={existing.pid} agent={existing.agent_type} "
                f"workspace={existing.workspace}. Stop it before starting another."
            )
        # Stale PID
        if not take_stale:
            try:
                mtime = time.ctime(lock_path.stat().st_mtime)
            except OSError:
                mtime = "unknown"
            raise LockAcquireError(
                f"stale serve.lock found (pid={existing.pid}, mtime={mtime}). "
                f"Pass --force or set VCWS_TAKEOVER_STALE=1 to take over."
            )
        try:
            mtime = time.ctime(lock_path.stat().st_mtime)
        except OSError:
            mtime = "unknown"
        log.warning("Taking over stale lockfile: old_pid=%d mtime=%s", existing.pid, mtime)
        lock_path.unlink()
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)

    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)

    lock = Lock(path=lock_path, pid=my_pid)
    atexit.register(lock.release)
    return lock


def install_signal_handlers(lock: Lock) -> None:
    """Best-effort lockfile cleanup on SIGINT/SIGTERM. Caller chains further handlers as needed."""

    def _handler(signum, _frame):
        lock.release()
        # Re-raise default behavior
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)

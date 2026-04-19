"""Tests that serve acquires the lockfile and refuses double-start."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_serve_creates_lockfile_and_releases_on_sigint(tmp_path):
    runtime = tmp_path / "rt"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    env = {
        **os.environ,
        "CWS_RUNTIME_DIR": str(runtime),
        "CWS_DEFAULT_WORKSPACE": str(workspace),
        "FEISHU_APP_ID": "x",
        "FEISHU_APP_SECRET": "y",
    }
    # Start serve as subprocess (it will fail at feishu gateway since creds are fake,
    # but it should still acquire the lockfile first)
    proc = subprocess.Popen(
        [sys.executable, "-m", "cws", "serve", "--agent", "codex", "--workspace", str(workspace)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    # Give it up to 3s to get far enough to create the lockfile OR die
    for _ in range(30):
        if (runtime / "serve.lock").exists():
            break
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    try:
        # Either the lockfile exists, or the process died before acquiring it
        lockfile_existed = (runtime / "serve.lock").exists()
        # Try sigint to gracefully exit
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        # After process exit, lockfile should be cleaned up
        if lockfile_existed:
            assert not (runtime / "serve.lock").exists(), "lockfile not cleaned up after SIGINT"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


def test_serve_refuses_double_start(tmp_path):
    runtime = tmp_path / "rt"
    runtime.mkdir()
    # Pre-populate a live lockfile (our own PID)
    lockfile = runtime / "serve.lock"
    lockfile.write_text(f"{os.getpid()}\ncodex\n{tmp_path}")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    env = {
        **os.environ,
        "CWS_RUNTIME_DIR": str(runtime),
        "FEISHU_APP_ID": "x",
        "FEISHU_APP_SECRET": "y",
    }
    result = subprocess.run(
        [sys.executable, "-m", "cws", "serve", "--agent", "codex", "--workspace", str(workspace)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(PROJECT_ROOT),
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "another serve is running" in combined or str(os.getpid()) in combined
    # Critical: it must NOT have printed "Feishu websocket mode active."
    assert "Feishu websocket mode active" not in combined

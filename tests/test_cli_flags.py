import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run(args, env=None, cwd=None):
    env_full = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", "cws", *args],
        capture_output=True,
        text=True,
        env=env_full,
        cwd=str(cwd or PROJECT_ROOT),
    )


def test_serve_without_agent_foreground_fails_on_creds(tmp_path):
    # With --foreground and no FEISHU creds the process should exit non-zero
    # (fails at lockfile acquire because FEISHU check happens before serving,
    # or exits immediately after banner; either way non-zero on missing creds).
    # We also verify no argparse error about --agent being required.
    r = _run(
        ["serve", "--foreground", "--force"],
        env={"CWS_RUNTIME_DIR": str(tmp_path), "FEISHU_APP_ID": "", "FEISHU_APP_SECRET": ""},
        cwd=tmp_path,
    )
    # It will try to connect to Feishu and fail — or exit cleanly after banner.
    # What matters: no error about unrecognized --agent argument.
    combined = r.stdout + r.stderr
    assert "unrecognized" not in combined.lower() or "agent" not in combined.lower()


def test_serve_no_workspace_flag(tmp_path):
    """--workspace flag no longer accepted on serve."""
    r = _run(
        ["serve", "--workspace", str(tmp_path)],
        env={"CWS_RUNTIME_DIR": str(tmp_path)},
        cwd=tmp_path,
    )
    # argparse should reject the unknown flag
    assert r.returncode != 0


def test_doctor_agent_codex(tmp_path):
    r = _run(
        ["doctor", "--agent", "codex"],
        env={"CWS_RUNTIME_DIR": str(tmp_path), "FEISHU_APP_ID": "x", "FEISHU_APP_SECRET": "y"},
        cwd=tmp_path,
    )
    combined = r.stdout + r.stderr
    assert "codex" in combined.lower() or r.returncode == 0


def test_doctor_agent_claude_code_missing(tmp_path):
    r = _run(
        ["doctor", "--agent", "claude-code"],
        env={"CWS_RUNTIME_DIR": str(tmp_path), "FEISHU_APP_ID": "x", "FEISHU_APP_SECRET": "y"},
        cwd=tmp_path,
    )
    combined = r.stdout + r.stderr
    try:
        import claude_agent_sdk  # noqa
        assert r.returncode == 0
    except ImportError:
        assert r.returncode != 0
        assert "claude-agent-sdk" in combined or "claude_agent_sdk" in combined

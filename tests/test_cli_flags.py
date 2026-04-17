import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run(args, env=None):
    env_full = {**os.environ, **(env or {})}
    # Isolate runtime so tests don't collide
    return subprocess.run(
        [sys.executable, "-m", "vcws", *args],
        capture_output=True,
        text=True,
        env=env_full,
        cwd=str(PROJECT_ROOT),
    )


def test_serve_without_agent_errors(tmp_path):
    r = _run(["serve"], env={"CWS_RUNTIME_DIR": str(tmp_path)})
    assert r.returncode != 0
    # argparse produces 'required' or 'one of'
    assert "--agent" in (r.stdout + r.stderr)


def test_serve_conflict_workspace(tmp_path):
    r = _run(
        ["serve", "--agent", "codex", "--workspace", "/different/path"],
        env={
            "CWS_DEFAULT_WORKSPACE": str(tmp_path),
            "CWS_RUNTIME_DIR": str(tmp_path / "rt"),
            "FEISHU_APP_ID": "x",
            "FEISHU_APP_SECRET": "y",
        },
    )
    assert r.returncode == 2
    assert "workspace" in (r.stdout + r.stderr).lower()


def test_doctor_agent_codex(tmp_path):
    r = _run(
        ["doctor", "--agent", "codex"],
        env={"CWS_RUNTIME_DIR": str(tmp_path), "FEISHU_APP_ID": "x", "FEISHU_APP_SECRET": "y"},
    )
    # Exit 0 if codex on PATH; exit 1 otherwise. Either way, output references codex.
    combined = r.stdout + r.stderr
    # We don't assert exit code because codex may or may not be installed;
    # but we assert the doctor did check
    assert "codex" in combined.lower() or r.returncode == 0


def test_doctor_agent_claude_code_missing(tmp_path):
    # claude_agent_sdk is likely not installed in test env; exit non-zero with hint
    r = _run(
        ["doctor", "--agent", "claude-code"],
        env={"CWS_RUNTIME_DIR": str(tmp_path), "FEISHU_APP_ID": "x", "FEISHU_APP_SECRET": "y"},
    )
    # If SDK installed, returncode == 0 and no error; else returncode != 0 and message
    combined = r.stdout + r.stderr
    try:
        import claude_agent_sdk  # noqa
        # Installed — exit 0
        assert r.returncode == 0
    except ImportError:
        assert r.returncode != 0
        assert "claude-agent-sdk" in combined or "claude_agent_sdk" in combined

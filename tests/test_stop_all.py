"""Tests for `cws stop --all` orphan cleanup."""
from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest


@pytest.fixture
def fake_ps_output():
    """Build a fake `ps -axo pid=,command=` output covering the realistic
    process-table mess we saw in production:

    - PID 100: an old `vcws serve` from before the project rename
    - PID 200: a pytest-spawned `python -m cws serve --foreground --force`
    - PID 300: a regular installed `cws serve`
    - PID 400: an editable `python -m cws serve --agent claude-code`
    - PID 500: a noise process whose argv mentions `cws` but not `serve`
    - PID 600: a noise grep that contains the literal string "cws serve"
    """
    return (
        "100 /Users/x/old/.venv/bin/python -m vcws serve\n"
        "200 /Users/x/.venv/bin/python -m cws serve --foreground --force\n"
        "300 /Users/x/.local/share/uv/tools/code-while-shit/bin/python /Users/x/.local/bin/cws serve\n"
        "400 /Users/x/proj/.venv/bin/python -m cws serve --agent claude-code\n"
        "500 /Users/x/.venv/bin/cws status\n"
        "600 grep cws serve\n"
    )


def test_scan_finds_only_real_serve_processes(fake_ps_output, monkeypatch):
    from cws import __main__ as m

    monkeypatch.setattr(m, "subprocess", subprocess)

    fake = subprocess.CompletedProcess(args=["ps"], returncode=0, stdout=fake_ps_output, stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)

    matches = m._scan_serve_pids()
    pids = sorted(p for p, _ in matches)
    # Must include vcws orphan, both editable installs, and the installed binary.
    assert pids == [100, 200, 300, 400], (
        f"unexpected match set: {matches}. Should match exactly the four real "
        "serve processes (legacy vcws, two `python -m cws serve`, one cws "
        "binary), and exclude `cws status` and the grep noise line."
    )


def test_scan_excludes_self_pid(monkeypatch):
    """The stop command itself shows up in `ps`; we must not try to kill it."""
    import os
    from cws import __main__ as m

    self_pid = os.getpid()
    out = f"{self_pid} /Users/x/.venv/bin/python -m cws stop --all\n"
    fake = subprocess.CompletedProcess(args=["ps"], returncode=0, stdout=out, stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)

    assert m._scan_serve_pids() == []


def test_scan_handles_ps_failure(monkeypatch):
    """If `ps` is missing or errors, we report and return empty rather than
    crashing the user's terminal."""
    from cws import __main__ as m

    def boom(*a, **kw):
        raise FileNotFoundError("ps not found")

    monkeypatch.setattr(subprocess, "run", boom)
    assert m._scan_serve_pids() == []


def test_run_stop_all_kills_everything_returned_by_scan(monkeypatch, capsys):
    from cws import __main__ as m

    monkeypatch.setattr(m, "_scan_serve_pids", lambda: [(100, "vcws serve"), (200, "cws serve")])
    killed: list[int] = []

    def fake_terminate(pid, timeout):
        killed.append(pid)
        return "stopped"

    monkeypatch.setattr(m, "_terminate_pid", fake_terminate)
    rc = m._run_stop_all(timeout=2.0)
    assert rc == 0
    assert killed == [100, 200]
    captured = capsys.readouterr().out
    assert "found 2 serve" in captured


def test_run_stop_all_returns_nonzero_on_partial_failure(monkeypatch):
    from cws import __main__ as m

    monkeypatch.setattr(m, "_scan_serve_pids", lambda: [(100, "vcws serve"), (200, "cws serve")])

    def fake_terminate(pid, timeout):
        return "stopped" if pid == 100 else "kill-failed"

    monkeypatch.setattr(m, "_terminate_pid", fake_terminate)
    rc = m._run_stop_all(timeout=2.0)
    assert rc == 1


def test_run_stop_all_no_processes(monkeypatch, capsys):
    from cws import __main__ as m

    monkeypatch.setattr(m, "_scan_serve_pids", lambda: [])
    rc = m._run_stop_all(timeout=2.0)
    assert rc == 0
    assert "no cws/vcws serve processes" in capsys.readouterr().out

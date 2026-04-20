from __future__ import annotations

import argparse
import atexit
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from .agents import create_backend
from .config import AppConfig, ConfigConflictError, load_dotenv
from .feishu import FeishuAdapter, FeishuApiClient, FeishuWebSocketGateway, lark_sdk_available
from .lockfile import (
    SERVE_LOCK_FILENAME,
    Lock,
    LockAcquireError,
    acquire as lockfile_acquire,
    pid_alive,
)
from .service import BridgeService
from .terminal_sink import TerminalSink

_AGENT_CHOICES = ["codex", "claude-code", "opencode"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Feishu-driven remote Codex bridge MVP")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="Start the Feishu bridge server")
    serve_p.add_argument(
        "--agent",
        choices=_AGENT_CHOICES,
        default=None,
        help="Agent backend (default: claude-code, or [agent] default in config.toml)",
    )
    serve_p.add_argument(
        "--allow-auto-approve",
        action="store_true",
        default=False,
        help="Allow auto-approve (opencode only; other agents ignore it)",
    )
    serve_p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Force takeover of a stale lockfile",
    )
    serve_p.add_argument(
        "--foreground",
        action="store_true",
        default=False,
        help="Run in the foreground instead of detaching (default: detach to background)",
    )

    doctor_p = sub.add_parser("doctor", help="Validate local configuration")
    doctor_p.add_argument(
        "--agent",
        choices=_AGENT_CHOICES,
        default=None,
        help="When supplied, check only that agent's dependencies",
    )

    sub.add_parser("init", help="Create global config template at ~/.config/cws/config.toml")

    stop_p = sub.add_parser("stop", help="Stop the running serve process")
    stop_p.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for graceful shutdown before SIGKILL (default: 5)",
    )
    stop_p.add_argument(
        "--all",
        action="store_true",
        help=(
            "Scan the process table and stop EVERY cws/vcws serve process "
            "for the current user (ignores lockfile). Useful for cleaning up "
            "orphans from crashed tests, project renames, or stray --force "
            "starts that bypassed the singleton lock."
        ),
    )

    sub.add_parser("status", help="Show running serve info from lockfile")

    sub.add_parser(
        "update",
        help="Re-install cws from upstream main via `uv tool install --force`",
    )

    restart_p = sub.add_parser("restart", help="Stop the running serve, then start a new one")
    restart_p.add_argument(
        "--agent",
        choices=_AGENT_CHOICES,
        default=None,
        help="Agent backend (default: claude-code, or [agent] default in config.toml)",
    )
    restart_p.add_argument(
        "--allow-auto-approve",
        action="store_true",
        default=False,
        help="Allow auto-approve (opencode only)",
    )
    restart_p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Force takeover of a stale lockfile",
    )
    restart_p.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for graceful shutdown before SIGKILL",
    )
    restart_p.add_argument(
        "--foreground",
        action="store_true",
        default=False,
        help="Run in the foreground instead of detaching (default: detach to background)",
    )

    # --- config subcommand ---
    config_p = sub.add_parser("config", help="Manage global config (~/.config/cws/config.toml)")
    config_sub = config_p.add_subparsers(dest="config_action", required=True)

    config_sub.add_parser("path", help="Print path to global config file")
    config_sub.add_parser("list", help="Print all config values (TOML format)")

    config_get = config_sub.add_parser("get", help="Print a config value (key format: section.name)")
    config_get.add_argument("key", help="Key in section.name format (e.g. feishu.app_id)")

    config_set = config_sub.add_parser("set", help="Set a config value")
    config_set.add_argument("key", help="Key in section.name format")
    config_set.add_argument("value", help="Value to set")

    config_unset = config_sub.add_parser("unset", help="Remove a config key")
    config_unset.add_argument("key", help="Key in section.name format")

    config_sub.add_parser("edit", help="Open config file in $EDITOR (fallback: vi)")

    return parser


def _check_agent_deps(agent: str) -> list[str]:
    problems: list[str] = []
    if agent == "codex":
        if shutil.which("codex") is None:
            problems.append("codex 命令不可用")
    elif agent == "claude-code":
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            problems.append(
                "缺少 Python 依赖 claude-agent-sdk; 使用 pip install 'code-while-shit[claude]' 安装"
            )
    elif agent == "opencode":
        if shutil.which("opencode") is None:
            problems.append("opencode 命令不可用")
    return problems


def _read_lock_info(runtime_dir: Path):
    lock_path = runtime_dir / SERVE_LOCK_FILENAME
    if not lock_path.exists():
        return None
    return Lock.read(lock_path)


def _run_status(runtime_dir: Path) -> int:
    info = _read_lock_info(runtime_dir)
    if info is None:
        print("no serve running (no lockfile)")
        return 0
    alive = pid_alive(info.pid)
    state = "running" if alive else "stale"
    print(f"- state: {state}")
    print(f"- pid: {info.pid}")
    print(f"- agent: {info.agent_type}")
    print(f"- workspace: {info.workspace}")
    print(f"- lockfile: {runtime_dir / SERVE_LOCK_FILENAME}")
    if not alive:
        print("hint: pid is dead. Run `cws serve --force` to take over the stale lock.")
    return 0


def _run_stop(runtime_dir: Path, timeout: float) -> int:
    lock_path = runtime_dir / SERVE_LOCK_FILENAME
    info = _read_lock_info(runtime_dir)
    if info is None:
        print("no serve running (no lockfile)")
        return 0
    if not pid_alive(info.pid):
        print(f"- stale lockfile (pid={info.pid} not alive); cleaning up")
        try:
            lock_path.unlink()
        except OSError as exc:
            print(f"warning: could not remove lockfile: {exc}", file=sys.stderr)
        return 0

    pid = info.pid
    print(f"- sending SIGTERM to pid={pid} (agent={info.agent_type})")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print("- process already gone")
        if lock_path.exists():
            try:
                lock_path.unlink()
            except OSError:
                pass
        return 0
    except PermissionError:
        print(f"error: permission denied signaling pid={pid}", file=sys.stderr)
        return 1

    deadline = time.monotonic() + max(timeout, 0.0)
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            break
        time.sleep(0.1)
    else:
        print(f"- timed out after {timeout}s; sending SIGKILL")
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for _ in range(20):
            if not pid_alive(pid):
                break
            time.sleep(0.1)

    leftover = _read_lock_info(runtime_dir)
    if leftover is not None and leftover.pid == pid and lock_path.exists():
        try:
            lock_path.unlink()
        except OSError:
            pass

    if pid_alive(pid):
        print(f"error: pid={pid} still alive after SIGKILL", file=sys.stderr)
        return 1
    print(f"- stopped pid={pid}")
    return 0


def _scan_serve_pids() -> list[tuple[int, str]]:
    """Return [(pid, argv-snippet), ...] for every cws/vcws serve process owned
    by the current user.

    We deliberately match a wide set of argv patterns:
      - `cws serve` (current name)
      - `python -m cws serve` (dev / editable installs)
      - `vcws serve` / `python -m vcws serve` (legacy name from before the
        project rename — these are exactly the orphans `cws stop` cannot see
        because the binary name changed)

    Excludes the pid of the calling stop command itself.
    """
    self_pid = os.getpid()
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"error: could not list processes via ps: {exc}", file=sys.stderr)
        return []

    matches: list[tuple[int, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        head, _, rest = line.partition(" ")
        try:
            pid = int(head)
        except ValueError:
            continue
        if pid == self_pid:
            continue
        cmd = rest.strip()
        # Token match against argv components so we don't accidentally match
        # editor windows or grep commands that mention the string.
        tokens = cmd.split()
        if "serve" not in tokens:
            continue
        # Skip search/inspection tools whose argv coincidentally contains the
        # words "cws" and "serve" (e.g. `grep cws serve`, `pgrep -f cws serve`).
        first_tok_base = tokens[0].rsplit("/", 1)[-1] if tokens else ""
        if first_tok_base in {"grep", "pgrep", "pkill", "ps", "ag", "rg", "ack", "fzf"}:
            continue
        # Look for an argv token that is one of: cws, vcws, ends with /cws,
        # ends with /vcws, or is a `-m cws` / `-m vcws` python invocation.
        keywords = {"cws", "vcws"}
        is_serve = False
        for i, tok in enumerate(tokens):
            base = tok.rsplit("/", 1)[-1]
            if base in keywords:
                is_serve = True
                break
            if tok == "-m" and i + 1 < len(tokens) and tokens[i + 1] in keywords:
                is_serve = True
                break
        if not is_serve:
            continue
        matches.append((pid, cmd))
    return matches


def _terminate_pid(pid: int, timeout: float) -> str:
    """SIGTERM, wait up to `timeout` seconds, then SIGKILL.

    Returns one of: 'stopped', 'gone', 'kill-failed', 'no-perm'.
    """
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return "gone"
    except PermissionError:
        return "no-perm"

    deadline = time.monotonic() + max(timeout, 0.0)
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return "stopped"
        time.sleep(0.1)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return "stopped"
    for _ in range(20):
        if not pid_alive(pid):
            return "stopped"
        time.sleep(0.1)
    return "kill-failed"


def _run_stop_all(timeout: float) -> int:
    """Stop every cws/vcws serve process for the current user (ignores lockfile).

    Why this exists: the regular `cws stop` only knows about the pid recorded
    in the runtime_dir lockfile. Orphans from prior project names, pytest
    fixtures that started `serve --force --foreground` and crashed before
    teardown, or processes started under a different runtime_dir all slip
    past it. Each orphan opens its own Feishu WebSocket and steals a fraction
    of inbound messages, which manifests as "every chat message looks like a
    new conversation" and "old hard-coded strings reappear".
    """
    matches = _scan_serve_pids()
    if not matches:
        print("no cws/vcws serve processes found")
        return 0

    print(f"found {len(matches)} serve process(es); stopping...")
    failed: list[int] = []
    for pid, cmd in matches:
        # Truncate long argv for readability.
        snippet = cmd if len(cmd) <= 120 else cmd[:117] + "..."
        print(f"- pid={pid}  {snippet}")
        result = _terminate_pid(pid, timeout)
        if result == "stopped":
            print(f"  → stopped")
        elif result == "gone":
            print(f"  → already gone")
        elif result == "no-perm":
            print(f"  → SKIPPED: permission denied (try sudo?)")
            failed.append(pid)
        else:
            print(f"  → FAILED to kill")
            failed.append(pid)

    # Also clean up our own runtime_dir's lockfile if its pid is dead now.
    # Best-effort; not fatal if it fails.
    return 1 if failed else 0


def _run_doctor(args: argparse.Namespace, config: AppConfig) -> int:
    problems: list[str] = []
    if not config.feishu.app_id:
        problems.append("缺少 FEISHU_APP_ID")
    if not config.feishu.app_secret:
        problems.append("缺少 FEISHU_APP_SECRET")
    if not lark_sdk_available():
        problems.append("缺少 Python 依赖 lark-oapi（Feishu websocket transport 必需）")

    target_agent = getattr(args, "agent", None)
    if target_agent:
        agent_problems = _check_agent_deps(target_agent)
        problems.extend(agent_problems)
        if not agent_problems:
            print(f"- {target_agent} 依赖可用")
    else:
        for agent in _AGENT_CHOICES:
            agent_problems = _check_agent_deps(agent)
            if agent_problems:
                for p in agent_problems:
                    print(f"- ⚠️  {agent}: {p}")
            else:
                print(f"- ✓ {agent} 依赖可用")

    if problems:
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("配置看起来可启动（Feishu WebSocket mode）。")
    return 0


_REPO_GIT_URL = "git+https://github.com/luyao618/code-while-shit.git"


def _run_update() -> int:
    """Re-install cws from upstream main via `uv tool install --force`."""
    if shutil.which("uv") is None:
        print(
            "error: `uv` not found on PATH. Re-run the installer:\n"
            "  curl -fsSL https://raw.githubusercontent.com/luyao618/code-while-shit/main/scripts/install.sh | sh",
            file=sys.stderr,
        )
        return 1
    print(f"→ Re-installing cws from {_REPO_GIT_URL} (latest main)...")
    rc = subprocess.call(["uv", "tool", "install", "--force", _REPO_GIT_URL])
    if rc != 0:
        print("error: uv tool install failed", file=sys.stderr)
        return rc
    print("✅ cws updated. The new version is active in any new shell.")
    return 0


def _run_init() -> int:
    from . import user_config
    config_path = user_config.get_path()
    written = user_config.write_init_template()
    if written:
        print(f"Global config written: {config_path}")
    else:
        print(f"Global config already exists: {config_path}")
    print()
    print("Next steps:")
    print(f"  cws config set feishu.app_id YOUR_APP_ID")
    print(f"  cws config set feishu.app_secret YOUR_APP_SECRET")
    print(f"  cws doctor                                     # verify everything is wired up")
    print(f"  cd /path/to/your/project && cws serve          # workspace = cwd")
    return 0


def _run_config(args: argparse.Namespace) -> int:
    from . import user_config
    action = args.config_action

    if action == "path":
        print(user_config.get_path())
        return 0

    if action == "list":
        data = user_config.load()
        if not data:
            print("(empty)")
        else:
            print(user_config.format_for_display(data), end="")
        return 0

    if action == "get":
        val = user_config.get_value(args.key)
        if val is None:
            print(f"error: key {args.key!r} not found", file=sys.stderr)
            return 1
        print(val)
        return 0

    if action == "set":
        try:
            user_config.set_value(args.key, args.value)
            print(f"set {args.key} = {args.value!r}")
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0

    if action == "unset":
        try:
            user_config.unset_value(args.key)
            print(f"unset {args.key}")
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0

    if action == "edit":
        editor = os.environ.get("EDITOR", "vi")
        path = user_config.get_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            user_config.write_init_template()
        os.execvp(editor, [editor, str(path)])
        return 0  # unreachable

    print(f"error: unknown config action: {action}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    # Auto-load .env (project, then user-global). Explicit env vars win.
    load_dotenv(Path(".env"))
    load_dotenv(Path.home() / ".config" / "cws" / ".env")

    args = build_parser().parse_args(argv)

    if args.command == "init":
        return _run_init()

    if args.command == "config":
        return _run_config(args)

    if args.command == "update":
        return _run_update()

    # --- config resolution ---
    try:
        config = AppConfig.from_sources(args)
    except ConfigConflictError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    config.ensure_runtime_dirs()

    if args.command == "status":
        return _run_status(config.runtime_dir)

    if args.command == "stop":
        if getattr(args, "all", False):
            return _run_stop_all(args.timeout)
        return _run_stop(config.runtime_dir, args.timeout)

    if args.command == "restart":
        rc = _run_stop(config.runtime_dir, args.timeout)
        if rc != 0:
            return rc
        return _run_serve(args, config)

    # --- doctor ---
    if args.command == "doctor":
        return _run_doctor(args, config)

    if args.command == "serve":
        return _run_serve(args, config)

    print(f"error: unknown command: {args.command}", file=sys.stderr)
    return 2


_LOG_ROTATE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_LOG_ROTATE_KEEP = 3


def _rotate_log_if_needed(log_path: Path) -> None:
    """Rotate log_path on startup if it exceeds size threshold.

    Keeps up to _LOG_ROTATE_KEEP backups (.1, .2, .3). Old .3 is dropped.
    Done once on serve start; no in-process rotation (raw fd is used).
    """
    try:
        size = log_path.stat().st_size
    except FileNotFoundError:
        return
    except OSError:
        return
    if size < _LOG_ROTATE_MAX_BYTES:
        return
    # Shift backups: .2 -> .3, .1 -> .2, current -> .1
    for i in range(_LOG_ROTATE_KEEP, 0, -1):
        src = log_path.with_suffix(log_path.suffix + f".{i}")
        if i == _LOG_ROTATE_KEEP:
            try:
                src.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
            continue
        dst = log_path.with_suffix(log_path.suffix + f".{i + 1}")
        try:
            src.rename(dst)
        except FileNotFoundError:
            pass
        except OSError:
            pass
    try:
        log_path.rename(log_path.with_suffix(log_path.suffix + ".1"))
    except OSError:
        pass


def _daemonize(log_path: Path) -> None:
    """Detach the current process to background, redirecting stdout/stderr to log_path."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_log_if_needed(log_path)
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid > 0:
        print(f"- cws serve detached to background (pid={pid})")
        print(f"- logs: {log_path}")
        os._exit(0)
    os.setsid()
    devnull_fd = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull_fd, 0)
    os.close(devnull_fd)
    log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(log_fd)


def _run_serve(args: argparse.Namespace, config: AppConfig) -> int:
    agent_type = config.agent.agent_type

    foreground = getattr(args, "foreground", False)
    if not foreground:
        log_path = config.runtime_dir / "serve.log"
        _daemonize(log_path)

    try:
        lock = lockfile_acquire(
            config.runtime_dir,
            agent_type=agent_type,
            workspace=str(config.workspace),
            force=args.force,
        )
    except LockAcquireError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    atexit.register(lock.release)

    sink = TerminalSink()

    adapter = FeishuAdapter(FeishuApiClient(config.feishu))
    backend = create_backend(config)
    service = BridgeService(config=config, adapter=adapter, backend=backend, terminal_sink=sink)
    gateway = FeishuWebSocketGateway(
        config=config,
        on_message=service.handle_message,
        on_submission=service.handle_submission,
        on_transport_state=service.update_transport_state,
        accept_transport_event=service.should_accept_transport_event,
    )

    def shutdown(_signum: int, _frame: object) -> None:
        gateway.shutdown()
        backend.kill()
        lock.release()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    sink.banner(f"{agent_type} ready, waiting feishu messages... (workspace={config.workspace})")
    gateway.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

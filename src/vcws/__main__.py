from __future__ import annotations

import argparse
import atexit
import os
import shutil
import signal
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

_ENV_EXAMPLE = """# vcws configuration — copy to .env and fill in.
FEISHU_APP_ID=
FEISHU_APP_SECRET=
CWS_DEFAULT_WORKSPACE=.

# Optional: default agent (codex | claude-code | opencode)
# VCWS_AGENT=claude-code

# Optional: comma-separated open_ids allowlist
# FEISHU_ALLOWED_USERS=
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Feishu-driven remote Codex bridge MVP")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="Start the Feishu bridge server")
    serve_p.add_argument(
        "--agent",
        choices=_AGENT_CHOICES,
        default=None,
        help="Agent backend (default: claude-code, or $VCWS_AGENT)",
    )
    serve_p.add_argument("--workspace", metavar="PATH", default=None, help="Workspace path")
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
        help="Force takeover of a stale lockfile (wired in US-009)",
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

    init_p = sub.add_parser("init", help="Scaffold .env and workspace")
    init_p.add_argument("--workspace", default=".", help="Workspace path to create")
    init_p.add_argument(
        "--agent",
        choices=_AGENT_CHOICES,
        default="claude-code",
        help="Default agent recorded in .env comment",
    )

    stop_p = sub.add_parser("stop", help="Stop the running serve process")
    stop_p.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for graceful shutdown before SIGKILL (default: 5)",
    )

    sub.add_parser("status", help="Show running serve info from lockfile")

    restart_p = sub.add_parser("restart", help="Stop the running serve, then start a new one")
    restart_p.add_argument(
        "--agent",
        choices=_AGENT_CHOICES,
        default=None,
        help="Agent backend (default: claude-code, or $VCWS_AGENT)",
    )
    restart_p.add_argument("--workspace", metavar="PATH", default=None, help="Workspace path")
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
        print("hint: pid is dead. Run `vcws serve --force` to take over the stale lock.")
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
        # Brief grace for OS cleanup
        for _ in range(20):
            if not pid_alive(pid):
                break
            time.sleep(0.1)

    # Clean lockfile if it still references the dead pid
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


def _run_init(workspace: str, agent: str) -> int:
    ws = Path(workspace).expanduser().resolve()
    try:
        ws.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"error: 无法创建工作目录 {ws}: {exc}", file=sys.stderr)
        return 1
    env_path = Path(".env")
    if env_path.exists():
        print(f"- .env 已存在，保留不覆盖：{env_path.resolve()}")
    else:
        template = _ENV_EXAMPLE.replace("CWS_DEFAULT_WORKSPACE=.", f"CWS_DEFAULT_WORKSPACE={ws}")
        if agent != "codex":
            template = template.replace(
                "# VCWS_AGENT=codex  # optional: codex | claude-code | opencode",
                f"VCWS_AGENT={agent}",
            )
        try:
            env_path.write_text(template, encoding="utf-8")
        except OSError as exc:
            print(f"error: 无法写入 .env（当前目录不可写？）: {exc}", file=sys.stderr)
            return 1
        print(f"- 已生成模板 .env：{env_path.resolve()}")
    print(f"- 工作目录已就绪：{ws}")
    print(f"- 默认 agent：{agent}")
    print("下一步：编辑 .env 填入 FEISHU_APP_ID / FEISHU_APP_SECRET，然后 `vcws doctor`。")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Auto-load .env (project, then user-global). Explicit env vars win.
    load_dotenv(Path(".env"))
    load_dotenv(Path.home() / ".config" / "vcws" / ".env")

    args = build_parser().parse_args(argv)

    if args.command == "init":
        return _run_init(args.workspace, args.agent)

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
        return _run_stop(config.runtime_dir, args.timeout)

    if args.command == "restart":
        rc = _run_stop(config.runtime_dir, args.timeout)
        if rc != 0:
            return rc
        return _run_serve(args, config)

    # --- doctor ---
    if args.command == "doctor":
        problems: list[str] = []
        if not config.feishu.app_id:
            problems.append("缺少 FEISHU_APP_ID")
        if not config.feishu.app_secret:
            problems.append("缺少 FEISHU_APP_SECRET")
        if not lark_sdk_available():
            problems.append("缺少 Python 依赖 lark-oapi（Feishu websocket transport 必需）")

        if args.agent:
            agent_problems = _check_agent_deps(args.agent)
            problems.extend(agent_problems)
            if not agent_problems:
                print(f"- {args.agent} 依赖可用")
        else:
            # Survey all three; only Feishu problems count as fatal.
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

    if args.command == "serve":
        return _run_serve(args, config)

    print(f"error: unknown command: {args.command}", file=sys.stderr)
    return 2


def _daemonize(log_path: Path) -> None:
    """Detach the current process to background, redirecting stdout/stderr to log_path.

    Uses a single fork (sufficient for our use): parent exits so the shell returns,
    child becomes a session leader and writes to the log file. stdin is closed.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Flush before fork so buffered banner doesn't double-print.
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid > 0:
        # Parent: print info and exit so the shell prompt returns.
        print(f"- vcws serve detached to background (pid={pid})")
        print(f"- logs: {log_path}")
        os._exit(0)
    # Child
    os.setsid()
    # Reopen stdio
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
        # Now in detached child; continue startup.

    try:
        lock = lockfile_acquire(
            config.runtime_dir,
            agent_type=agent_type,
            workspace=str(config.default_workspace),
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
    sink.banner(f"{agent_type} ready, waiting feishu messages... (workspace={config.default_workspace})")
    gateway.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

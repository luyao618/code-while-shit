from __future__ import annotations

import argparse
import atexit
import shutil
import signal
import sys

from .agents import create_backend
from .config import AppConfig, ConfigConflictError
from .feishu import FeishuAdapter, FeishuApiClient, FeishuWebSocketGateway, lark_sdk_available
from .lockfile import LockAcquireError, acquire as lockfile_acquire
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
        required=True,
        help="Agent backend to use",
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

    doctor_p = sub.add_parser("doctor", help="Validate local configuration")
    doctor_p.add_argument(
        "--agent",
        choices=_AGENT_CHOICES,
        default=None,
        help="When supplied, also check that agent's dependencies",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # --- config resolution ---
    try:
        if args.command == "serve" or (args.command == "doctor" and args.agent):
            config = AppConfig.from_sources(args)
        else:
            config = AppConfig.from_env()
    except ConfigConflictError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    config.ensure_runtime_dirs()

    # --- doctor ---
    if args.command == "doctor":
        problems: list[str] = []
        if not config.feishu.app_id:
            problems.append("缺少 FEISHU_APP_ID")
        if not config.feishu.app_secret:
            problems.append("缺少 FEISHU_APP_SECRET")
        if not lark_sdk_available():
            problems.append("缺少 Python 依赖 lark-oapi（Feishu websocket transport 必需）")

        if args.agent == "codex":
            if shutil.which("codex") is None:
                problems.append("codex 命令不可用")
            else:
                print("- codex 命令可用")
        elif args.agent == "claude-code":
            try:
                import claude_agent_sdk  # noqa: F401
            except ImportError:
                problems.append(
                    "缺少 Python 依赖 claude-agent-sdk; 使用 pip install 'vibe-coding-while-shit[claude]' 安装"
                )
        elif args.agent == "opencode":
            if shutil.which("opencode") is None:
                problems.append("opencode 命令不可用")
            else:
                print("- opencode 命令可用")

        if problems:
            for problem in problems:
                print(f"- {problem}")
            return 1
        print("配置看起来可启动（Feishu WebSocket mode）。")
        return 0

    # --- serve ---
    try:
        lock = lockfile_acquire(
            config.runtime_dir,
            agent_type=args.agent,
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
    sink.banner(f"{args.agent} ready, waiting feishu messages... (workspace={config.default_workspace})")
    gateway.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

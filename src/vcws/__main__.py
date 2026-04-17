from __future__ import annotations

import argparse
import signal
import sys

from .config import AppConfig
from .feishu import FeishuAdapter, FeishuApiClient, FeishuWebSocketGateway, lark_sdk_available
from .service import BridgeService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Feishu-driven remote Codex bridge MVP")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="Start the Feishu bridge server")
    sub.add_parser("doctor", help="Validate local configuration")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = AppConfig.from_env()
    config.ensure_runtime_dirs()

    if args.command == "doctor":
        problems: list[str] = []
        if not config.feishu.app_id:
            problems.append("缺少 FEISHU_APP_ID")
        if not config.feishu.app_secret:
            problems.append("缺少 FEISHU_APP_SECRET")
        if not lark_sdk_available():
            problems.append("缺少 Python 依赖 lark-oapi（Feishu websocket transport 必需）")
        if problems:
            for problem in problems:
                print(f"- {problem}")
            return 1
        print("配置看起来可启动（Feishu WebSocket mode）。")
        return 0

    adapter = FeishuAdapter(FeishuApiClient(config.feishu))
    service = BridgeService(config=config, adapter=adapter)
    gateway = FeishuWebSocketGateway(
        config=config,
        on_message=service.handle_message,
        on_submission=service.handle_submission,
        on_transport_state=service.update_transport_state,
        accept_transport_event=service.should_accept_transport_event,
    )

    def shutdown(_signum: int, _frame: object) -> None:
        gateway.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    print("Feishu websocket mode active.")
    gateway.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

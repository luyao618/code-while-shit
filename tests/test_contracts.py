from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from codewhileshit.__main__ import main
from codewhileshit.codex_app_server import CodexAppServerBackend
from codewhileshit.config import AppConfig, CodexConfig


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = CodexAppServerBackend(
            CodexConfig("codex", ("app-server",), "gpt-5.4", "on-request", "user", "workspace-write", None)
        )

    def test_command_approval_response_shape(self) -> None:
        response = self.backend._approval_response(
            "item/commandExecution/requestApproval",
            {"command": "git push --force"},
            "approve",
        )
        self.assertEqual(response, {"decision": "allow_once"})

    def test_permissions_approval_response_shape(self) -> None:
        response = self.backend._approval_response(
            "item/permissions/requestApproval",
            {"permissions": {"disk": "write"}},
            "approve",
        )
        self.assertEqual(response, {"permissions": {"disk": "write"}, "scope": "turn"})

    def test_doctor_passes_with_only_app_id_and_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "FEISHU_APP_ID": "cli_xxx",
                "FEISHU_APP_SECRET": "secret",
                "CWS_DEFAULT_WORKSPACE": str(Path(tmp) / "workspace"),
                "CWS_RUNTIME_DIR": str(Path(tmp) / "runtime"),
            },
            clear=True,
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["doctor"])
            self.assertEqual(code, 0)
            self.assertIn("Feishu WebSocket", stdout.getvalue())

    def test_doctor_requires_app_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "FEISHU_APP_ID": "cli_xxx",
                "CWS_DEFAULT_WORKSPACE": str(Path(tmp) / "workspace"),
                "CWS_RUNTIME_DIR": str(Path(tmp) / "runtime"),
            },
            clear=True,
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["doctor"])
            self.assertEqual(code, 1)
            self.assertIn("缺少 FEISHU_APP_SECRET", stdout.getvalue())

    def test_app_config_defaults_to_websocket_domain_shape(self) -> None:
        config = AppConfig.from_env({"FEISHU_APP_ID": "cli_xxx", "FEISHU_APP_SECRET": "secret"})
        self.assertEqual(config.feishu.domain, "https://open.feishu.cn")
        self.assertEqual(config.feishu.base_url, "https://open.feishu.cn/open-apis")

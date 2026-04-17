from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str | None
    app_secret: str | None
    domain: str
    base_url: str
    allowed_user_ids: tuple[str, ...]


@dataclass(frozen=True)
class CodexConfig:
    command: str
    app_server_args: tuple[str, ...]
    model: str
    approval_policy: str
    approvals_reviewer: str
    sandbox: str
    service_tier: str | None


@dataclass(frozen=True)
class AppConfig:
    default_workspace: Path
    runtime_dir: Path
    state_file: Path
    feishu: FeishuConfig
    codex: CodexConfig

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "AppConfig":
        env = env or dict(os.environ)
        runtime_dir = Path(env.get("CWS_RUNTIME_DIR", ".omx/runtime")).resolve()
        default_workspace = Path(env.get("CWS_DEFAULT_WORKSPACE", ".")).resolve()
        state_file = runtime_dir / "bridge-state.json"
        domain = env.get("FEISHU_DOMAIN", "https://open.feishu.cn").rstrip("/")
        feishu = FeishuConfig(
            app_id=env.get("FEISHU_APP_ID"),
            app_secret=env.get("FEISHU_APP_SECRET"),
            domain=domain,
            base_url=env.get("FEISHU_BASE_URL", f"{domain}/open-apis"),
            allowed_user_ids=tuple(
                value.strip()
                for value in env.get("FEISHU_ALLOWED_USERS", "").split(",")
                if value.strip()
            ),
        )
        codex = CodexConfig(
            command=env.get("CODEX_COMMAND", "codex"),
            app_server_args=tuple(
                part
                for part in env.get("CODEX_APP_SERVER_ARGS", "app-server").split(" ")
                if part
            ),
            model=env.get("CODEX_MODEL", "gpt-5.4"),
            approval_policy=env.get("CODEX_APPROVAL_POLICY", "on-request"),
            approvals_reviewer=env.get("CODEX_APPROVALS_REVIEWER", "user"),
            sandbox=env.get("CODEX_SANDBOX", "workspace-write"),
            service_tier=env.get("CODEX_SERVICE_TIER") or None,
        )
        return cls(
            default_workspace=default_workspace,
            runtime_dir=runtime_dir,
            state_file=state_file,
            feishu=feishu,
            codex=codex,
        )

    def ensure_runtime_dirs(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.default_workspace.mkdir(parents=True, exist_ok=True)

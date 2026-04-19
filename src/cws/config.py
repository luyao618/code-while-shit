from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

AgentType = Literal["codex", "claude-code", "opencode"]

DEFAULT_AGENT: AgentType = "claude-code"


class ConfigConflictError(ValueError):
    """CLI and env disagree on a setting."""


def load_dotenv(path: Path) -> bool:
    """Load KEY=VALUE pairs from `path` into os.environ, without overriding
    keys that are already set. Returns True if the file was loaded.

    Minimal parser — no `export`, no multi-line, no `${VAR}` expansion.
    Blank lines and lines starting with `#` are ignored. Values may be
    wrapped in matching single or double quotes.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return False
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)
    return True


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str | None
    app_secret: str | None
    domain: str
    base_url: str
    allowed_user_ids: tuple[str, ...]


@dataclass(frozen=True)
class AgentConfig:
    agent_type: str = field(default="")


@dataclass(frozen=True)
class CodexAgentConfig(AgentConfig):
    agent_type: str = "codex"
    command: str = "codex"
    app_server_args: tuple[str, ...] = ("app-server",)
    model: str = "gpt-5.4"
    approval_policy: str = "on-request"
    approvals_reviewer: str = "user"
    sandbox: str = "workspace-write"
    service_tier: str | None = None


@dataclass(frozen=True)
class ClaudeCodeAgentConfig(AgentConfig):
    agent_type: str = "claude-code"
    model: str | None = None
    permission_mode: str = "default"


@dataclass(frozen=True)
class OpencodeAgentConfig(AgentConfig):
    agent_type: str = "opencode"
    command: str = "opencode"
    host: str = "127.0.0.1"
    port: int | None = None
    allow_auto_approve: bool = False
    startup_timeout_s: float = 5.0


# Backward-compat shim
CodexConfig = CodexAgentConfig


@dataclass(frozen=True)
class AppConfig:
    default_workspace: Path
    runtime_dir: Path
    state_file: Path
    feishu: FeishuConfig
    agent: AgentConfig

    # Backward compat — old .codex attribute access
    @property
    def codex(self) -> CodexAgentConfig:
        if isinstance(self.agent, CodexAgentConfig):
            return self.agent
        return CodexAgentConfig()

    def ensure_runtime_dirs(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.default_workspace.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_sources(
        cls,
        args: argparse.Namespace,
        env: dict[str, str] | None = None,
    ) -> "AppConfig":
        if env is None:
            env = dict(os.environ)

        # --- agent_type ---
        cli_agent = getattr(args, "agent", None) or None
        env_agent = env.get("CWS_AGENT") or None
        if cli_agent and env_agent and cli_agent != env_agent:
            raise ConfigConflictError(
                f"agent conflict: CLI={cli_agent!r} vs env CWS_AGENT={env_agent!r}"
            )
        agent_type = cli_agent or env_agent
        if not agent_type:
            agent_type = DEFAULT_AGENT

        # --- workspace ---
        cli_workspace = getattr(args, "workspace", None) or None
        env_workspace = env.get("CWS_DEFAULT_WORKSPACE") or None
        if cli_workspace and env_workspace and cli_workspace != env_workspace:
            raise ConfigConflictError(
                f"workspace conflict: CLI={cli_workspace!r} vs env CWS_DEFAULT_WORKSPACE={env_workspace!r}"
            )
        workspace_str = cli_workspace or env_workspace or "."
        default_workspace = Path(workspace_str).resolve()

        runtime_dir = Path(env.get("CWS_RUNTIME_DIR", ".omx/runtime")).resolve()
        state_file = runtime_dir / "bridge-state.json"

        domain = env.get("FEISHU_DOMAIN", "https://open.feishu.cn").rstrip("/")
        feishu = FeishuConfig(
            app_id=env.get("FEISHU_APP_ID"),
            app_secret=env.get("FEISHU_APP_SECRET"),
            domain=domain,
            base_url=env.get("FEISHU_BASE_URL", f"{domain}/open-apis"),
            allowed_user_ids=tuple(
                v.strip()
                for v in env.get("FEISHU_ALLOWED_USERS", "").split(",")
                if v.strip()
            ),
        )

        agent: AgentConfig
        if agent_type == "codex":
            agent = CodexAgentConfig(
                command=env.get("CODEX_COMMAND", "codex"),
                app_server_args=tuple(
                    p
                    for p in env.get("CODEX_APP_SERVER_ARGS", "app-server").split(" ")
                    if p
                ),
                model=env.get("CODEX_MODEL", "gpt-5.4"),
                approval_policy=env.get("CODEX_APPROVAL_POLICY", "on-request"),
                approvals_reviewer=env.get("CODEX_APPROVALS_REVIEWER", "user"),
                sandbox=env.get("CODEX_SANDBOX", "workspace-write"),
                service_tier=env.get("CODEX_SERVICE_TIER") or None,
            )
        elif agent_type == "claude-code":
            agent = ClaudeCodeAgentConfig(
                model=env.get("CLAUDE_MODEL") or None,
                permission_mode=env.get("CLAUDE_PERMISSION_MODE", "default"),
            )
        elif agent_type == "opencode":
            allow_auto_approve = getattr(args, "allow_auto_approve", False) or False
            agent = OpencodeAgentConfig(
                command=env.get("OPENCODE_COMMAND", "opencode"),
                host=env.get("OPENCODE_HOST", "127.0.0.1"),
                port=int(env["OPENCODE_PORT"]) if env.get("OPENCODE_PORT") else None,
                allow_auto_approve=allow_auto_approve,
                startup_timeout_s=float(env.get("OPENCODE_STARTUP_TIMEOUT_S", "5.0")),
            )
        else:
            raise ConfigConflictError(f"unknown agent type: {agent_type!r}")

        return cls(
            default_workspace=default_workspace,
            runtime_dir=runtime_dir,
            state_file=state_file,
            feishu=feishu,
            agent=agent,
        )

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "AppConfig":
        if env is None:
            env = dict(os.environ)
        # Build a minimal namespace; agent defaults to codex for backward compat
        ns = argparse.Namespace(
            agent=env.get("CWS_AGENT", DEFAULT_AGENT),
            workspace=None,
            allow_auto_approve=False,
            force=False,
        )
        return cls.from_sources(ns, env=env)

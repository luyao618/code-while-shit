from __future__ import annotations

import argparse
import hashlib
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


def _workspace_hash(workspace: Path) -> str:
    return hashlib.sha256(str(workspace).encode()).hexdigest()[:12]


def _default_runtime_dir(workspace: Path) -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "cws" / "runtime" / _workspace_hash(workspace)


def _load_global_config_as_env() -> dict[str, str]:
    """Load global TOML config and flatten to env-like dict as fallback."""
    from . import user_config  # local import to avoid circular
    data = user_config.load()
    fallback: dict[str, str] = {}

    feishu = data.get("feishu", {})
    if feishu.get("app_id"):
        fallback["FEISHU_APP_ID"] = str(feishu["app_id"])
    if feishu.get("app_secret"):
        fallback["FEISHU_APP_SECRET"] = str(feishu["app_secret"])
    if feishu.get("domain"):
        fallback["FEISHU_DOMAIN"] = str(feishu["domain"])
    if feishu.get("allowed_users"):
        users = feishu["allowed_users"]
        if isinstance(users, list):
            fallback["FEISHU_ALLOWED_USERS"] = ",".join(str(u) for u in users)
        else:
            fallback["FEISHU_ALLOWED_USERS"] = str(users)

    agent = data.get("agent", {})
    if agent.get("default"):
        fallback["CWS_AGENT"] = str(agent["default"])

    codex = data.get("codex", {})
    if codex.get("model"):
        fallback["CODEX_MODEL"] = str(codex["model"])
    if codex.get("approval_policy"):
        fallback["CODEX_APPROVAL_POLICY"] = str(codex["approval_policy"])
    if codex.get("command"):
        fallback["CODEX_COMMAND"] = str(codex["command"])
    if codex.get("sandbox"):
        fallback["CODEX_SANDBOX"] = str(codex["sandbox"])
    if codex.get("service_tier"):
        fallback["CODEX_SERVICE_TIER"] = str(codex["service_tier"])

    return fallback


@dataclass(frozen=True)
class AppConfig:
    workspace: Path
    runtime_dir: Path
    state_file: Path
    feishu: FeishuConfig
    agent: AgentConfig

    # Backward compat: old code accesses default_workspace
    @property
    def default_workspace(self) -> Path:
        return self.workspace

    # Backward compat — old .codex attribute access
    @property
    def codex(self) -> CodexAgentConfig:
        if isinstance(self.agent, CodexAgentConfig):
            return self.agent
        return CodexAgentConfig()

    def ensure_runtime_dirs(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_sources(
        cls,
        args: argparse.Namespace,
        env: dict[str, str] | None = None,
    ) -> "AppConfig":
        if env is None:
            env = dict(os.environ)

        # Load global TOML config as fallback (lowest precedence above built-in defaults)
        toml_fallback = _load_global_config_as_env()

        def _get(key: str, default: str = "") -> str:
            return env.get(key) or toml_fallback.get(key) or default

        # --- workspace = cwd at call time ---
        workspace = Path.cwd().resolve()

        # --- agent_type ---
        cli_agent = getattr(args, "agent", None) or None
        env_agent = _get("CWS_AGENT") or None
        if cli_agent and env_agent and cli_agent != env_agent:
            raise ConfigConflictError(
                f"agent conflict: CLI={cli_agent!r} vs env CWS_AGENT={env_agent!r}"
            )
        agent_type = cli_agent or env_agent or DEFAULT_AGENT

        # --- runtime_dir ---
        runtime_dir_env = env.get("CWS_RUNTIME_DIR")
        if runtime_dir_env:
            runtime_dir = Path(runtime_dir_env).resolve()
        else:
            runtime_dir = _default_runtime_dir(workspace)

        state_file = runtime_dir / "bridge-state.json"

        domain = _get("FEISHU_DOMAIN", "https://open.feishu.cn").rstrip("/")
        feishu = FeishuConfig(
            app_id=_get("FEISHU_APP_ID") or None,
            app_secret=_get("FEISHU_APP_SECRET") or None,
            domain=domain,
            base_url=_get("FEISHU_BASE_URL", f"{domain}/open-apis"),
            allowed_user_ids=tuple(
                v.strip()
                for v in _get("FEISHU_ALLOWED_USERS", "").split(",")
                if v.strip()
            ),
        )

        agent: AgentConfig
        if agent_type == "codex":
            agent = CodexAgentConfig(
                command=_get("CODEX_COMMAND", "codex"),
                app_server_args=tuple(
                    p
                    for p in _get("CODEX_APP_SERVER_ARGS", "app-server").split(" ")
                    if p
                ),
                model=_get("CODEX_MODEL", "gpt-5.4"),
                approval_policy=_get("CODEX_APPROVAL_POLICY", "on-request"),
                approvals_reviewer=_get("CODEX_APPROVALS_REVIEWER", "user"),
                sandbox=_get("CODEX_SANDBOX", "workspace-write"),
                service_tier=_get("CODEX_SERVICE_TIER") or None,
            )
        elif agent_type == "claude-code":
            agent = ClaudeCodeAgentConfig(
                model=_get("CLAUDE_MODEL") or None,
                permission_mode=_get("CLAUDE_PERMISSION_MODE", "default"),
            )
        elif agent_type == "opencode":
            allow_auto_approve = getattr(args, "allow_auto_approve", False) or False
            opencode_port = _get("OPENCODE_PORT")
            agent = OpencodeAgentConfig(
                command=_get("OPENCODE_COMMAND", "opencode"),
                host=_get("OPENCODE_HOST", "127.0.0.1"),
                port=int(opencode_port) if opencode_port else None,
                allow_auto_approve=allow_auto_approve,
                startup_timeout_s=float(_get("OPENCODE_STARTUP_TIMEOUT_S", "5.0")),
            )
        else:
            raise ConfigConflictError(f"unknown agent type: {agent_type!r}")

        return cls(
            workspace=workspace,
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
            allow_auto_approve=False,
            force=False,
        )
        return cls.from_sources(ns, env=env)

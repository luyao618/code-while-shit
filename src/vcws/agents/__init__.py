from __future__ import annotations

from .base import AgentBackend, AgentTurn, CancelNotSupported, TurnState
from .codex import CodexAgentBackend, CodexAgentTurn

from ..config import AppConfig, AgentConfig as AgentConfigBase, CodexAgentConfig


class AgentConfig:
    """Minimal config wrapper for agent factory dispatch (legacy shim)."""

    def __init__(self, agent_type: str, codex_config: CodexAgentConfig | None = None):
        self.agent_type = agent_type
        self.codex_config = codex_config

    @classmethod
    def from_app_config(cls, app_config: AppConfig) -> "AgentConfig":
        return cls(agent_type="codex", codex_config=app_config.codex)


def _resolve(config: "AgentConfig | AppConfig | AgentConfigBase") -> tuple[str, object]:
    """Return (agent_type, per-agent config) from any accepted config shape."""
    if isinstance(config, AppConfig):
        return config.agent.agent_type, (
            config.codex if config.agent.agent_type == "codex" else config.agent
        )
    if isinstance(config, AgentConfigBase):
        return config.agent_type, config
    # Legacy AgentConfig shim
    if config.agent_type == "codex":
        if config.codex_config is None:
            raise ValueError("codex_config required for codex agent type")
        return "codex", config.codex_config
    return config.agent_type, config


def create_backend(config: "AgentConfig | AppConfig | AgentConfigBase") -> AgentBackend:
    """Factory that returns an AgentBackend for the given config."""
    agent_type, agent_config = _resolve(config)
    if agent_type == "codex":
        return CodexAgentBackend(agent_config)  # type: ignore[arg-type]
    if agent_type == "opencode":
        from .opencode import OpencodeAgentBackend  # lazy import

        return OpencodeAgentBackend(agent_config)
    if agent_type == "claude-code":
        from .claude_code import ClaudeCodeAgentBackend  # lazy import

        return ClaudeCodeAgentBackend(agent_config)
    raise NotImplementedError(f"Unknown agent_type: {agent_type!r}")


__all__ = [
    "AgentBackend",
    "AgentTurn",
    "AgentConfig",
    "CancelNotSupported",
    "TurnState",
    "CodexAgentBackend",
    "CodexAgentTurn",
    "create_backend",
]

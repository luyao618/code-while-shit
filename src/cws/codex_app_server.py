"""Legacy shim for src/cws/agents/codex.

DEPRECATED: This module is a compatibility shim. Import from
cws.agents.codex or cws.agents directly.

NOT PICKLE-STABLE: objects imported from this module are not guaranteed
to unpickle under future versions. Scheduled for removal in 0.3.
"""

from cws.agents.codex import (
    CodexAgentBackend,
    CodexAgentBackend as CodexAppServerBackend,  # legacy alias
    CodexAgentTurn,
    CodexAppServerClient,
    CodexRpcError,
    TurnMilestoneUpdate,
    TurnTracker,
)
from cws.agents.base import AgentBackend as CodexBackend  # legacy alias

__all__ = [
    "CodexAgentBackend",
    "CodexAppServerBackend",
    "CodexAgentTurn",
    "CodexAppServerClient",
    "CodexRpcError",
    "CodexBackend",
    "TurnMilestoneUpdate",
    "TurnTracker",
]

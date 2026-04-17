"""Legacy shim for src/vcws/agents/codex.

DEPRECATED: This module is a compatibility shim. Import from
vcws.agents.codex or vcws.agents directly.

NOT PICKLE-STABLE: objects imported from this module are not guaranteed
to unpickle under future versions. Scheduled for removal in 0.3.
"""

from vcws.agents.codex import (
    CodexAgentBackend,
    CodexAgentBackend as CodexAppServerBackend,  # legacy alias
    CodexAgentTurn,
    CodexAppServerClient,
    CodexRpcError,
    TurnMilestoneUpdate,
    TurnTracker,
)
from vcws.agents.base import AgentBackend as CodexBackend  # legacy alias

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

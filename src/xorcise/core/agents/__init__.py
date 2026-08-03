"""xorcise.core.agents — thin agent registry.

LAYER: APPLICATION (domain module). Stores a declaration (name + how it connects + how it emits
its OTel trace), never source analysis. The xorcise.core.code
deep-understanding seam is reserved but NOT invoked here.
"""

from __future__ import annotations

from xorcise.core.agents.registry import (
    DuplicateAgentError,
    get,
    get_by_id,
    list_agents,
    register,
    remove,
    update_agent,
)

__all__ = [
    "DuplicateAgentError",
    "get",
    "get_by_id",
    "list_agents",
    "register",
    "remove",
    "update_agent",
]

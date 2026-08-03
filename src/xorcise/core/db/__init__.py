"""xorcise.core.db — engine-neutral DB access (sqlite default).

LAYER: SHARED-KERNEL. Imports contracts + config. Side-effect-free.
"""

from __future__ import annotations

from xorcise.core.db.base import Base
from xorcise.core.db.engine import get_engine, session_scope
from xorcise.core.db.migrate import boot_state, current_revision, head_revision, upgrade

__all__ = [
    "Base",
    "boot_state",
    "current_revision",
    "get_engine",
    "head_revision",
    "session_scope",
    "upgrade",
]

"""Per-role composition roots — the ONLY modules with import side effects.

`AppSpec` (side-effect-free) is what each `role_<x>.apps()` returns: the ASGI
apps the local lifecycle driver serves for that role.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AppSpec:
    app: Any  # an ASGI app (FastAPI / Starlette)
    port: int
    log_level: str = "info"
    # None → fall back to settings.host at serve time; a tuple lists every
    # address the app must listen on (one uvicorn server per element).
    host: str | tuple[str, ...] | None = None

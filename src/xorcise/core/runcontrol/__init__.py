"""xorcise.core.runcontrol — agent-facing run-control service (domain module).

REST, not MCP. Transport-agnostic operations + server-side
rules; the FastAPI adapter lives in `rest`. Imports only contracts + the shared kernel
(config, db) — cross-module reads are injected (dependency rule). Zero side-effect import."""

from __future__ import annotations

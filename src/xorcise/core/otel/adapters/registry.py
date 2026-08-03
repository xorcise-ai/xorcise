"""Adapter registry + selection.

`register()` files an `AgentTraceAdapter` by `.name`; `GenericOtelAdapter` is registered
under `"generic"` at import so there is always a safe fallback. `select()` walks a trusted
hierarchy — exact `source_agent` match, then `resource_kind`, then a best-effort span
fingerprint, then `generic` — and reports whether the pick was a fallback (`True`) or an
exact match (`False`) so callers/telemetry can tell the two apart.

Imports stdlib + xorcise.core.otel.flatten + xorcise.core.otel.adapters.base +
xorcise.core.otel.adapters.generic only (all within the otel part-island).
"""

from __future__ import annotations

from xorcise.core.otel.adapters.base import AgentTraceAdapter
from xorcise.core.otel.adapters.generic import GenericOtelAdapter
from xorcise.core.otel.flatten import FlatSpan

_REGISTRY: dict[str, AgentTraceAdapter] = {}


def register(adapter: AgentTraceAdapter) -> None:
    """File `adapter` under `adapter.name`, replacing any prior adapter of that name."""
    _REGISTRY[adapter.name] = adapter


def registered_names() -> set[str]:
    return set(_REGISTRY)


def get(name: str) -> AgentTraceAdapter | None:
    """The registered adapter for `name`, or None (callers decide their own fallback)."""
    return _REGISTRY.get(name)


def _fingerprint(spans: list[FlatSpan]) -> str | None:
    """v1: a registered adapter name if a span's `scope`/`name` uniquely identifies one.

    Deliberately conservative — ambiguous (0 or >1 candidate) fingerprints return None so
    `select()` falls through to `generic` rather than guessing wrong.
    """
    candidates = registered_names() - {"generic"}
    if not candidates:
        return None
    hits = {hint for span in spans for hint in (span.scope, span.name) if hint in candidates}
    return hits.pop() if len(hits) == 1 else None


def select(
    source_agent: str,
    spans: list[FlatSpan],
    *,
    resource_kind: str | None = None,
) -> tuple[AgentTraceAdapter, bool]:
    """Pick the adapter for a trace: exact `source_agent` > `resource_kind` > fingerprint >
    `generic`. Returns `(adapter, fallback)` — `fallback=False` only for the exact match."""
    if source_agent in _REGISTRY:
        return _REGISTRY[source_agent], False
    if resource_kind and resource_kind in _REGISTRY:
        return _REGISTRY[resource_kind], True
    fingerprint = _fingerprint(spans)
    if fingerprint is not None:
        return _REGISTRY[fingerprint], True
    return _REGISTRY["generic"], True


register(GenericOtelAdapter())

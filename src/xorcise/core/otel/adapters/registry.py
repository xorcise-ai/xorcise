"""Adapter registry + selection.

`register()` files an `AgentTraceAdapter` by `.name`; `GenericOtelAdapter` is registered
under `"generic"` at import so there is always a safe fallback. `select()` walks a trusted
hierarchy — exact `source_agent` match, then `resource_kind`, then a best-effort span
fingerprint, then `generic` — and reports `fallback`: True whenever the GENERIC renderer ended
up doing the work (no harness-specific adapter, however the walk got there — a blank kind and
a mistyped one read the same), False when a harness-specific adapter was chosen.

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
    `generic`. Returns `(adapter, fallback)` — `fallback` is True iff the pick is the generic
    renderer, so a blank kind and an unrecognised kind carry the same flag (#119)."""
    if source_agent in _REGISTRY:
        adapter = _REGISTRY[source_agent]
    elif resource_kind and resource_kind in _REGISTRY:
        adapter = _REGISTRY[resource_kind]
    elif (fingerprint := _fingerprint(spans)) is not None:
        adapter = _REGISTRY[fingerprint]
    else:
        adapter = _REGISTRY["generic"]
    return adapter, adapter.name == "generic"


register(GenericOtelAdapter())

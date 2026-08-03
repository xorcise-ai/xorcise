"""Telemetry provider registry + selection — the emit-side mirror of
otel/adapters/registry.py.

``register()`` files a provider by ``.name``; ``GenericTelemetryProvider`` is registered under
``"generic"`` at import so there is always a safe floor. ``select(source_agent)`` walks exact
``source_agent`` match → ``generic`` and reports whether the pick was a fallback (``True``) or an
exact match (``False``) — same contract as the adapter registry, minus the fingerprint/kind
hierarchy (there is no trace to fingerprint at launch time). Imports only the telemetry base +
generic provider (all within runs/telemetry).
"""

from __future__ import annotations

from xorcise.core.runs.telemetry.base import TelemetryProfileProvider
from xorcise.core.runs.telemetry.generic import GenericTelemetryProvider

_REGISTRY: dict[str, TelemetryProfileProvider] = {}


def register(provider: TelemetryProfileProvider) -> None:
    """File *provider* under ``provider.name``, replacing any prior provider of that name."""
    _REGISTRY[provider.name] = provider


def registered_names() -> set[str]:
    return set(_REGISTRY)


def select(source_agent: str) -> tuple[TelemetryProfileProvider, bool]:
    """Pick the provider for a run: exact ``source_agent`` match → ``generic``.

    Returns ``(provider, fallback)`` — ``fallback=False`` only for the exact match, mirroring
    ``otel.adapters.registry.select`` so emit and collect agree on what "known harness" means.
    """
    if source_agent in _REGISTRY:
        return _REGISTRY[source_agent], False
    return _REGISTRY["generic"], True


register(GenericTelemetryProvider())

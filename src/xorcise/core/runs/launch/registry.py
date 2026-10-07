"""Launch-provider registry + selection — mirrors runs/telemetry/registry.py.

``register()`` files a provider by ``.name``; ``GenericLaunchProvider`` is the always-present
floor. ``select(source_agent)`` walks exact match -> generic and reports fallback. Imports only
the launch base + generic (all within runs/launch).
"""

from __future__ import annotations

from xorcise.core.runs.launch.base import HarnessLaunchProvider
from xorcise.core.runs.launch.generic import GenericLaunchProvider

_REGISTRY: dict[str, HarnessLaunchProvider] = {}


def register(provider: HarnessLaunchProvider) -> None:
    """File *provider* under ``provider.name``, replacing any prior provider of that name."""
    _REGISTRY[provider.name] = provider


def registered_names() -> set[str]:
    return set(_REGISTRY)


def select(source_agent: str) -> tuple[HarnessLaunchProvider, bool]:
    """Pick provider: exact match or generic. Returns (provider, fallback) — fallback is True iff
    the generic floor was picked, mirroring the adapter + telemetry registries."""
    provider = _REGISTRY.get(source_agent, _REGISTRY["generic"])
    return provider, provider.name == "generic"


register(GenericLaunchProvider())

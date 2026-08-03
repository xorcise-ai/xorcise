"""Resolvers — read a value from a named source in the SealedContext.

Each resolver is deterministic and reads ONLY the sealed DTO (never live infra). A missing ref
resolves to None so the operation decides the verdict. New sources are added by registry entry.
"""

from __future__ import annotations

from collections.abc import Callable

from xorcise.core.contracts.evidence import SealedContext


def _artifacts(ctx: SealedContext, ref: str) -> object:
    return ctx.artifacts.get(ref)


def _otel_stats(ctx: SealedContext, ref: str) -> object:
    return ctx.otel_stats.get(ref)


def _observed_facts(ctx: SealedContext, ref: str) -> object:
    return ctx.observed_facts.get(ref)


RESOLVERS: dict[str, Callable[[SealedContext, str], object]] = {
    "artifacts": _artifacts,
    "otel-stats": _otel_stats,
    "observed-facts": _observed_facts,
}

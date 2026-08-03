"""Telemetry provider framework — the emit-side peer of otel/adapters/base.py.

A ``TelemetryProfileProvider`` turns an ``EmitContext`` (the run being launched) into a
``LaunchProfile`` (the pre-start OTel env a harness needs). Selected by ``source_agent``,
mirroring the replay-adapter selection — so a harness is a PAIR: {adapter (normalize),
provider (emit)}. Imports only the ``connect`` contract (LEAF); ``profile()`` is pure (no I/O).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from xorcise.core.contracts.connect import LaunchProfile


@dataclass(frozen=True)
class EmitContext:
    """The run a launch profile is being built for (the emit-side peer of AdapterContext)."""

    run_id: str
    otlp_endpoint: str  # the server's configured collector base URL ("" when none is configured)
    source_agent: str


class TelemetryProfileProvider(ABC):
    """Emits the pre-start OTel env for ONE harness, selected by ``source_agent``."""

    name: str
    version: str

    @abstractmethod
    def profile(self, ctx: EmitContext) -> LaunchProfile:
        """Return the LaunchProfile (env + correlation + notes) for *ctx*. Pure + total."""
        raise NotImplementedError

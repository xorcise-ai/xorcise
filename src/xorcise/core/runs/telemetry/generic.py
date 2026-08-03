"""The generic telemetry provider — the run-AGNOSTIC fallback.

Reproduces today's three OTLP vars (via ``runs.prompt.build_launch_profile`` — one source of
truth for the generic env shape) and leaves correlation to the prompt's ``xorcise.run_id``
marker (``correlation="prompt-sentinel"``). Registered as the always-present floor by
``registry.py`` at import. Imports only the telemetry base + ``runs.prompt`` (same layer).
"""

from __future__ import annotations

from xorcise.core.contracts.connect import LaunchProfile
from xorcise.core.runs.prompt import build_launch_profile
from xorcise.core.runs.telemetry.base import EmitContext, TelemetryProfileProvider


class GenericTelemetryProvider(TelemetryProfileProvider):
    name = "generic"
    version = "1"

    def profile(self, ctx: EmitContext) -> LaunchProfile:
        # build_launch_profile returns the 3 OTLP vars (or empty when no collector is configured);
        # its correlation already defaults to "prompt-sentinel" — the run-agnostic behaviour.
        return build_launch_profile(ctx.otlp_endpoint)

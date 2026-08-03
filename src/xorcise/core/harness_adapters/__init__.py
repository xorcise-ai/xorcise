"""Per-harness composition tier: one home per agent harness.

Each harness lives in ``harness_adapters/<harness>/`` with an ``otel.py`` (span/log -> AgentEvent
adapter, COLLECT plane), when it also emits a ``telemetry.py`` (launch-profile provider, EMIT
plane), and optionally a ``launch.py`` (HarnessLaunchProvider for CLI launch tips, GUI plane).
The three loaders register each plane INDEPENDENTLY so importing one plane never drags in the
others -- preserving the plane-isolation invariant (the otel display plane stays off role:control's
boot path; guards: test_core_no_openhands_import, test_core_no_telemetry_provider_import,
test_core_no_launch_provider_import). All are idempotent (the underlying registries are name-keyed)
and are called LAZILY from rest-tier seams (events_view for collect,
run_create.launch_profile_for for emit, run_create.launch_tips_for for GUI) -- never at import.
"""

from __future__ import annotations

from importlib import import_module

# Harnesses whose COLLECT-side adapter (otel.py) is available.
_ADAPTER_HARNESSES = ("claude_code", "openhands", "codex")
# Harnesses whose EMIT-side telemetry provider (telemetry.py) is available.
_PROVIDER_HARNESSES = ("claude_code", "codex")
# Harnesses whose GUI launch provider (launch.py) is available.
_LAUNCH_HARNESSES = ("claude_code", "openhands", "codex")


def load_adapters() -> None:
    """Import each harness's ``.otel`` so its ``AgentTraceAdapter`` self-registers (collect plane).

    Idempotent; pulls ONLY the otel display plane.
    """
    for name in _ADAPTER_HARNESSES:
        import_module(f"xorcise.core.harness_adapters.{name}.otel")


def load_providers() -> None:
    """Import each harness's ``.telemetry`` so its ``TelemetryProfileProvider`` self-registers.

    Emit plane. Idempotent; pulls ONLY the telemetry emit plane.
    """
    for name in _PROVIDER_HARNESSES:
        import_module(f"xorcise.core.harness_adapters.{name}.telemetry")


def load_launch_providers() -> None:
    """Import each harness's ``.launch`` so its ``HarnessLaunchProvider`` self-registers.

    GUI plane. Idempotent; pulls ONLY the launch provider plane.
    """
    for name in _LAUNCH_HARNESSES:
        import_module(f"xorcise.core.harness_adapters.{name}.launch")

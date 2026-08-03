"""Harness capability profiles (REST surface) — the honest per-adapter telemetry matrix."""

from __future__ import annotations

from fastapi import APIRouter

from xorcise.core.contracts.agent_event import (
    HarnessCapabilityProfile,
    HarnessDescriptor,
    HarnessLaunchPreview,
)

router = APIRouter(prefix="/harnesses", tags=["harnesses"])


@router.get("/capabilities")
def list_capabilities() -> list[HarnessCapabilityProfile]:
    """Every registered adapter's declared profile, adapter_name-sorted (stable wire order)."""
    # Lazy: keep the otel plane off this module's import path (plane-isolation invariant,
    # mirrors rest/run_terminate.py). load_adapters() self-registers claude-code/codex/openhands
    # (idempotent, name-keyed) so the profile list is complete even when this router answers
    # before any other otel-plane seam (e.g. events_view) has run.
    from xorcise.core.harness_adapters import load_adapters
    from xorcise.core.otel.adapters import registry

    load_adapters()
    profiles = (registry.get(name) for name in sorted(registry.registered_names()))
    return [a.capabilities for a in profiles if a is not None]


@router.get("")
def list_harnesses() -> list[HarnessDescriptor]:
    """Registration descriptors assembled from the existing collect + launch providers.

    The launch information is a non-runnable preview. The run-specific launch-profile route
    remains authoritative once a mission, endpoints, and credentials exist.
    """
    # Lazy imports preserve the three-plane isolation rule: merely importing this router does not
    # pull harness-specific collect or launch implementations into another role.
    from xorcise.core.harness_adapters import load_adapters, load_launch_providers
    from xorcise.core.otel.adapters import registry as adapter_registry
    from xorcise.core.runs.launch import registry as launch_registry
    from xorcise.core.runs.launch.base import LaunchContext

    load_adapters()
    load_launch_providers()

    descriptors: list[HarnessDescriptor] = []
    for kind in sorted(launch_registry.registered_names()):
        launch, _ = launch_registry.select(kind)
        adapter = adapter_registry.get(kind)
        if adapter is None:
            continue
        mode = launch.launch_modes[0]
        ctx = LaunchContext(run_id="<run-id>", source_agent=kind, launch_mode=mode)
        descriptors.append(
            HarnessDescriptor(
                kind=kind,
                display_name=launch.display_name,
                description=launch.description,
                model_hints=launch.model_hints,
                capabilities=adapter.capabilities,
                launch=HarnessLaunchPreview(
                    launch_modes=launch.launch_modes,
                    command_template=launch.launch_command_template,
                    model_flag=launch.model_flag,
                    model_flag_anchor=launch.model_flag_anchor if launch.model_flag else None,
                    tips=launch.tips(ctx),
                    mission_preamble=launch.mission_preamble(ctx),
                ),
            )
        )
    return descriptors

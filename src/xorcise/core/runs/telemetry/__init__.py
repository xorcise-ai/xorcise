"""Per-harness telemetry launch profiles — the EMIT-side peer of otel/adapters.

A run's ``source_agent`` selects both a replay **adapter** (collect side, otel/adapters) and a
**TelemetryProfileProvider** (emit side, here) — so a harness is a pair: it both speaks and
understands its own telemetry. This package is import-side-effect-free; the concrete providers live
in the ``harness_adapters`` tier and self-register only when ``harness_adapters.load_providers()``
imports them (called lazily by ``rest/run_create.launch_profile_for``), keeping the core control
path harness-agnostic (guard: tests/unit/test_core_no_telemetry_provider_import.py).
"""

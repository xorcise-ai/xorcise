"""OpenAI Codex CLI telemetry provider (emit plane) — the run-correlation env for `codex`.

Codex's OTel is `config.toml`-driven and IGNORES the OTEL_EXPORTER_* env vars, so — unlike Claude
Code — this provider does NOT emit them (the exporter endpoints ride the launch command's ``-c``
flags instead; putting the ignored vars in the copy-paste block would only mislead). It emits ONLY
``OTEL_RESOURCE_ATTRIBUTES=xorcise.run_id=<run_id>``, which the OTel Rust SDK's EnvResourceDetector
(inside `Resource::builder()`) folds into the RESOURCE attributes — the exact key the server
routes on (``otel/decode._run_id_of``). Verified live: a codex run correlated a
216-span trace via this attribute; `span_attributes` (span-level) does NOT correlate. Hence
``correlation="resource-attr"`` — the same strong tier as Claude Code (not the prompt sentinel).

Self-registers at import; pulled in ONLY via harness_adapters.load_providers() so the core control
path stays harness-agnostic (guard: tests/unit/test_core_no_telemetry_provider_import.py).
"""

from __future__ import annotations

from xorcise.core.contracts.connect import LaunchProfile
from xorcise.core.runs.telemetry.base import EmitContext, TelemetryProfileProvider
from xorcise.core.runs.telemetry.registry import register

_RESOURCE_ATTR_KEY = "OTEL_RESOURCE_ATTRIBUTES"


class CodexTelemetryProvider(TelemetryProfileProvider):
    name = "codex"
    version = "1"

    def profile(self, ctx: EmitContext) -> LaunchProfile:
        # No OTLP endpoint env: codex ignores OTEL_EXPORTER_* — the endpoints live in the launch
        # command's `-c` flags. Only the resource-attribute correlation rides the environment.
        env: tuple[tuple[str, str], ...] = ()
        notes: tuple[str, ...] = ()
        if ctx.run_id:
            env = ((_RESOURCE_ATTR_KEY, f"xorcise.run_id={ctx.run_id}"),)
            notes = (
                "Append xorcise.run_id to any OTEL_RESOURCE_ATTRIBUTES you already export "
                "(comma-separated) rather than overwriting service.name/deployment.environment.",
            )
        return LaunchProfile(env=env, correlation="resource-attr", notes=notes)


register(CodexTelemetryProvider())

"""The Claude Code telemetry provider — a SELECTED harness gets its solid OTEL env.

Emits the generic OTLP vars PLUS Claude Code's telemetry flags and, crucially,
``OTEL_RESOURCE_ATTRIBUTES=xorcise.run_id=<run_id>`` so EVERY OTLP ResourceSpans batch correlates
to the run (``otel/decode._run_id_of``). Verified live: a Claude Code run correlated a
full 52-event trace WITH the attribute and only ~1 event without it (the prompt marker rides only
the one batch that echoes the prompt). Claude Code's OTel SDK honours ``OTEL_RESOURCE_ATTRIBUTES``,
so no sidecar is needed; ``correlation="resource-attr"``.

Self-registers at import; pulled in ONLY at the composition seam (``composition.py``) so the core
control path stays harness-agnostic (guard: tests/unit/test_core_no_telemetry_provider_import.py).
Imports the telemetry base + ``runs.prompt`` (generic env) + the registry (same layer).
"""

from __future__ import annotations

from xorcise.core.contracts.connect import LaunchProfile
from xorcise.core.runs.prompt import build_launch_profile
from xorcise.core.runs.telemetry.base import EmitContext, TelemetryProfileProvider
from xorcise.core.runs.telemetry.registry import register

# Claude Code's telemetry flags: enable + the beta gate REQUIRED for spans, content capture
# (prompt / tool detail / tool output), and a short export interval so a headless run flushes
# before it exits. Mirrors the fixtures-repo agents/claude-code/env.sh capture recipe.
_CLAUDE_CODE_FLAGS: tuple[tuple[str, str], ...] = (
    ("CLAUDE_CODE_ENABLE_TELEMETRY", "1"),
    ("CLAUDE_CODE_ENHANCED_TELEMETRY_BETA", "1"),
    ("OTEL_LOG_USER_PROMPTS", "1"),
    ("OTEL_LOG_TOOL_DETAILS", "1"),
    ("OTEL_LOG_TOOL_CONTENT", "1"),
    ("OTEL_TRACES_EXPORT_INTERVAL", "1000"),
    # Logs signal: the assistant's response TEXT + rich event stream ride OTLP logs, not
    # spans. Enable the logs exporter (→ <endpoint>/v1/logs) + OTEL_LOG_ASSISTANT_RESPONSES (off by
    # default; the response is <REDACTED> without it) so the replay shows Claude's actual reasoning.
    ("OTEL_LOGS_EXPORTER", "otlp"),
    ("OTEL_LOG_ASSISTANT_RESPONSES", "1"),
    ("OTEL_LOGS_EXPORT_INTERVAL", "1000"),
)

_RESOURCE_ATTR_KEY = "OTEL_RESOURCE_ATTRIBUTES"


class ClaudeCodeTelemetryProvider(TelemetryProfileProvider):
    name = "claude-code"
    version = "1"

    def profile(self, ctx: EmitContext) -> LaunchProfile:
        env = (*build_launch_profile(ctx.otlp_endpoint).env, *_CLAUDE_CODE_FLAGS)
        notes: tuple[str, ...] = ()
        if ctx.run_id:
            env = (*env, (_RESOURCE_ATTR_KEY, f"xorcise.run_id={ctx.run_id}"))
            notes = (
                "Append xorcise.run_id to any OTEL_RESOURCE_ATTRIBUTES you already export "
                "(comma-separated) rather than overwriting service.name/deployment.environment.",
            )
        return LaunchProfile(env=env, correlation="resource-attr", notes=notes)


register(ClaudeCodeTelemetryProvider())

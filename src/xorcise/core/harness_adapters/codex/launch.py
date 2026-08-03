"""OpenAI Codex CLI launch provider (GUI plane): the copy-paste `codex` one-liner + tips.

Codex's OTel is `config.toml`-driven and IGNORES the OTEL_EXPORTER_* env vars, so the exporter
config rides the launch command's ``-c`` overrides (layered on the operator's config in-memory, per
invocation — no file written, nothing to clean up). The per-signal endpoints are filled by
``build_startup_tips`` (``{otlp_traces_endpoint}`` / ``{otlp_logs_endpoint}``) from the mode-aware
collector host; run correlation rides the ``OTEL_RESOURCE_ATTRIBUTES`` export the telemetry provider
emits. Host-only: codex runs in the operator's terminal, where the collector is the local loopback.

Self-registers at import; pulled in ONLY via harness_adapters.load_launch_providers() so the core
control path stays harness-free (guard: tests/unit/test_core_no_launch_provider_import.py).
"""

from __future__ import annotations

from xorcise.core.runs.launch.base import HarnessLaunchProvider, LaunchContext
from xorcise.core.runs.launch.registry import register


class CodexLaunchProvider(HarnessLaunchProvider):
    name = "codex"
    version = "1"
    display_name = "Codex CLI"
    description = "OpenAI's non-interactive coding CLI."
    model_hints = ("gpt-5.3-codex", "gpt-5.6", "gpt-5.6-terra")
    model_flag = "--model"
    # Exporter config via `-c` (codex ignores OTEL_EXPORTER_* env). Endpoints are per-signal full
    # URLs (codex does NOT append /v1/traces); protocol "binary" == http/protobuf; metrics off (the
    # Headless & autonomous — the codex analog of Claude Code's `claude -p`: `codex exec` runs to
    # completion without a TUI and flushes spans on exit. `codex exec` is inherently non-interactive
    # (there is NO --ask-for-approval flag — that's top-level `codex` only and errors here), so
    # --sandbox danger-full-access is what lets it run the objective's tools without prompts;
    # --skip-git-repo-check so it runs from any cwd the operator pastes into. Exporter config rides
    # `-c` (codex ignores OTEL_EXPORTER_* env); {otlp_*_endpoint} are filled by build_startup_tips;
    # {mission} is the final positional arg.
    launch_command_template = (
        "codex exec --skip-git-repo-check --sandbox danger-full-access "
        "-c otel.trace_exporter.otlp-http.endpoint='\"{otlp_traces_endpoint}\"' "
        "-c otel.trace_exporter.otlp-http.protocol='\"binary\"' "
        "-c otel.exporter.otlp-http.endpoint='\"{otlp_logs_endpoint}\"' "
        "-c otel.exporter.otlp-http.protocol='\"binary\"' "
        "-c otel.metrics_exporter='\"none\"' "
        # Capture the user prompt in the logs (else codex logs it as "[REDACTED]" and the replay
        # can't show the task). Mirrors Claude Code's OTEL_LOG_USER_PROMPTS=1; the prompt (with the
        # run-control bearer) goes only to THIS run's own collector — same trust domain.
        "-c otel.log_user_prompt=true "
        "{mission}"
    )
    # Host-only: `codex` runs on the host, where the collector is the local loopback —
    # host.docker.internal (the container address) doesn't resolve there.
    launch_modes = ("host",)

    def tips(self, ctx: LaunchContext) -> tuple[str, ...]:
        return (
            "Keep the saved execution context aligned with the generated command: "
            "OTEL_RESOURCE_ATTRIBUTES=xorcise.run_id=<id> is what correlates every span to THIS "
            "run; codex ignores OTEL_EXPORTER_* env, so append it rather than relying on them.",
            "The exporter config rides the `-c` flags in the command (layered in-memory, per run) "
            "— nothing is written to your ~/.codex/config.toml and there is nothing to clean up.",
        )

    def mission_preamble(self, ctx: LaunchContext) -> tuple[str, ...]:
        return (
            "You are launched via the `codex` CLI. Work the objective directly; do not modify "
            "system networking or join any tailnet other than the one this "
            "run provides.",
            "Note: run tailscale in user-space and don't leave artifacts outside your workspace.",
        )


register(CodexLaunchProvider())

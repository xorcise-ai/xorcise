from __future__ import annotations

from xorcise.core.runs.launch.base import HarnessLaunchProvider, LaunchContext


def _provider() -> tuple[HarnessLaunchProvider, bool]:
    from xorcise.core.harness_adapters import load_launch_providers
    from xorcise.core.runs.launch.registry import select

    load_launch_providers()
    return select("codex")


def test_codex_launch_provider_registers_and_exposes_command():
    from xorcise.core.harness_adapters import load_launch_providers
    from xorcise.core.runs.launch.registry import registered_names

    load_launch_providers()
    assert "codex" in registered_names()
    provider, fallback = _provider()
    assert fallback is False
    template = provider.launch_command_template
    assert template is not None
    # Headless, autonomous run (the codex analog of `claude -p`): `codex exec` runs without a TUI;
    # --sandbox danger-full-access lets it run the objective's tools without prompts (exec is
    # inherently non-interactive — it has NO --ask-for-approval flag, unlike top-level `codex`).
    assert template.startswith("codex exec ")
    assert "--sandbox danger-full-access" in template
    assert "--ask-for-approval" not in template  # invalid for `codex exec` — would error instantly
    assert "--skip-git-repo-check" in template
    # codex carries its exporter config in `-c` flags (it ignores OTEL_EXPORTER_* env), and the
    # mission is the final positional arg.
    assert "otel.trace_exporter.otlp-http.endpoint" in template
    assert "{otlp_traces_endpoint}" in template
    assert "otel.exporter.otlp-http.endpoint" in template
    assert "{otlp_logs_endpoint}" in template
    assert "otel.metrics_exporter='\"none\"'" in template
    # capture the user prompt (else codex logs "[REDACTED]" and the replay can't show the task)
    assert "otel.log_user_prompt=true" in template
    assert template.rstrip().endswith("{mission}")


def test_codex_is_host_only():
    provider, _ = _provider()
    assert provider.launch_modes == ("host",)


def test_codex_disclosed_model_is_inserted_before_mission():
    provider, _ = _provider()
    template = provider.command_template_for("gpt-5.6-terra")
    assert template is not None
    assert "--model gpt-5.6-terra {mission}" in template
    assert template.rstrip().endswith("{mission}")


def test_codex_tips_mention_execution_context_and_export():
    provider, _ = _provider()
    blob = "\n".join(provider.tips(LaunchContext("r1", "codex", "host")))
    assert "execution context" in blob.lower()
    # the exporter config rides -c flags; correlation rides the OTEL_RESOURCE_ATTRIBUTES export,
    # which must be exported before the command — the tips call that out.
    assert "OTEL_RESOURCE_ATTRIBUTES" in blob or "export" in blob.lower()


def test_codex_mission_preamble_is_nonempty():
    provider, _ = _provider()
    pre = provider.mission_preamble(LaunchContext("r1", "codex", "host"))
    assert pre and isinstance(pre, tuple)


def test_codex_template_renders_a_valid_one_liner():
    # The rendered command (via build_startup_tips) is a single line with the endpoints filled and
    # the mission shell-quoted last — the paste-and-go one-liner.
    from xorcise.core.contracts.connect import LaunchProfile
    from xorcise.core.rest.run_create import build_startup_tips

    provider, _ = _provider()
    tips = build_startup_tips(
        LaunchProfile(env=(("OTEL_RESOURCE_ATTRIBUTES", "xorcise.run_id=r1"),)),
        provider.launch_command_template,
        "solve the CTF",
        otlp_traces_endpoint="http://127.0.0.1:4318/v1/traces",
        otlp_logs_endpoint="http://127.0.0.1:4318/v1/logs",
    )
    cmd = tips.command
    assert cmd is not None
    assert "\n" not in cmd  # single line
    assert "http://127.0.0.1:4318/v1/traces" in cmd
    assert "http://127.0.0.1:4318/v1/logs" in cmd
    assert cmd.endswith("'solve the CTF'")
    assert tips.shell_block.startswith("export OTEL_RESOURCE_ATTRIBUTES=xorcise.run_id=r1")

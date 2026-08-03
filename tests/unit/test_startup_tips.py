# tests/unit/test_startup_tips.py
"""build_startup_tips — the pure, harness-agnostic shell-block builder."""

from __future__ import annotations

from xorcise.core.contracts.connect import LaunchProfile, StartupTips
from xorcise.core.rest.run_create import build_startup_tips


def test_builds_shell_block_with_quoted_env_and_mission():
    profile = LaunchProfile(
        env=(
            ("OTEL_EXPORTER_OTLP_ENDPOINT", "http://h:4318"),
            ("CLAUDE_CODE_ENABLE_TELEMETRY", "1"),
        ),
        correlation="resource-attr",
        notes=("hint",),
    )
    tips = build_startup_tips(profile, "claude -p {mission}", "solve the CTF; flag=X")
    assert isinstance(tips, StartupTips)
    assert tips.command == "claude -p 'solve the CTF; flag=X'"  # shell-quoted mission
    assert "export OTEL_EXPORTER_OTLP_ENDPOINT=http://h:4318" in tips.shell_block
    assert tips.shell_block.strip().endswith("claude -p 'solve the CTF; flag=X'")
    assert tips.correlation == "resource-attr" and tips.notes == ("hint",)
    assert tips.env == profile.env


def test_placeholder_mission_when_absent_and_no_command_without_template():
    profile = LaunchProfile(env=(("A", "1"),))
    with_tmpl = build_startup_tips(profile, "claude -p {mission}", None)
    assert with_tmpl.command == "claude -p <mission>"  # literal placeholder, unquoted
    no_tmpl = build_startup_tips(profile, None, "m")
    assert no_tmpl.command is None
    assert no_tmpl.shell_block == "export A=1"  # env only, no command line


def test_fills_otlp_endpoint_placeholders_for_codex_style_template():
    # codex carries its exporter config in the launch command's `-c` flags (it
    # ignores OTEL_EXPORTER_* env), so build_startup_tips must substitute the mode-aware per-signal
    # collector URLs into {otlp_traces_endpoint} / {otlp_logs_endpoint} alongside {mission}.
    profile = LaunchProfile(env=(("OTEL_RESOURCE_ATTRIBUTES", "xorcise.run_id=r1"),))
    tmpl = (
        "codex -c otel.trace_exporter.otlp-http.endpoint='\"{otlp_traces_endpoint}\"' "
        "-c otel.exporter.otlp-http.endpoint='\"{otlp_logs_endpoint}\"' {mission}"
    )
    tips = build_startup_tips(
        profile,
        tmpl,
        "solve it",
        otlp_traces_endpoint="http://127.0.0.1:4318/v1/traces",
        otlp_logs_endpoint="http://127.0.0.1:4318/v1/logs",
    )
    cmd = tips.command
    assert cmd is not None
    assert "otel.trace_exporter.otlp-http.endpoint='\"http://127.0.0.1:4318/v1/traces\"'" in cmd
    assert "otel.exporter.otlp-http.endpoint='\"http://127.0.0.1:4318/v1/logs\"'" in cmd
    assert cmd.endswith("'solve it'")  # mission still shell-quoted, last
    # env still exported before the command in the consolidated block
    assert tips.shell_block.startswith("export OTEL_RESOURCE_ATTRIBUTES=")


def test_endpoint_placeholders_ignored_by_plain_template():
    # A template without the endpoint placeholders (Claude Code) is unaffected when they're passed.
    profile = LaunchProfile(env=())
    tips = build_startup_tips(
        profile,
        "claude -p {mission}",
        "m",
        otlp_traces_endpoint="http://x/v1/traces",
        otlp_logs_endpoint="http://x/v1/logs",
    )
    assert tips.command == "claude -p m"  # shlex.quote leaves a bare token unquoted

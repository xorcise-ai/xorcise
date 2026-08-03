# tests/unit/test_telemetry_providers.py
"""Emit-side telemetry providers: registry selection + generic/claude-code profiles +
the additive LaunchProfile contract. The emit-side mirror of the otel adapter registry tests."""

from __future__ import annotations

from xorcise.core.contracts.connect import LaunchProfile, StartupTips
from xorcise.core.harness_adapters import load_providers
from xorcise.core.runs.telemetry.base import EmitContext
from xorcise.core.runs.telemetry.registry import registered_names, select

load_providers()  # register the providers (emit plane) — was the composition-seam import

_ENDPOINT = "http://host.docker.internal:4318"


def _ctx(source_agent: str, run_id: str = "run123") -> EmitContext:
    return EmitContext(run_id=run_id, otlp_endpoint=_ENDPOINT, source_agent=source_agent)


def test_generic_and_claude_code_are_registered_via_the_seam():
    names = registered_names()
    assert "generic" in names
    assert "claude-code" in names


def test_select_claude_code_is_an_exact_match():
    provider, fallback = select("claude-code")
    assert provider.name == "claude-code"
    assert fallback is False


def test_select_unknown_falls_back_to_generic():
    provider, fallback = select("no-such-harness")
    assert provider.name == "generic"
    assert fallback is True


def test_select_generic_is_not_a_fallback():
    provider, fallback = select("generic")
    assert provider.name == "generic"
    assert fallback is False


def test_generic_profile_is_run_agnostic_three_vars_prompt_sentinel():
    provider, _ = select("generic")
    profile = provider.profile(_ctx("generic"))
    env = dict(profile.env)
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == _ENDPOINT
    assert env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
    assert env["OTEL_TRACES_EXPORTER"] == "otlp"
    assert "OTEL_RESOURCE_ATTRIBUTES" not in env  # run-agnostic — no per-run correlation
    assert profile.correlation == "prompt-sentinel"
    assert profile.notes == ()


def test_claude_code_profile_has_flags_and_binds_run_correlation():
    provider, _ = select("claude-code")
    profile = provider.profile(_ctx("claude-code", run_id="abc123"))
    env = dict(profile.env)
    # the 3 generic OTLP vars are still present (endpoint known)
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == _ENDPOINT
    # Claude Code's telemetry flags
    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    assert env["CLAUDE_CODE_ENHANCED_TELEMETRY_BETA"] == "1"
    assert env["OTEL_LOG_USER_PROMPTS"] == "1"
    assert env["OTEL_LOG_TOOL_CONTENT"] == "1"
    # the logs signal (assistant response text) is enabled + opted in
    assert env["OTEL_LOGS_EXPORTER"] == "otlp"
    assert env["OTEL_LOG_ASSISTANT_RESPONSES"] == "1"
    # the resource attribute the server routes on — bound to THIS run (rides every batch)
    assert env["OTEL_RESOURCE_ATTRIBUTES"] == "xorcise.run_id=abc123"
    assert profile.correlation == "resource-attr"
    assert profile.notes  # operator hint: append, don't clobber, existing OTEL_RESOURCE_ATTRIBUTES


def test_claude_code_without_run_id_omits_the_resource_attr():
    provider, _ = select("claude-code")
    profile = provider.profile(_ctx("claude-code", run_id=""))
    env = dict(profile.env)
    assert "OTEL_RESOURCE_ATTRIBUTES" not in env  # nothing to correlate to
    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"  # flags still emitted
    assert profile.correlation == "resource-attr"


def test_generic_with_no_collector_stays_empty():
    provider, _ = select("generic")
    profile = provider.profile(EmitContext(run_id="r", otlp_endpoint="", source_agent="generic"))
    assert profile.env == ()  # no collector configured → nothing to export


def test_launch_profile_contract_is_additive():
    p = LaunchProfile()
    assert p.env == ()
    assert p.correlation == "prompt-sentinel"
    assert p.notes == ()
    q = LaunchProfile(env=(("A", "1"),), correlation="resource-attr", notes=("hint",))
    assert q.correlation == "resource-attr" and q.notes == ("hint",)


def test_startup_tips_is_additive_and_frozen():
    t = StartupTips()
    assert t.env == () and t.command is None and t.shell_block == ""
    assert t.correlation == "prompt-sentinel" and t.notes == ()
    t2 = StartupTips(
        env=(("A", "1"),),
        command="claude -p 'x'",
        shell_block="export A=1\nclaude -p 'x'",
        correlation="resource-attr",
    )
    assert t2.command == "claude -p 'x'" and "export A=1" in t2.shell_block

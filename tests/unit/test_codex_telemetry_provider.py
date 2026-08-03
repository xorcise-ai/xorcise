from __future__ import annotations

from xorcise.core.runs.telemetry.base import EmitContext, TelemetryProfileProvider


def _provider() -> tuple[TelemetryProfileProvider, bool]:
    from xorcise.core.harness_adapters import load_providers
    from xorcise.core.runs.telemetry.registry import select

    load_providers()
    return select("codex")


def test_codex_telemetry_registers():
    from xorcise.core.harness_adapters import load_providers
    from xorcise.core.runs.telemetry.registry import registered_names

    load_providers()
    assert "codex" in registered_names()
    _, fallback = _provider()
    assert fallback is False


def test_codex_profile_sets_only_resource_attr_correlation():
    provider, _ = _provider()
    profile = provider.profile(
        EmitContext(run_id="run-xyz", otlp_endpoint="http://127.0.0.1:4318", source_agent="codex")
    )
    env = dict(profile.env)
    # correlation rides the resource attribute the OTel SDK honours — the SAME tier as Claude Code.
    assert env.get("OTEL_RESOURCE_ATTRIBUTES") == "xorcise.run_id=run-xyz"
    assert profile.correlation == "resource-attr"
    # codex IGNORES the OTEL_EXPORTER_* env — they must NOT appear (exporter config rides -c flags,
    # so showing them in the copy-paste block would mislead).
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in env
    assert "OTEL_EXPORTER_OTLP_PROTOCOL" not in env
    assert "OTEL_TRACES_EXPORTER" not in env


def test_codex_profile_without_run_id_is_empty_env():
    provider, _ = _provider()
    profile = provider.profile(EmitContext(run_id="", otlp_endpoint="", source_agent="codex"))
    assert dict(profile.env) == {}


def test_codex_profile_notes_guard_resource_attr_overwrite():
    provider, _ = _provider()
    profile = provider.profile(
        EmitContext(run_id="r1", otlp_endpoint="http://127.0.0.1:4318", source_agent="codex")
    )
    # a note warns the operator to append (not overwrite) any existing OTEL_RESOURCE_ATTRIBUTES
    assert any("OTEL_RESOURCE_ATTRIBUTES" in n for n in profile.notes)

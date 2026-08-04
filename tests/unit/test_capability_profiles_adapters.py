# tests/unit/test_capability_profiles_adapters.py
"""Adapter capability seam: abstract property, helper totality, registry lookup."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from xorcise.core.contracts.agent_event import (
    AgentEventKind,
    HarnessCapabilityProfile,
    KindSupport,
)
from xorcise.core.harness_adapters.claude_code.otel import ClaudeCodeAdapter
from xorcise.core.harness_adapters.codex.otel import CodexAdapter
from xorcise.core.harness_adapters.openhands.otel import OpenHandsAdapter  # real class name
from xorcise.core.otel.adapters import registry
from xorcise.core.otel.adapters.base import AdapterContext, AgentTraceAdapter, profile_from
from xorcise.core.otel.adapters.generic import GenericOtelAdapter
from xorcise.core.otel.flatten import FlatLogRecord, FlatSpan, flatten, flatten_logs

_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures/otlp"


def test_profile_from_fills_unlisted_kinds_as_unsupported() -> None:
    p = profile_from("x", "1", supported=(AgentEventKind.message,))
    assert p.kinds["message"] is KindSupport.supported
    assert p.kinds["thinking"] is KindSupport.unsupported
    assert set(p.kinds) == {k.value for k in AgentEventKind}


def test_generic_profile_is_unverified_best_effort() -> None:
    p = GenericOtelAdapter().capabilities
    assert p.adapter_name == "generic"
    assert p.verified is False
    assert p.kinds["message"] is KindSupport.supported


def test_registry_get_returns_registered_adapter_or_none() -> None:
    assert registry.get("generic") is not None
    assert registry.get("no-such-harness") is None


_GENAI_MODULE = "src/xorcise/core/otel/adapters/genai.py"


def _emitted_kinds(adapter_module_path: str) -> set[str]:
    """The AgentEventKind members an adapter's source can construct (the honesty oracle).

    Excludes the `capabilities` property's own body: that block DECLARES supported/partial
    kinds via bare `AgentEventKind.x` references, so scanning it too would make the guard below
    tautological (any kind added to `capabilities` would trivially count as "emitted"). Everything
    outside that property is what the adapter's own event constructors / delegated extractors can
    actually build.

    File-scoped, so a kind built by a delegated builder living in ANOTHER module is invisible to a
    plain scan of this one — this is a known, documented blind spot (see the fixture-based test
    below, which covers that direction against real data instead of source text). The ONE delegate
    every adapter here is allowed to name directly, GenAiSemconvExtractor, is folded in explicitly
    when a module imports it, so a legitimately delegated kind (e.g. openhands' `metric`, built
    entirely by genai.py) doesn't trip this guard as a false "never produces this" failure.
    """
    import re
    from pathlib import Path

    src = Path(adapter_module_path).read_text(encoding="utf-8")
    delegates_to_genai = "GenAiSemconvExtractor" in src
    prop = re.search(r"\n    def capabilities\(self\).*?(?=\n    def )", src, re.DOTALL)
    # If the `capabilities` property were ever the LAST method in the class, the `(?=\n    def )`
    # lookahead would never match, `prop` would be None, and the guard below would silently scan
    # the WHOLE file (capabilities property included) — making the honesty guard tautological
    # (anything declared would trivially count as "emitted"). Fail loudly instead (Minor 3).
    assert prop is not None, (
        "capabilities property not found/excisable — oracle would be tautological"
    )
    src = src[: prop.start()] + src[prop.end() :]
    kinds = set(re.findall(r"AgentEventKind\.([a-z_]+)", src))
    if delegates_to_genai:
        genai_src = Path(_GENAI_MODULE).read_text(encoding="utf-8")
        kinds |= set(re.findall(r"AgentEventKind\.([a-z_]+)", genai_src))
    return kinds


@pytest.mark.parametrize(
    ("adapter", "module_rel"),
    [
        (ClaudeCodeAdapter(), "src/xorcise/core/harness_adapters/claude_code/otel.py"),
        (CodexAdapter(), "src/xorcise/core/harness_adapters/codex/otel.py"),
        (OpenHandsAdapter(), "src/xorcise/core/harness_adapters/openhands/otel.py"),
    ],
    ids=["claude-code", "codex", "openhands"],
)
def test_declared_support_is_subset_of_what_the_code_emits(adapter, module_rel) -> None:
    """HONESTY GUARD: an adapter may not declare supported/partial a kind its code never builds."""
    profile: HarnessCapabilityProfile = adapter.capabilities
    declared = {k for k, v in profile.kinds.items() if v.value in ("supported", "partial")}
    assert declared <= _emitted_kinds(module_rel), (
        f"{profile.adapter_name} declares kinds its normalize() can never produce"
    )


def _spans_from_records(doc: dict[str, Any]) -> list[FlatSpan]:
    spans: list[FlatSpan] = []
    for rec in doc.get("records", []):
        spans.extend(flatten(rec["payload"], raw_seq=rec.get("seq", 0)))
    return spans


def _logs_from_records(doc: dict[str, Any]) -> list[FlatLogRecord]:
    logs: list[FlatLogRecord] = []
    for rec in doc.get("log_records", []):
        logs.extend(flatten_logs(rec["payload"], raw_seq=rec.get("seq", 0)))
    return logs


def _emitted_kinds_from_fixture(adapter: AgentTraceAdapter, fixture_name: str) -> set[str]:
    """The AgentEventKind values an adapter ACTUALLY produces over its own real captured OTLP
    fixture — the honesty oracle's other, more powerful direction (emitted ⊆ declared). Unlike
    `_emitted_kinds` above (a source scan of ONE file), this runs both normalize() and
    normalize_logs() over real data, so it catches a kind built by a DELEGATED builder living in a
    different module (e.g. GenAiSemconvExtractor.extract in otel/adapters/genai.py) that a
    per-file source scan structurally cannot see — this is what would have caught C1 (openhands
    delegates to GenAiSemconvExtractor, which emits `metric`, but the profile didn't declare it)."""
    doc = json.loads((_FIXTURES_DIR / fixture_name).read_text())
    ctx = AdapterContext(
        run_id=str(doc.get("run_id", "fixture")),
        source_agent=adapter.name,
        mission_id="fixture",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    spans = _spans_from_records(doc)
    logs = _logs_from_records(doc)
    kinds = {e.kind.value for e in adapter.normalize(spans, ctx)}
    kinds |= {e.kind.value for e in adapter.normalize_logs(logs, ctx)}
    return kinds


@pytest.mark.parametrize(
    ("adapter", "fixture_name"),
    [
        (ClaudeCodeAdapter(), "claude_code_real_run.json"),
        (CodexAdapter(), "codex_real_run.json"),
        (OpenHandsAdapter(), "openhands_real_run.json"),
    ],
    ids=["claude-code", "codex", "openhands"],
)
def test_emitted_kinds_from_real_fixture_are_all_declared(adapter, fixture_name) -> None:
    """HONESTY GUARD, bidirectional half (emitted ⊆ declared∪partial): every AgentEventKind an
    adapter's real captured trace ACTUALLY produces must be declared supported or partial. The
    source-scan guard above only ever checked declared ⊆ emitted-in-source, so a kind produced by a
    delegated builder in another file (or only reachable via a real fixture, not a bare
    `AgentEventKind.x` reference) could be silently under-declared — exactly the openhands/metric
    gap (C1) this test exists to catch."""
    fixture_path = _FIXTURES_DIR / fixture_name
    if not fixture_path.exists():
        pytest.skip(f"no real OTLP fixture {fixture_name} for {adapter.name} — cannot verify")
    emitted = _emitted_kinds_from_fixture(adapter, fixture_name)
    profile: HarnessCapabilityProfile = adapter.capabilities
    declared_ok = {k for k, v in profile.kinds.items() if v.value in ("supported", "partial")}
    unexpected = emitted - declared_ok
    assert not unexpected, (
        f"{profile.adapter_name} emits {sorted(unexpected)} from its real fixture "
        f"({fixture_name}) but declares them unsupported"
    )


def test_claude_code_declares_no_thinking_and_codex_partial_messages() -> None:
    """The two headline honesty facts this feature exists to surface."""
    cc = ClaudeCodeAdapter().capabilities
    assert cc.kinds["thinking"].value == "unsupported"
    assert "thinking" in cc.notes
    cx = CodexAdapter().capabilities
    assert cx.kinds["message"].value == "partial"
    assert "message" in cx.notes
    assert cx.kinds["error"].value == "unsupported"
    assert cx.notes["error"] == "Model refusal details are not exported."

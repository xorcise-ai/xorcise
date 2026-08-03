# tests/unit/test_genai_extractor.py
from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from xorcise.core.contracts.agent_event import AgentEventKind
from xorcise.core.otel.adapters.base import AdapterContext
from xorcise.core.otel.adapters.genai import GenAiSemconvExtractor
from xorcise.core.otel.flatten import FlatSpan, flatten

_CTX = AdapterContext(
    run_id="run-1",
    source_agent="some-other-agent",  # deliberately NOT openhands
    mission_id="chal-1",
    created_at=datetime(2026, 7, 4, tzinfo=UTC),
)


def _span(
    attrs: dict[str, str], *, name: str = "chat gpt-4o", scope: str = "openllmetry"
) -> FlatSpan:
    return FlatSpan(
        span_id="abc123",
        parent_span_id="parent1",
        trace_id="trace1",
        name=name,
        start_ns=1_700_000_000_000_000_000,
        end_ns=1_700_000_001_000_000_000,
        status_code=0,
        attrs=attrs,
        scope=scope,
        resource={"service.name": "not-openhands"},
        raw_seq=3,
    )


def test_is_llm_span_via_gen_ai_attrs():
    assert GenAiSemconvExtractor.is_llm_span(_span({"gen_ai.request.model": "gpt-4o"})) is True


def test_is_llm_span_via_lmnr_type_without_gen_ai():
    assert GenAiSemconvExtractor.is_llm_span(_span({"lmnr.span.type": "LLM"})) is True


def test_is_not_llm_span():
    assert GenAiSemconvExtractor.is_llm_span(_span({"other.attr": "x"})) is False


def test_extract_synthetic_non_openhands_span():
    """Reuse proof: a non-OpenHands gen_ai.* span yields message + tool_call + metric."""
    out_msgs = [
        {
            "role": "assistant",
            "content": "Let me list the files.",
            "tool_calls": [
                {"id": "call_1", "function": {"name": "run_shell", "arguments": '{"cmd":"ls"}'}}
            ],
        }
    ]
    span = _span(
        {
            "gen_ai.output.messages": json.dumps(out_msgs),
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.usage.input_tokens": "123",
            "gen_ai.usage.output_tokens": "45",
        }
    )
    events = GenAiSemconvExtractor().extract(span, _CTX)

    kinds = [e.kind for e in events]
    assert AgentEventKind.message in kinds
    assert AgentEventKind.tool_call in kinds
    assert AgentEventKind.metric in kinds

    msg = next(e for e in events if e.kind == AgentEventKind.message)
    assert msg.body == "Let me list the files."
    assert msg.role == "agent"

    tool = next(e for e in events if e.kind == AgentEventKind.tool_call)
    assert tool.title == "run_shell"
    assert "ls" in tool.body

    metric = next(e for e in events if e.kind == AgentEventKind.metric)
    assert metric.data["model"] == "gpt-4o"
    assert metric.data["input_tokens"] == "123"
    assert metric.data["output_tokens"] == "45"

    # framework-neutral: no subkind, provenance + source_agent carried through
    assert all(e.subkind is None for e in events)
    assert all(e.source_agent == "some-other-agent" for e in events)
    assert all(e.raw_ref.span_id == "abc123" and e.raw_ref.raw_seq == 3 for e in events)
    assert all(e.ts.tzinfo is not None for e in events)


def test_content_as_list_of_parts_is_joined():
    out_msgs = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "part-a"}, {"type": "text", "text": "part-b"}],
        }
    ]
    span = _span({"gen_ai.output.messages": json.dumps(out_msgs)})
    msg = next(
        e for e in GenAiSemconvExtractor().extract(span, _CTX) if e.kind == AgentEventKind.message
    )
    assert msg.body == "part-a\npart-b"


def test_missing_usage_tolerated_no_metric():
    out_msgs = [{"role": "assistant", "content": "hi"}]
    span = _span({"gen_ai.output.messages": json.dumps(out_msgs)})  # no usage, no model
    events = GenAiSemconvExtractor().extract(span, _CTX)
    assert [e.kind for e in events] == [AgentEventKind.message]


def test_missing_messages_tolerated_metric_only():
    span = _span(
        {"gen_ai.request.model": "gpt-4o", "gen_ai.usage.output_tokens": "9"}
    )  # no messages
    events = GenAiSemconvExtractor().extract(span, _CTX)
    assert [e.kind for e in events] == [AgentEventKind.metric]


def test_malformed_messages_json_does_not_raise():
    span = _span({"gen_ai.output.messages": "{not valid json", "gen_ai.request.model": "gpt-4o"})
    events = GenAiSemconvExtractor().extract(span, _CTX)  # must not raise
    assert [e.kind for e in events] == [AgentEventKind.metric]


def test_ids_are_deterministic():
    out_msgs = [
        {
            "role": "assistant",
            "content": "x",
            "tool_calls": [{"function": {"name": "t", "arguments": "{}"}}],
        }
    ]
    span = _span({"gen_ai.output.messages": json.dumps(out_msgs), "gen_ai.request.model": "m"})
    ids_a = [e.id for e in GenAiSemconvExtractor().extract(span, _CTX)]
    ids_b = [e.id for e in GenAiSemconvExtractor().extract(span, _CTX)]
    assert ids_a == ids_b
    assert len(set(ids_a)) == len(ids_a)  # unique


def test_ids_unique_across_multiple_messages_and_tools():
    """The real collision risk: N output messages each with M tool_calls -> all ids unique."""
    out_msgs = [
        {
            "role": "assistant",
            "content": f"msg-{i}",
            "tool_calls": [{"function": {"name": f"t{i}{j}", "arguments": "{}"}} for j in range(2)],
        }
        for i in range(2)
    ]
    span = _span({"gen_ai.output.messages": json.dumps(out_msgs), "gen_ai.request.model": "m"})
    ids_a = [e.id for e in GenAiSemconvExtractor().extract(span, _CTX)]
    ids_b = [e.id for e in GenAiSemconvExtractor().extract(span, _CTX)]
    assert ids_a == ids_b  # deterministic across calls
    assert len(set(ids_a)) == len(ids_a)  # 2 msgs + 4 tool_calls + 1 metric, all unique
    assert len(ids_a) == 7


def test_pathological_start_ns_falls_back_and_does_not_raise():
    """A malformed startTimeUnixNano beyond datetime's range must not crash the shared helper."""
    out_msgs = [{"role": "assistant", "content": "hi"}]
    base = _span({"gen_ai.output.messages": json.dumps(out_msgs), "gen_ai.request.model": "m"})
    span = replace(base, start_ns=10**28)  # int64-overflowing nanos
    events = GenAiSemconvExtractor().extract(span, _CTX)  # must not raise
    assert events  # message + metric still produced
    # ts falls back to the tz-aware ctx.created_at rather than raising OverflowError
    assert all(e.ts == _CTX.created_at for e in events)
    assert all(e.ts.tzinfo is not None for e in events)


def test_handles_real_openhands_llm_span_shape():
    """Smoke: the real fixture's litellm.completion spans (gen_ai.* + lmnr) extract cleanly.
    (owns the OpenHands golden; here we only prove shape-compatibility.)"""
    fixture = Path(__file__).resolve().parents[1] / "fixtures/otlp/openhands_real_run.json"
    doc = json.loads(fixture.read_text())
    spans: list[FlatSpan] = []
    for rec in doc["records"]:
        spans.extend(flatten(rec["payload"], raw_seq=rec.get("seq", 0)))
    llm = [s for s in spans if GenAiSemconvExtractor.is_llm_span(s)]
    assert len(llm) >= 10  # 10 litellm.completion spans
    events = GenAiSemconvExtractor().extract(llm[0], _CTX)
    assert any(e.kind == AgentEventKind.message for e in events)
    metric = next(e for e in events if e.kind == AgentEventKind.metric)
    assert "claude" in metric.data["model"].lower()

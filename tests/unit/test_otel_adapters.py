# tests/unit/test_otel_adapters.py
"""GenericOtelAdapter (parse-trace.ts parity) + registry selection + normalize_run."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from xorcise.core.contracts.agent_event import (
    AgentEvent,
    AgentEventKind,
    HarnessCapabilityProfile,
    RawTraceRef,
)
from xorcise.core.otel.adapters import normalize_run, registry
from xorcise.core.otel.adapters.base import AdapterContext, AgentTraceAdapter, profile_from
from xorcise.core.otel.adapters.generic import GenericOtelAdapter, classify, pick_body
from xorcise.core.otel.flatten import FlatSpan

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/otlp/openhands_real_run.json"


def _fixture_records() -> list[Mapping[str, Any]]:
    doc = json.loads(_FIXTURE.read_text())
    return [{"seq": r["seq"], "payload": r["payload"]} for r in doc["records"]]


def _span(**over: Any) -> FlatSpan:
    base: dict[str, Any] = dict(
        span_id="sp1",
        parent_span_id="",
        trace_id="tr1",
        name="agent.step",
        start_ns=0,
        end_ns=0,
        status_code=0,
        attrs={},
        scope="",
        resource={},
        raw_seq=0,
    )
    base.update(over)
    return FlatSpan(**base)


def _ctx(**over: Any) -> AdapterContext:
    base: dict[str, Any] = dict(
        run_id="run1",
        source_agent="generic",
        mission_id="chal1",
        created_at=datetime(2026, 7, 3, tzinfo=UTC),
    )
    base.update(over)
    return AdapterContext(**base)


# ── classify() — 1:1 port of parse-trace.ts classify(), first-match-wins ────────────


@pytest.mark.parametrize(
    "name,attrs,status_code,expected",
    [
        ("litellm.completion", {}, 0, AgentEventKind.message),
        ("_execute_action_event", {}, 0, AgentEventKind.terminal_command),  # matches "exec"
        ("TerminalAction", {}, 0, AgentEventKind.terminal_command),
        ("FileEditorAction", {}, 0, AgentEventKind.tool_call),  # matches "edit"
        ("agent.step", {}, 0, AgentEventKind.tool_call),  # default
        ("conversation.send_message", {}, 0, AgentEventKind.message),
        ("something_error_happened", {}, 0, AgentEventKind.error),
        ("capture_the_flag", {}, 0, AgentEventKind.flag),
        ("mcp.call_tool", {}, 0, AgentEventKind.mcp_call),
        ("noop", {"mcp.tool": "x"}, 0, AgentEventKind.mcp_call),
        ("noop", {}, 2, AgentEventKind.error),  # status_code == 2
        ("think_step", {}, 0, AgentEventKind.thinking),
    ],
)
def test_classify_matches_parse_trace_ts_order(
    name: str, attrs: dict[str, str], status_code: int, expected: AgentEventKind
) -> None:
    assert classify(name, attrs, status_code) == expected


def test_pick_body_prefers_known_keys_in_declared_order() -> None:
    assert pick_body({"path": "/x", "command": "ls -la"}) == "ls -la"
    assert pick_body({"path": "/x"}) == "/x"
    assert pick_body({"only": "one"}) == "only: one"
    assert pick_body({}) == ""


# ── GenericOtelAdapter.normalize() — AgentEvent construction ────────────────────────


def test_normalize_builds_agent_event_with_raw_ref_ts_body_data() -> None:
    span = _span(
        span_id="abc123",
        parent_span_id="parent1",
        trace_id="trace1",
        name="TerminalAction",
        start_ns=1_751_500_000_000_000_000,
        raw_seq=3,
        attrs={"command": "ls -la", "lmnr.span.type": "TOOL"},
    )
    ctx = _ctx()

    [event] = GenericOtelAdapter().normalize([span], ctx)

    assert isinstance(event, AgentEvent)
    assert event.id == "abc123"
    assert event.kind == AgentEventKind.terminal_command
    assert event.title == "TerminalAction"
    assert event.body == "ls -la"
    assert dict(event.data)["lmnr.span.type"] == "TOOL"
    assert event.raw_ref.run_id == "run1"
    assert event.raw_ref.raw_seq == 3
    assert event.raw_ref.span_id == "abc123"
    assert event.raw_ref.trace_id == "trace1"
    assert event.parent_id == "parent1"
    assert event.group_id is None
    assert event.role == "agent"
    assert event.severity == "info"
    assert event.status is None
    assert event.source_agent == "generic"
    assert event.ts == datetime.fromtimestamp(1_751_500_000_000_000_000 / 1e9, tz=UTC)


def test_normalize_populates_duration_ms_from_span_end() -> None:
    span = _span(
        name="TerminalAction",
        start_ns=1_751_500_000_000_000_000,
        end_ns=1_751_500_000_000_000_000 + 2_500_000_000,  # +2.5s
    )
    [event] = GenericOtelAdapter().normalize([span], _ctx())
    assert event.duration_ms == 2500


def test_normalize_duration_ms_none_when_no_end() -> None:
    span = _span(name="TerminalAction", start_ns=1_751_500_000_000_000_000, end_ns=0)
    [event] = GenericOtelAdapter().normalize([span], _ctx())
    assert event.duration_ms is None


def test_normalize_falls_back_to_sha1_id_and_ctx_ts_when_span_id_and_start_ns_missing() -> None:
    span = _span(span_id="", name="agent.step", raw_seq=5, start_ns=0)
    ctx = _ctx()

    [event] = GenericOtelAdapter().normalize([span], ctx)

    assert event.id  # non-empty, stable, derived
    assert len(event.id) == 16
    assert event.ts == ctx.created_at


def test_normalize_stable_id_is_deterministic_for_same_span() -> None:
    span = _span(span_id="", name="agent.step", raw_seq=5, start_ns=0)
    ctx = _ctx()

    ev1 = GenericOtelAdapter().normalize([span], ctx)[0]
    ev2 = GenericOtelAdapter().normalize([span], ctx)[0]

    assert ev1.id == ev2.id


def test_normalize_error_kind_sets_severity_and_status() -> None:
    span = _span(name="tool_error")
    ctx = _ctx()

    [event] = GenericOtelAdapter().normalize([span], ctx)

    assert event.kind == AgentEventKind.error
    assert event.severity == "error"
    assert event.status == "error"


def test_normalize_no_parent_span_id_yields_none() -> None:
    span = _span(parent_span_id="")
    ctx = _ctx()

    [event] = GenericOtelAdapter().normalize([span], ctx)

    assert event.parent_id is None


def test_adapter_name_and_version() -> None:
    adapter = GenericOtelAdapter()
    assert adapter.name == "generic"
    assert adapter.version == "1"


# ── registry.select() ────────────────────────────────────────────────────────────────


def test_select_registered_source_agent_returns_exact_adapter_no_fallback() -> None:
    adapter, fallback = registry.select("generic", [])
    assert adapter.name == "generic"
    assert fallback is False


def test_select_unknown_source_agent_falls_back_to_generic() -> None:
    adapter, fallback = registry.select("totally-unknown-agent", [])
    assert adapter.name == "generic"
    assert fallback is True


def test_registered_names_includes_generic() -> None:
    assert "generic" in registry.registered_names()


# ── framework guard: a brand-new adapter needs ONLY registration ────────────────────


def test_framework_guard_new_adapter_needs_only_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves the framework is extensible w/o touching select()/generic.py: registering a
    throwaway FakeAdapter under a new name makes select() return it (no fallback) and its
    own normalize() output — not generic's — is what gets used."""

    class FakeAdapter(AgentTraceAdapter):
        name = "fake"
        version = "1"

        @property
        def capabilities(self) -> HarnessCapabilityProfile:
            return profile_from(self.name, self.version)

        def normalize(self, spans: list[FlatSpan], ctx: AdapterContext) -> list[AgentEvent]:
            return [
                AgentEvent(
                    run_id=ctx.run_id,
                    id=f"fake-{i}",
                    ts=ctx.created_at,
                    source_agent=ctx.source_agent,
                    kind=AgentEventKind.unknown,
                    title="fake",
                    raw_ref=RawTraceRef(run_id=ctx.run_id, raw_seq=0, span_id=f"fake-{i}"),
                )
                for i, _ in enumerate(spans)
            ]

    # Snapshot + restore so this registration never leaks into other tests.
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    registry.register(FakeAdapter())

    assert "fake" in registry.registered_names()

    adapter, fallback = registry.select("fake", [_span(), _span()])
    assert adapter.name == "fake"
    assert fallback is False

    events = adapter.normalize([_span(), _span()], _ctx())
    assert [e.id for e in events] == ["fake-0", "fake-1"]
    assert events[0].kind == AgentEventKind.unknown  # FakeAdapter's own mapping, not generic's


# ── normalize_run() -> RunEventsView ────────────────────────────────────────────────


def test_normalize_run_real_fixture_yields_all_events_sorted_by_seq_no_adapter_yet() -> None:
    records = _fixture_records()
    ctx = _ctx(
        run_id="2d6d1c2e-real-run",
        source_agent="unknown-agent",
        mission_id="sqli-login",
        created_at=datetime(2026, 7, 3, tzinfo=UTC),
    )

    view = normalize_run(records, ctx)

    assert view.adapter_name == "generic"  # no adapter registered for "unknown-agent"
    assert view.fallback is True
    assert len(view.events) == 35
    assert view.counts["total"] == 35
    seqs = [e.raw_ref.raw_seq for e in view.events]
    assert seqs == sorted(seqs)
    assert view.next_since == 9
    assert view.next_cursor.trace_seq == 9
    assert view.next_cursor.log_seq == -1
    assert view.warnings == ()
    assert view.run_id == ctx.run_id
    assert view.source_agent == "unknown-agent"


def test_normalize_run_tolerates_json_string_payload() -> None:
    records = _fixture_records()
    records[0] = {"seq": records[0]["seq"], "payload": json.dumps(records[0]["payload"])}

    view = normalize_run(records, _ctx(source_agent="unknown-agent"))

    assert view.counts["total"] == 35  # same span count as the all-dict-payload run


def test_normalize_run_malformed_json_string_skips_that_records_spans_not_raises() -> None:
    records = _fixture_records()
    records[0] = {"seq": records[0]["seq"], "payload": "{not valid json"}

    view = normalize_run(records, _ctx(source_agent="unknown-agent"))

    assert view.counts["total"] == 34  # record seq=0's single span dropped, not raised
    assert view.next_since == 9  # the malformed record's seq still counts toward next_since


def test_normalize_run_adapter_exception_yields_one_error_event_and_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Framework guard: an adapter that raises is contained — never propagates."""

    class BoomAdapter(AgentTraceAdapter):
        name = "boom"
        version = "1"

        @property
        def capabilities(self) -> HarnessCapabilityProfile:
            return profile_from(self.name, self.version)

        def normalize(self, spans: list[FlatSpan], ctx: AdapterContext) -> list[AgentEvent]:
            raise RuntimeError("kaboom")

    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    registry.register(BoomAdapter())

    boom_records: list[Mapping[str, Any]] = [{"seq": 0, "payload": {}}]
    view = normalize_run(boom_records, _ctx(source_agent="boom"))

    assert len(view.events) == 1
    assert view.events[0].kind == AgentEventKind.error
    assert view.counts["total"] == 1
    assert len(view.warnings) == 1
    assert view.warnings[0].code == "adapter_error"
    assert "kaboom" in view.warnings[0].message
    assert view.adapter_name == "boom"
    assert view.fallback is False


def test_normalize_run_tolerates_naive_created_at_with_mixed_ts() -> None:
    """A tz-naive `ctx.created_at` must not blow up sorting against tz-aware span ts's:
    one span has a real `startTimeUnixNano` (aware ts), the other doesn't (falls back to
    `created_at`, coerced to UTC-aware) — comparing them must not raise `TypeError`."""
    naive_ctx = _ctx(created_at=datetime(2026, 7, 3))
    payload = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {"name": "with_ts", "startTimeUnixNano": 1_751_500_000_000_000_000},
                            {"name": "without_ts"},
                        ]
                    }
                ]
            }
        ]
    }

    naive_records: list[Mapping[str, Any]] = [{"seq": 0, "payload": payload}]
    view = normalize_run(naive_records, naive_ctx)

    assert view.counts["total"] == 2
    assert len(view.events) == 2


def test_normalize_run_skips_structurally_broken_record() -> None:
    """A record missing `seq`/`payload` (or the wrong shape) must be skipped, not raise."""
    records: list[Mapping[str, Any]] = [
        {"seq": 0, "payload": {"resourceSpans": [{"scopeSpans": [{"spans": [{"name": "ok"}]}]}]}},
        {},  # structurally broken: no seq/payload
    ]

    view = normalize_run(records, _ctx(source_agent="openhands"))

    assert view.counts["total"] == 1
    assert view.events[0].title == "ok"


def test_normalize_run_keeps_delayed_older_event_in_producer_chronology() -> None:
    """Receipt is an observation time, not an occurrence time: a late export containing an older
    producer event must not move that event behind a response exported earlier."""
    early_receipt = datetime(2026, 7, 25, 1, 0, 1, tzinfo=UTC)
    late_receipt = datetime(2026, 7, 25, 1, 0, 10, tzinfo=UTC)
    records: list[Mapping[str, Any]] = [
        {
            "seq": 0,
            "received_at": early_receipt,
            "payload": {
                "resourceSpans": [
                    {
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "spanId": "response-received-first",
                                        "name": "response",
                                        "startTimeUnixNano": "2000000000",
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
        },
        {
            "seq": 1,
            "received_at": late_receipt,
            "payload": {
                "resourceSpans": [
                    {
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "spanId": "delayed-prompt",
                                        "name": "prompt",
                                        "startTimeUnixNano": "1000000000",
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
        },
    ]

    view = normalize_run(records, _ctx(source_agent="generic"))

    assert [event.id for event in view.events] == ["delayed-prompt", "response-received-first"]
    assert [event.received_at for event in view.events] == [late_receipt, early_receipt]


def test_normalize_run_uses_producer_time_within_one_receipt_batch() -> None:
    received = datetime(2026, 7, 25, 1, 0, 1, tzinfo=UTC)
    records: list[Mapping[str, Any]] = [
        {
            "seq": 0,
            "received_at": received.isoformat(),
            "payload": {
                "resourceSpans": [
                    {
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "spanId": "later",
                                        "name": "later",
                                        "startTimeUnixNano": "2000000000",
                                    },
                                    {
                                        "spanId": "earlier",
                                        "name": "earlier",
                                        "startTimeUnixNano": "1000000000",
                                    },
                                ]
                            }
                        ]
                    }
                ]
            },
        }
    ]

    view = normalize_run(records, _ctx(source_agent="generic"))

    assert [event.id for event in view.events] == ["earlier", "later"]
    assert all(event.received_at == received for event in view.events)

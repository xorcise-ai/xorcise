# tests/unit/test_openhands_adapter.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from xorcise.core.contracts.agent_event import AgentEventKind
from xorcise.core.harness_adapters.openhands import otel as openhands  # noqa: F401 — self-register
from xorcise.core.harness_adapters.openhands.otel import OpenHandsAdapter
from xorcise.core.otel.adapters.base import AdapterContext
from xorcise.core.otel.adapters.genai import GenAiSemconvExtractor
from xorcise.core.otel.adapters.registry import select
from xorcise.core.otel.flatten import FlatSpan, flatten

_FIX = Path(__file__).resolve().parents[1] / "fixtures/otlp/openhands_real_run.json"
_GOLDEN = Path(__file__).resolve().parents[1] / "fixtures/otlp/openhands_events_golden.json"
_CTX = AdapterContext(
    run_id="2d6d0c9f32e8481abd8b2175af345b93",
    source_agent="openhands",
    mission_id="sqli-login",
    created_at=datetime(2026, 7, 3, tzinfo=UTC),
)


def _fixture_spans() -> list[FlatSpan]:
    doc = json.loads(_FIX.read_text())
    spans: list[FlatSpan] = []
    for rec in doc["records"]:
        spans.extend(flatten(rec["payload"], raw_seq=rec.get("seq", 0)))
    return spans


def _project(events) -> list[dict[str, object]]:
    """A stable, readable projection for the golden (avoids ts/order churn; sorted)."""
    rows = [
        {
            "id": e.id,
            "kind": e.kind.value,
            "role": e.role,
            "title": e.title,
            "body_head": e.body[:80],
            "group_id": e.group_id,
            "status": e.status,
        }
        for e in events
    ]
    return sorted(rows, key=lambda r: r["id"])


def test_openhands_selected_by_source_agent():
    adapter, fallback = select("openhands", _fixture_spans())
    assert adapter.name == "openhands"
    assert fallback is False


def test_maps_the_real_fixture_kinds():
    events = OpenHandsAdapter().normalize(_fixture_spans(), _CTX)
    kinds = {e.kind for e in events}
    # conversation.send_message -> message(user); _execute_action_event -> thinking;
    # TerminalAction -> terminal_command + terminal_output; LLM -> message/tool_call/metric
    assert AgentEventKind.message in kinds
    assert AgentEventKind.thinking in kinds
    assert AgentEventKind.terminal_command in kinds
    assert AgentEventKind.terminal_output in kinds
    assert AgentEventKind.metric in kinds  # delegated LLM usage
    # a user message from conversation.send_message
    user_msgs = [e for e in events if e.kind == AgentEventKind.message and e.role == "user"]
    assert user_msgs and "security agent" in user_msgs[0].body.lower()
    # a terminal command carrying the join/curl
    cmds = [e for e in events if e.kind == AgentEventKind.terminal_command]
    assert any("curl" in e.body or "tailscale" in e.body for e in cmds)


def test_llm_spans_are_delegated_to_the_shared_extractor():
    spans = _fixture_spans()
    llm = next(s for s in spans if s.name == "litellm.completion")
    direct = GenAiSemconvExtractor().extract(llm, _CTX)
    got = OpenHandsAdapter().normalize([llm], _CTX)
    # The adapter delegates the non-tool_call events (assistant text + usage metric) verbatim to the
    # shared extractor. tool_calls are handled separately by the action-span correlation (drop when
    # duplicated by an action span, surface orphans — see the dedicated tests), which can't run when
    # a single LLM span is normalized in isolation, so compare only the delegated non-tool events.
    got_non_tool = [e for e in got if e.kind is not AgentEventKind.tool_call]
    kept = [e for e in direct if e.kind is not AgentEventKind.tool_call]
    assert [(e.id, e.kind) for e in got_non_tool] == [(e.id, e.kind) for e in kept]


def _flatspan(name: str, attrs: dict[str, str], *, span_id: str = "s1") -> FlatSpan:
    return FlatSpan(
        span_id=span_id,
        parent_span_id="",
        trace_id="t",
        name=name,
        start_ns=1_700_000_000_000_000_000,
        end_ns=0,
        status_code=0,
        attrs=attrs,
        scope="lmnr.tracer",
        resource={},
        raw_seq=0,
    )


def test_send_message_with_dict_content_maps_to_user_message():
    # Headless (`openhands --headless`) sends the mission as a structured message dict, NOT a plain
    # string: {"role":"user","content":[{"type":"text","text":...}]}. The user message must still
    # render (regression: str-only handling dropped it, so headless runs showed no user prompt).
    span = _flatspan(
        "conversation.send_message",
        {
            "lmnr.span.input": json.dumps(
                {
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "Run r1 — mission: idor-accounts"}],
                    }
                }
            )
        },
    )
    events = OpenHandsAdapter().normalize([span], _CTX)
    users = [e for e in events if e.kind == AgentEventKind.message and e.role == "user"]
    assert len(users) == 1
    assert "idor-accounts" in users[0].body


def test_llm_tool_call_dropped_when_it_duplicates_an_action_span():
    # A litellm.completion whose assistant turn calls `terminal` with a command that OpenHands ALSO
    # ran via a dedicated TerminalAction span: the LLM tool_call is a duplicate and must be dropped
    # (the action span is canonical), but the assistant text + usage metric are kept.
    llm = _flatspan(
        "litellm.completion",
        {
            "lmnr.span.type": "LLM",
            "gen_ai.request.model": "bedrock/claude",
            "gen_ai.usage.input_tokens": "100",
            "gen_ai.usage.output_tokens": "20",
            "gen_ai.output.messages": json.dumps(
                [
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Running the exploit."}],
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {
                                    "name": "terminal",
                                    "arguments": json.dumps({"command": "curl /mission"}),
                                },
                            }
                        ],
                    }
                ]
            ),
        },
        span_id="llm1",
    )
    action = _flatspan(
        "TerminalAction",
        {"lmnr.span.input": json.dumps({"action": {"command": "curl /mission"}})},
        span_id="act1",
    )
    kinds = [e.kind for e in OpenHandsAdapter().normalize([llm, action], _CTX)]
    assert AgentEventKind.tool_call not in kinds  # duplicate of the TerminalAction → dropped
    assert AgentEventKind.terminal_command in kinds  # rendered by the action span
    assert AgentEventKind.message in kinds
    assert AgentEventKind.metric in kinds


def test_orphan_tool_call_without_an_action_span_is_surfaced():
    # Regression: OpenHands ran a high-security-risk command (the tailnet join) but emitted
    # NO TerminalAction span for it — only the LLM tool_call. Blanket-dropping tool_calls made that
    # real action invisible in the trace. A tool_call whose command has no matching action span is
    # now surfaced; a sibling command that DOES have an action span is still de-duplicated.
    llm = _flatspan(
        "litellm.completion",
        {
            "lmnr.span.type": "LLM",
            "gen_ai.output.messages": json.dumps(
                [
                    {
                        "role": "assistant",
                        "content": [],
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {
                                    "name": "terminal",
                                    "arguments": json.dumps(
                                        {
                                            "command": 'curl -fsS "$BASE/join.sh" | sh',
                                            "security_risk": "HIGH",
                                        }
                                    ),
                                },
                            },
                            {
                                "id": "c2",
                                "function": {
                                    "name": "terminal",
                                    "arguments": json.dumps({"command": "curl /mission"}),
                                },
                            },
                        ],
                    }
                ]
            ),
        },
        span_id="llm1",
    )
    # only the SECOND command has a canonical TerminalAction span; the join (first) is an orphan
    action = _flatspan(
        "TerminalAction",
        {"lmnr.span.input": json.dumps({"action": {"command": "curl /mission"}})},
        span_id="act1",
    )
    events = OpenHandsAdapter().normalize([llm, action], _CTX)
    tool_calls = [e for e in events if e.kind is AgentEventKind.tool_call]
    assert len(tool_calls) == 1  # only the orphan survives
    assert "join.sh" in tool_calls[0].body  # the invisible join is now shown
    # the correlated command renders via its action span, and NOT as a duplicate tool_call
    cmds = [e for e in events if e.kind is AgentEventKind.terminal_command]
    assert any("curl /mission" in e.body for e in cmds)
    # the join had no action span → NOT rendered as a terminal_command (only as the tool_call)
    assert not any("join.sh" in e.body for e in cmds)


def test_think_action_maps_to_thinking():
    span = _flatspan(
        "ThinkAction",
        {
            "lmnr.span.type": "TOOL",
            "lmnr.span.input": json.dumps(
                {"action": {"thought": "Two tailscaled instances are running.", "kind": "Think"}}
            ),
        },
    )
    events = OpenHandsAdapter().normalize([span], _CTX)
    assert [e.kind for e in events] == [AgentEventKind.thinking]
    assert "tailscaled" in events[0].body


def test_task_tracker_action_maps_to_single_card_with_task_list():
    span = _flatspan(
        "TaskTrackerAction",
        {
            "lmnr.span.type": "TOOL",
            "lmnr.span.input": json.dumps(
                {
                    "action": {
                        "command": "plan",
                        "task_list": [
                            {"title": "Install tailscale", "status": "done"},
                            {"title": "Exploit IDOR", "status": "todo"},
                        ],
                    }
                }
            ),
        },
    )
    events = OpenHandsAdapter().normalize([span], _CTX)
    assert len(events) == 1  # ONE card, not a tool_call + an empty unknown
    assert events[0].kind != AgentEventKind.unknown
    assert "Exploit IDOR" in events[0].body


def test_unknown_openhands_span_becomes_unknown_no_crash():
    span = FlatSpan(
        span_id="x1",
        parent_span_id="",
        trace_id="t",
        name="SomeFutureAction",
        start_ns=1_700_000_000_000_000_000,
        end_ns=0,
        status_code=0,
        attrs={"lmnr.span.type": "TOOL"},
        scope="lmnr.tracer",
        resource={},
        raw_seq=0,
    )
    events = OpenHandsAdapter().normalize([span], _CTX)
    assert [e.kind for e in events] == [AgentEventKind.unknown]


def test_agent_step_emits_no_event_but_groups_children():
    events = OpenHandsAdapter().normalize(_fixture_spans(), _CTX)
    assert all(e.kind != AgentEventKind.status or e.title != "agent.step" for e in events)
    # most events carry a group_id (the enclosing agent.step)
    assert any(e.group_id for e in events)


def test_totality_on_malformed_lmnr_blobs():
    span = FlatSpan(
        span_id="t1",
        parent_span_id="",
        trace_id="t",
        name="TerminalAction",
        start_ns=1_700_000_000_000_000_000,
        end_ns=0,
        status_code=0,
        attrs={"lmnr.span.input": "{bad json", "lmnr.span.output": "also bad"},
        scope="lmnr.tracer",
        resource={},
        raw_seq=0,
    )
    events = OpenHandsAdapter().normalize([span], _CTX)  # must not raise
    assert isinstance(events, list)


def test_golden_snapshot_matches():
    events = OpenHandsAdapter().normalize(_fixture_spans(), _CTX)
    got = _project(events)
    golden = json.loads(_GOLDEN.read_text())
    assert got == golden, (
        "OpenHands normalization drifted from the golden — regenerate + review the diff."
    )

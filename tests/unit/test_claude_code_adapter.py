# tests/unit/test_claude_code_adapter.py
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xorcise.core.contracts.agent_event import AgentEventKind
from xorcise.core.harness_adapters.claude_code import (
    otel as claude_code,  # noqa: F401 — self-register
)
from xorcise.core.harness_adapters.claude_code.otel import ClaudeCodeAdapter
from xorcise.core.otel.adapters.base import AdapterContext
from xorcise.core.otel.adapters.genai import GenAiSemconvExtractor
from xorcise.core.otel.adapters.registry import select
from xorcise.core.otel.flatten import FlatLogRecord, FlatSpan, FlatSpanEvent, flatten

_FIX = Path(__file__).resolve().parents[1] / "fixtures/otlp/claude_code_real_run.json"
_GOLDEN = Path(__file__).resolve().parents[1] / "fixtures/otlp/claude_code_events_golden.json"
_DOC = json.loads(_FIX.read_text())
_CTX = AdapterContext(
    run_id=_DOC["run_id"],
    source_agent="claude-code",
    mission_id="hello-world",
    created_at=datetime(2026, 7, 8, tzinfo=UTC),
)


def _fixture_spans() -> list[FlatSpan]:
    spans: list[FlatSpan] = []
    for rec in _DOC["records"]:
        spans.extend(flatten(rec["payload"], raw_seq=rec.get("seq", 0)))
    return spans


def _project(events) -> list[dict[str, object]]:
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


def test_claude_code_selected_by_source_agent():
    adapter, fallback = select("claude-code", _fixture_spans())
    assert adapter.name == "claude-code"
    assert fallback is False


def test_maps_the_real_fixture_kinds():
    events = ClaudeCodeAdapter().normalize(_fixture_spans(), _CTX)
    kinds = {e.kind for e in events}
    # interaction -> message(user); llm_request -> metric; tool(Write) -> file_edit (with the
    # execution outcome FOLDED onto it, v4); a decision=unknown gate is dropped as noise.
    assert AgentEventKind.message in kinds
    assert AgentEventKind.metric in kinds
    assert AgentEventKind.file_edit in kinds
    user_msgs = [e for e in events if e.kind == AgentEventKind.message and e.role == "user"]
    assert user_msgs and "hello.py" in user_msgs[0].body
    edits = [e for e in events if e.kind == AgentEventKind.file_edit]
    assert edits and edits[0].data.get("tool.name") == "Write" and edits[0].data.get("path")
    assert "hello from claude code" in edits[0].body  # tool.output content surfaced
    # v4: no standalone empty tool_result (the Write execution folded onto the file_edit) and no
    # decision=unknown permission-gate noise
    assert not any(e.kind == AgentEventKind.tool_result for e in events)
    assert not any(e.body == "decision=unknown" for e in events)
    # the delegated LLM metric was enriched with Claude Code's bespoke tokens
    metrics = [e for e in events if e.kind == AgentEventKind.metric]
    assert any("input_tokens" in e.data and e.body.startswith("in=") for e in metrics)


def test_llm_request_delegated_to_shared_extractor():
    spans = _fixture_spans()
    llm = next(s for s in spans if s.name == "claude_code.llm_request")
    direct = GenAiSemconvExtractor().extract(llm, _CTX)
    got = ClaudeCodeAdapter().normalize([llm], _CTX)
    # same events (ids + kinds) as the shared extractor — only group_id/body/data enriched
    assert [(e.id, e.kind) for e in got] == [(e.id, e.kind) for e in direct]


def test_tool_execution_error_semantics_from_success_attr():
    ok = FlatSpan(
        "s1",
        "",
        "t",
        "claude_code.tool.execution",
        1_700_000_000_000_000_000,
        0,
        0,
        {"success": "True", "tool_use_id": "tu1"},
        "com.anthropic.claude_code.tracing",
        {},
        0,
    )
    err = FlatSpan(
        "s2",
        "",
        "t",
        "claude_code.tool.execution",
        1_700_000_000_000_000_000,
        0,
        0,
        {"success": "False", "tool_use_id": "tu2"},
        "com.anthropic.claude_code.tracing",
        {},
        0,
    )
    ev_ok = ClaudeCodeAdapter().normalize([ok], _CTX)[0]
    ev_err = ClaudeCodeAdapter().normalize([err], _CTX)[0]
    assert ev_ok.kind == AgentEventKind.tool_result and ev_ok.status == "ok"
    assert ev_err.kind == AgentEventKind.tool_result and ev_err.status == "error"
    assert ev_err.severity == "error"


def test_unknown_claude_code_span_becomes_unknown_no_crash():
    span = FlatSpan(
        "x1",
        "",
        "t",
        "claude_code.future_thing",
        1_700_000_000_000_000_000,
        0,
        0,
        {"span.type": "future"},
        "com.anthropic.claude_code.tracing",
        {},
        0,
    )
    events = ClaudeCodeAdapter().normalize([span], _CTX)
    assert [e.kind for e in events] == [AgentEventKind.unknown]


def test_totality_on_missing_tool_attrs():
    span = FlatSpan(
        "t1",
        "",
        "t",
        "claude_code.tool",
        1_700_000_000_000_000_000,
        0,
        0,
        {},
        "com.anthropic.claude_code.tracing",
        {},
        0,
    )
    events = ClaudeCodeAdapter().normalize([span], _CTX)  # must not raise
    assert isinstance(events, list) and events and events[0].kind == AgentEventKind.tool_call


def _bash_span(span_id: str, parent: str, cmd: str) -> FlatSpan:
    return FlatSpan(
        span_id,
        parent,
        "t",
        "claude_code.tool",
        1_700_000_000_000_000_000,
        0,
        0,
        {"tool_name": "Bash", "full_command": cmd, "tool_use_id": span_id},
        "com.anthropic.claude_code.tracing",
        {},
        0,
        (FlatSpanEvent("tool.output", 0, {"bash_command": cmd, "output": f"out-{cmd}"}),),
    )


def test_group_id_falls_back_to_missing_interaction_parent():
    # Headless runs (`claude -p`) never export the claude_code.interaction root, so tool spans
    # reference a parent that isn't in the trace. _group_id must fall back to that (unresolved)
    # parent id — a stable per-turn key — so a command + its output still share a group instead
    # of each getting None (which split them into separate replay turns).
    missing = "A0Usa8ENtSQ="
    spans = [_bash_span("s1", missing, "ls"), _bash_span("s2", missing, "pwd")]
    events = ClaudeCodeAdapter().normalize(spans, _CTX)
    term = [
        e
        for e in events
        if e.kind in (AgentEventKind.terminal_command, AgentEventKind.terminal_output)
    ]
    assert term, "expected terminal command + output events"
    assert {e.group_id for e in term} == {missing}  # all under the same (missing) interaction


def test_user_prompt_log_maps_to_user_message():
    # Headless runs never export the interaction span (the usual source of the user prompt), but
    # they DO emit a user_prompt LOG — map it so the agent's task is still shown.
    logs = [
        FlatLogRecord(
            event_name="claude_code.user_prompt",
            time_ns=1_700_000_000_000_000_000,
            attrs={"prompt": "solve the CTF", "prompt.id": "p1"},
            body="claude_code.user_prompt",
            raw_seq=0,
        )
    ]
    events = ClaudeCodeAdapter().normalize_logs(logs, _CTX)
    users = [e for e in events if e.kind == AgentEventKind.message and e.role == "user"]
    assert users and users[0].body == "solve the CTF"


def _otlp_span_payload(
    name: str, span_id: str, parent: str, attrs: dict[str, str]
) -> dict[str, Any]:
    return {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "scope": {"name": "com.anthropic.claude_code.tracing"},
                        "spans": [
                            {
                                "name": name,
                                "spanId": span_id,
                                "parentSpanId": parent,
                                "traceId": "t",
                                "startTimeUnixNano": 1_700_000_000_000_000_000,
                                "attributes": [
                                    {"key": k, "value": {"stringValue": v}}
                                    for k, v in attrs.items()
                                ],
                            }
                        ],
                    }
                ]
            }
        ]
    }


def _otlp_log_payload(event_name: str, attrs: dict[str, str]) -> dict[str, Any]:
    return {
        "resourceLogs": [
            {
                "scopeLogs": [
                    {
                        "scope": {"name": "com.anthropic.claude_code.events"},
                        "logRecords": [
                            {
                                "body": {"stringValue": event_name},
                                "timeUnixNano": 1_700_000_000_000_000_000,
                                "attributes": [
                                    {"key": "event.name", "value": {"stringValue": event_name}},
                                    *(
                                        {"key": k, "value": {"stringValue": v}}
                                        for k, v in attrs.items()
                                    ),
                                ],
                            }
                        ],
                    }
                ]
            }
        ]
    }


def test_user_prompt_not_duplicated_across_span_and_log():
    # A run could carry the prompt in BOTH the interaction span and the user_prompt log; the
    # merged view must show it exactly once (dedup by body).
    from xorcise.core.otel.adapters import normalize_run

    records: list[Mapping[str, Any]] = [
        {
            "seq": 0,
            "payload": _otlp_span_payload(
                "claude_code.interaction", "I1", "", {"user_prompt": "hi there"}
            ),
        }
    ]
    logs: list[Mapping[str, Any]] = [
        {
            "seq": 0,
            "payload": _otlp_log_payload(
                "claude_code.user_prompt", {"prompt": "hi there", "prompt.id": "p1"}
            ),
        }
    ]
    view = normalize_run(records, _CTX, log_records=logs)
    users = [e for e in view.events if e.kind == AgentEventKind.message and e.role == "user"]
    assert len(users) == 1, f"expected one user message, got {len(users)}"


def test_interaction_emits_user_message_and_groups_children():
    events = ClaudeCodeAdapter().normalize(_fixture_spans(), _CTX)
    assert any(e.kind == AgentEventKind.message and e.role == "user" for e in events)
    assert any(e.group_id for e in events)  # children grouped under the interaction turn


def test_tool_output_event_surfaces_content():
    # Write -> file_edit body is the written content (from the tool.output span event)
    write = FlatSpan(
        "w1",
        "",
        "t",
        "claude_code.tool",
        1_700_000_000_000_000_000,
        0,
        0,
        {"tool_name": "Write", "file_path": "/tmp/x.py", "tool_use_id": "tu1"},
        "com.anthropic.claude_code.tracing",
        {},
        0,
        (FlatSpanEvent("tool.output", 0, {"file_path": "/tmp/x.py", "content": "print(1)"}),),
    )
    (ev,) = ClaudeCodeAdapter().normalize([write], _CTX)
    assert ev.kind == AgentEventKind.file_edit and ev.body == "print(1)"
    # Bash -> terminal_command + a terminal_output carrying the command output
    bash = FlatSpan(
        "b1",
        "",
        "t",
        "claude_code.tool",
        1_700_000_000_000_000_000,
        0,
        0,
        {"tool_name": "Bash", "full_command": "ls -la", "tool_use_id": "tu2"},
        "com.anthropic.claude_code.tracing",
        {},
        0,
        # real Claude Code bash puts the OUTPUT in the `output` event key (NOT `content`)
        # and the command in `bash_command` — v2 read only `content` so shell output was dropped.
        (
            FlatSpanEvent(
                "tool.output", 0, {"bash_command": "ls -la", "output": "total 0\ndrwxr-xr-x"}
            ),
        ),
    )
    evs = ClaudeCodeAdapter().normalize([bash], _CTX)
    kinds = [e.kind for e in evs]
    assert AgentEventKind.terminal_command in kinds and AgentEventKind.terminal_output in kinds
    out = next(e for e in evs if e.kind == AgentEventKind.terminal_output)
    assert "total 0" in out.body and out.role == "tool"


def test_bash_command_falls_back_to_event_attr_and_execution_folds_onto_card():
    # command falls back to the bash_command event attr when full_command is absent;
    # and the paired tool.execution outcome (ok + duration) is FOLDED onto the command card —
    # NOT emitted as a separate empty "Bash result".
    tool = FlatSpan(
        "b9",
        "",
        "t",
        "claude_code.tool",
        1_700_000_000_000_000_000,
        0,
        0,
        {"tool_name": "Bash", "tool_use_id": "tuX"},  # no full_command attr
        "com.anthropic.claude_code.tracing",
        {},
        0,
        (FlatSpanEvent("tool.output", 0, {"bash_command": "id -u", "output": "1000"}),),
    )
    execu = FlatSpan(
        "e9",
        "",
        "t",
        "claude_code.tool.execution",
        1_700_000_000_000_000_001,
        0,
        0,
        {"tool_use_id": "tuX", "success": "True", "duration_ms": "12"},
        "com.anthropic.claude_code.tracing",
        {},
        0,
    )
    evs = ClaudeCodeAdapter().normalize([tool, execu], _CTX)
    cmd = next(e for e in evs if e.kind == AgentEventKind.terminal_command)
    assert cmd.body == "id -u"  # from the bash_command event attr
    assert cmd.status == "ok" and cmd.data.get("duration_ms") == "12"  # execution FOLDED onto it
    out = next(e for e in evs if e.kind == AgentEventKind.terminal_output)
    assert out.body == "1000"
    # v4: the execution folded — no separate empty tool_result card
    assert not any(e.kind == AgentEventKind.tool_result for e in evs)


def test_permission_gate_unknown_dropped_but_reject_kept():
    # a decision=unknown gate is auto-handled noise (dropped); a real block is kept.
    def gate(decision: str) -> FlatSpan:
        return FlatSpan(
            "g",
            "",
            "t",
            "claude_code.tool.blocked_on_user",
            1_700_000_000_000_000_000,
            0,
            0,
            {"decision": decision},
            "com.anthropic.claude_code.tracing",
            {},
            0,
        )

    assert ClaudeCodeAdapter().normalize([gate("unknown")], _CTX) == []
    rej = ClaudeCodeAdapter().normalize([gate("reject")], _CTX)
    assert len(rej) == 1
    assert rej[0].kind == AgentEventKind.status and "reject" in rej[0].body


def test_normalize_logs_maps_assistant_response_to_agent_message():
    # the assistant's response TEXT rides the OTLP logs signal (not spans).
    from xorcise.core.otel.flatten import FlatLogRecord

    rec = FlatLogRecord(
        event_name="claude_code.assistant_response",
        time_ns=1_700_000_000_000_000_000,
        attrs={"response": "SQL injection is a vulnerability…", "model": "claude-opus-4-8"},
        body="claude_code.assistant_response",
        raw_seq=0,
    )
    (ev,) = ClaudeCodeAdapter().normalize_logs([rec], _CTX)
    assert ev.kind == AgentEventKind.message and ev.role == "agent"
    assert ev.body == "SQL injection is a vulnerability…"
    assert ev.data.get("model") == "claude-opus-4-8"


def test_normalize_logs_maps_api_refusal_to_clean_error():
    rec = FlatLogRecord(
        event_name="claude_code.api_refusal",
        time_ns=1_700_000_000_000_000_000,
        attrs={
            "model": "claude-opus-4-8",
            "category": "cyber",
            "request_id": "req_refused",
            "attempt": "1",
            "has_explanation": "True",
            "user.email": "operator@example.com",
        },
        body="claude_code.api_refusal",
        raw_seq=3,
    )

    (event,) = ClaudeCodeAdapter().normalize_logs([rec], _CTX)

    assert event.kind is AgentEventKind.error
    assert event.subkind == "model_refusal"
    assert event.title == "model refusal"
    assert event.body == "claude-opus-4-8 refused the request (category: cyber)."
    assert event.status == "error"
    assert event.severity == "error"
    assert event.data == {
        "model": "claude-opus-4-8",
        "category": "cyber",
        "request_id": "req_refused",
        "attempt": "1",
        "has_explanation": "True",
    }
    assert event.raw_ref.signal == "log" and event.raw_ref.raw_seq == 3


def test_normalize_logs_prefers_exported_refusal_explanation():
    rec = FlatLogRecord(
        event_name="claude_code.api_refusal",
        time_ns=0,
        attrs={
            "model": "claude-opus",
            "category": "policy",
            "explanation": "I cannot assist with that request.",
        },
        body="claude_code.api_refusal",
        raw_seq=0,
    )

    (event,) = ClaudeCodeAdapter().normalize_logs([rec], _CTX)

    assert event.body == "I cannot assist with that request."


def test_normalize_logs_skips_redacted_and_non_response_events():
    from xorcise.core.otel.flatten import FlatLogRecord

    redacted = FlatLogRecord("claude_code.assistant_response", 0, {"response": "<REDACTED>"}, "", 0)
    other = FlatLogRecord("claude_code.api_request", 0, {"cost_usd": "0.01"}, "", 0)
    assert ClaudeCodeAdapter().normalize_logs([redacted, other], _CTX) == []


def test_normalize_run_merges_log_assistant_message_chronologically():
    import json as _json

    from xorcise.core.otel.adapters import normalize_run

    log_payload = _json.dumps(
        {
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "xorcise.run_id", "value": {"stringValue": _DOC["run_id"]}}
                        ]
                    },
                    "scopeLogs": [
                        {
                            "logRecords": [
                                {
                                    "timeUnixNano": "1783598825000000000",
                                    # REAL shape: full name in the BODY, bare suffix in event.name
                                    # (flatten precedence — body wins).
                                    "body": {"stringValue": "claude_code.assistant_response"},
                                    "attributes": [
                                        {
                                            "key": "event.name",
                                            "value": {"stringValue": "assistant_response"},
                                        },
                                        {
                                            "key": "response",
                                            "value": {"stringValue": "Here is my analysis."},
                                        },
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        }
    )
    view = normalize_run(_DOC["records"], _CTX, log_records=[{"seq": 0, "payload": log_payload}])
    agent_msgs = [e for e in view.events if e.kind == AgentEventKind.message and e.role == "agent"]
    assert any(e.body == "Here is my analysis." for e in agent_msgs)


def test_golden_snapshot_matches():
    events = ClaudeCodeAdapter().normalize(_fixture_spans(), _CTX)
    got = _project(events)
    golden = json.loads(_GOLDEN.read_text())
    assert got == golden, (
        "Claude Code normalization drifted from the golden — regenerate + review the diff."
    )

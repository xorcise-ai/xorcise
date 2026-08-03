# tests/unit/test_codex_adapter.py
"""CodexAdapter (Phase 2) — maps OpenAI Codex CLI OTLP to AgentEvents.

Codex's agent narrative rides the LOGS signal (codex.user_prompt / codex.tool_result / …), while
its spans are pure codex_core runtime internals (append_items, auth, handle_responses, …) — so
normalize(spans) suppresses them and normalize_logs builds the timeline. Built from a REAL captured
run (fixtures/otlp/codex_real_run.json)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from xorcise.core.contracts.agent_event import AgentEventKind
from xorcise.core.harness_adapters.codex import otel as codex  # noqa: F401 — self-register
from xorcise.core.harness_adapters.codex.otel import CodexAdapter
from xorcise.core.otel.adapters.base import AdapterContext
from xorcise.core.otel.adapters.registry import select
from xorcise.core.otel.flatten import FlatLogRecord, FlatSpan, flatten, flatten_logs

_FIX = Path(__file__).resolve().parents[1] / "fixtures/otlp/codex_real_run.json"
_DOC = json.loads(_FIX.read_text())
_CTX = AdapterContext(
    run_id=_DOC["run_id"],
    source_agent="codex",
    mission_id="hello",
    created_at=datetime(2026, 7, 22, tzinfo=UTC),
)


def _fixture_logs() -> list[FlatLogRecord]:
    logs: list[FlatLogRecord] = []
    for rec in _DOC["log_records"]:
        logs.extend(flatten_logs(rec["payload"], raw_seq=rec.get("seq", 0)))
    return logs


def _fixture_spans() -> list[FlatSpan]:
    spans: list[FlatSpan] = []
    for rec in _DOC["records"]:
        spans.extend(flatten(rec["payload"], raw_seq=rec.get("seq", 0)))
    return spans


def _log(event_name: str, attrs: dict[str, str], *, ts: int = 1_700_000_000_000_000_000):
    return FlatLogRecord(event_name=event_name, time_ns=ts, attrs=attrs, body="", raw_seq=0)


def test_codex_selected_by_source_agent():
    adapter, fallback = select("codex", _fixture_spans())
    assert adapter.name == "codex"
    assert fallback is False


def test_spans_are_suppressed_as_runtime_noise():
    # codex spans are all codex_core internals (append_items, auth, handle_responses…) — none are
    # agent narrative, so normalize(spans) yields nothing (the whole point vs the generic flood).
    assert CodexAdapter().normalize(_fixture_spans(), _CTX) == []


def test_user_prompt_log_maps_to_user_message():
    (ev,) = CodexAdapter().normalize_logs([_log("codex.user_prompt", {"prompt": "solve it"})], _CTX)
    assert ev.kind == AgentEventKind.message and ev.role == "user"
    assert ev.body == "solve it"


def test_exec_command_tool_result_maps_to_terminal_command_and_output():
    rec = _log(
        "codex.tool_result",
        {
            "tool_name": "exec_command",
            "call_id": "call_ABC",
            "arguments": '{"cmd":"echo hi","yield_time_ms":1000}',
            "output": "Output:\nhi\n",
            "success": "true",
            "duration_ms": "42",
        },
    )
    evs = CodexAdapter().normalize_logs([rec], _CTX)
    cmd = next(e for e in evs if e.kind == AgentEventKind.terminal_command)
    out = next(e for e in evs if e.kind == AgentEventKind.terminal_output)
    assert cmd.body == "echo hi" and cmd.status == "ok" and cmd.data.get("duration_ms") == "42"
    assert out.role == "tool" and "hi" in out.body
    # command + output share a group (the call id) so the UI renders them as one action
    assert cmd.group_id == out.group_id == "call_ABC"


def test_terminal_session_tools_are_not_misclassified_as_generic_calls():
    write = _log(
        "codex.tool_result",
        {
            "tool_name": "write_stdin",
            "call_id": "session-1",
            "arguments": '{"session_id":42,"chars":"y\\n"}',
            "output": "continued",
        },
    )
    wait = _log(
        "codex.tool_result",
        {
            "tool_name": "wait",
            "call_id": "session-1",
            "arguments": '{"cell_id":"cell-1","yield_time_ms":10000}',
            "output": "complete",
        },
    )

    events = CodexAdapter().normalize_logs([write, wait], _CTX)

    assert sum(e.kind == AgentEventKind.terminal_command for e in events) == 2
    assert sum(e.kind == AgentEventKind.terminal_output for e in events) == 2
    assert not any(e.kind in (AgentEventKind.tool_call, AgentEventKind.tool_result) for e in events)
    assert all(e.raw_ref.signal == "log" for e in events)
    commands = [e.body for e in events if e.kind == AgentEventKind.terminal_command]
    assert commands == ["y\n", "wait for cell-1"]


def test_approved_tool_decision_dropped_rejected_kept():
    approved = _log("codex.tool_decision", {"tool_name": "exec_command", "decision": "approved"})
    assert CodexAdapter().normalize_logs([approved], _CTX) == []
    rej = _log("codex.tool_decision", {"tool_name": "exec_command", "decision": "denied"})
    (ev,) = CodexAdapter().normalize_logs([rej], _CTX)
    assert ev.kind == AgentEventKind.status and "denied" in ev.body


def test_unknown_tool_maps_to_tool_call_and_result_totality():
    rec = _log(
        "codex.tool_result",
        {"tool_name": "web_search", "call_id": "c1", "arguments": "{}", "output": "results…"},
    )
    evs = CodexAdapter().normalize_logs([rec], _CTX)
    kinds = [e.kind for e in evs]
    assert AgentEventKind.tool_call in kinds and AgentEventKind.tool_result in kinds
    call = next(e for e in evs if e.kind == AgentEventKind.tool_call)
    assert call.title == "web_search"


def test_transport_and_startup_noise_suppressed():
    # startup_phase, a SUCCESSFUL api/websocket call, and a non-terminal sse frame are pure
    # transport/runtime noise — dropped.
    noise = [
        _log("codex.api_request", {"endpoint": "/models", "success": "True"}),
        _log("codex.websocket_connect", {"success": "True"}),
        _log("codex.startup_phase", {"startup.phase": "thread_start_total"}),
        _log("codex.sse_event", {"event.kind": "response.output_item.added"}),
    ]
    assert CodexAdapter().normalize_logs(noise, _CTX) == []


def test_sse_response_completed_maps_to_token_metric():
    rec = _log(
        "codex.sse_event",
        {
            "event.kind": "response.completed",
            "input_token_count": "14537",
            "output_token_count": "38",
            "reasoning_token_count": "0",
        },
    )
    (ev,) = CodexAdapter().normalize_logs([rec], _CTX)
    assert ev.kind == AgentEventKind.metric and "out=38" in ev.body
    assert ev.data.get("input_token_count") == "14537"


def test_turn_ttft_maps_to_metric():
    (ev,) = CodexAdapter().normalize_logs([_log("codex.turn_ttft", {"duration_ms": "4198"})], _CTX)
    assert ev.kind == AgentEventKind.metric and "4198" in ev.body


def test_failed_api_request_surfaces_error_status():
    rec = _log(
        "codex.api_request",
        {"endpoint": "/responses", "success": "false", "http.response.status_code": "500"},
    )
    (ev,) = CodexAdapter().normalize_logs([rec], _CTX)
    assert ev.kind == AgentEventKind.status and ev.status == "error" and ev.severity == "error"
    assert "/responses" in ev.body and "500" in ev.body


def test_redacted_prompt_skipped_square_brackets():
    # codex writes the SQUARE-bracket "[REDACTED]" when otel.log_user_prompt is false — skip it.
    rec = _log("codex.user_prompt", {"prompt": "[REDACTED]"})
    assert CodexAdapter().normalize_logs([rec], _CTX) == []


def test_event_timestamps_are_real_not_collapsed():
    # Regression: codex emits log records with timeUnixNano="0" (real time in observedTimeUnixNano).
    # A truthy-"0" fallback bug collapsed every event to ctx.created_at; flatten now falls back so
    # events carry their true wall-clock time. Assert the fixture yields multiple distinct ts (and
    # none equal to the ctx.created_at sentinel).
    evs = CodexAdapter().normalize_logs(_fixture_logs(), _CTX)
    stamps = {e.ts for e in evs}
    assert len(stamps) > 1, "all codex events collapsed to one timestamp — flatten ts fallback bug"
    assert _CTX.created_at not in stamps  # not the run-creation sentinel


def test_no_assistant_message_from_otel():
    # LOCKED NON-BUG: codex's OTel carries no assistant response/reasoning TEXT (only token counts),
    # so the adapter never fabricates an agent `message`. Don't "fix" this — the data isn't there.
    evs = CodexAdapter().normalize_logs(_fixture_logs(), _CTX)
    assert not any(e.kind == AgentEventKind.message and e.role == "agent" for e in evs)


def test_totality_on_malformed_tool_result_no_crash():
    # missing arguments / bad JSON must not raise
    rec = _log("codex.tool_result", {"tool_name": "exec_command", "arguments": "not-json{"})
    evs = CodexAdapter().normalize_logs([rec], _CTX)  # must not raise
    assert isinstance(evs, list)


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


def test_golden_snapshot_matches():
    events = CodexAdapter().normalize_logs(_fixture_logs(), _CTX)
    got = _project(events)
    golden = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures/otlp/codex_events_golden.json").read_text()
    )
    assert got == golden, (
        "Codex normalization drifted from the golden — regenerate + review the diff."
    )


def test_real_fixture_produces_clean_narrative():
    evs = CodexAdapter().normalize_logs(_fixture_logs(), _CTX)
    kinds = {e.kind for e in evs}
    assert AgentEventKind.message in kinds  # the user prompt
    assert AgentEventKind.terminal_command in kinds  # echo hello-codex-fixture
    assert AgentEventKind.terminal_output in kinds  # its output
    users = [e for e in evs if e.kind == AgentEventKind.message and e.role == "user"]
    assert users and "hello-codex-fixture" in users[0].body
    cmds = [e for e in evs if e.kind == AgentEventKind.terminal_command]
    assert cmds and "echo hello-codex-fixture" in cmds[0].body
    outs = [e for e in evs if e.kind == AgentEventKind.terminal_output]
    assert outs and "hello-codex-fixture" in outs[0].body
    # the flood of codex_core internal spans contributes ZERO events
    assert CodexAdapter().normalize(_fixture_spans(), _CTX) == []

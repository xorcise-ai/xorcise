import json

import pytest

from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.otel.distill import distill_transcript

pytestmark = pytest.mark.unit


def _span(
    *, name: str, attrs: dict[str, str], events: list[dict[str, object]] | None = None
) -> dict[str, object]:
    span: dict[str, object] = {
        "traceId": "aaaa",
        "spanId": "bbbb",
        "name": name,
        "startTimeUnixNano": "1",
        "endTimeUnixNano": "2",
        "attributes": [{"key": k, "value": {"stringValue": v}} for k, v in attrs.items()],
    }
    if events:
        span["events"] = events
    return span


def _payload(*spans: dict[str, object]) -> str:
    """A real OTLP/JSON traces payload ({"resourceSpans":[...]}) wrapping the given spans."""
    return json.dumps(
        {
            "resourceSpans": [
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [{"scope": {"name": "s"}, "spans": list(spans)}],
                }
            ]
        }
    )


def _rec(seq: int, payload: str) -> TraceRecord:
    return TraceRecord(run_id="r", seq=seq, payload=payload)


# The identity + telemetry noise that rides EVERY real span (from a live judge prompt).
_NOISE = {
    "xorcise.run_id": "bbd798c8",
    "user.id": "f90dc17a",
    "user.email": "operator@example.com",
    "session.id": "9158f9d6",
    "organization.id": "79330101",
    "terminal.type": "vscode",
    "span.type": "tool",
    "duration_ms": "3009",
    "input_tokens": "2",
    "output_tokens": "453",
    "cache_read_tokens": "15506",
    "request_id": "req_011",
    "client_request_id": "64790e44",
    "ttft_ms": "1853",
    "stop_reason": "tool_use",
    "tool_use_id": "toolu_01",
}


# The provenance block that rides EVERY span emitted by a Rust `tracing`/OTel harness (codex).
# Taken verbatim from a live codex `build_tool_call` span — note it describes the HARNESS's own
# source code, and carries nothing whatsoever about what the agent did.
_PROVENANCE = {
    "code.file.path": "core/src/tools/router.rs",
    "code.module.name": "codex_core::tools::router",
    "code.line.number": "111",
    "thread.id": "10",
    "thread.name": "tokio-rt-worker",
    "target": "codex_core::tools::router",
    "busy_ns": "1427",
    "idle_ns": "1915",
}


def test_drops_harness_source_provenance_attributes():
    """The leak that made the judge unusable on codex: `_is_content` matches the LAST dot-segment,
    so `code.module.name`/`thread.name` passed on the allowlist's generic "name" (meant for
    tool_name) and `code.file.path` passed on "path" (meant for api.path). A live 16,832-span run
    therefore fed the judge the same four Rust source paths over and over — 1,570,424 of 1,573,499
    kept characters (99.8%), a 991,150-token prompt against a 272,000-token model ceiling.

    A span carrying ONLY provenance describes the harness, not the agent, so it must distil to
    nothing at all."""
    payload = _payload(_span(name="build_tool_call", attrs=_PROVENANCE))
    assert distill_transcript([_rec(0, payload)]) == ()


def test_keeps_the_agent_action_while_dropping_provenance_on_the_same_span():
    payload = _payload(
        _span(name="tool", attrs={**_PROVENANCE, "tool_name": "Bash", "full_command": "id"})
    )
    line = distill_transcript([_rec(0, payload)])[0]
    assert "tool_name=Bash" in line and "full_command=id" in line
    for leaked in ("router.rs", "codex_core", "tokio-rt-worker"):
        assert leaked not in line


def test_keeps_http_target_but_not_the_bare_tracing_target():
    """`http.target` is the request path — real agent action. A BARE `target` is Rust `tracing`'s
    module path. Same word, opposite meaning; the namespace is what tells them apart."""
    payload = _payload(
        _span(name="req", attrs={"http.target": "/api/account/2", "target": "codex_core::client"})
    )
    line = distill_transcript([_rec(0, payload)])[0]
    assert "/api/account/2" in line
    assert "codex_core::client" not in line


def _log(*, event_name: str, attrs: dict[str, str], time_ns: str = "1") -> dict[str, object]:
    return {
        "timeUnixNano": time_ns,
        "attributes": [{"key": k, "value": {"stringValue": v}} for k, v in attrs.items()],
        **({"body": {"stringValue": event_name}} if event_name else {}),
    }


def _logs_payload(*records: dict[str, object]) -> str:
    """A real OTLP/JSON logs payload ({"resourceLogs":[...]})."""
    return json.dumps(
        {
            "resourceLogs": [
                {
                    "resource": {"attributes": []},
                    "scopeLogs": [{"scope": {"name": "s"}, "logRecords": list(records)}],
                }
            ]
        }
    )


def test_distils_the_logs_signal_not_only_spans():
    """The judge was blind to what the agent actually did on codex. Codex reports every tool call
    on the OTLP *logs* signal (`codex.tool_result` records carrying `arguments` and `output`), while
    its spans hold only plumbing — so a run that built and ran a proxy was graded "no evidence of an
    HTTP forward proxy implementation". Both signals are RAW and canonical; the grader
    must read both. Every local run carries logs, claude-code's included."""
    logs = _logs_payload(
        _log(
            event_name="codex.tool_result",
            attrs={
                "tool_name": "shell",
                "arguments": "python3 /tmp/emap_proxy.py --bind 0.0.0.0 --proxy-port 18080",
                "output": "Exit code: 0\nproxy listening",
                "user.email": "someone@example.com",  # identity must NOT ride along
                "duration_ms": "1",
            },
        )
    )
    lines = distill_transcript([], [_rec(0, logs)])
    assert len(lines) == 1
    assert "emap_proxy.py --bind 0.0.0.0" in lines[0]
    assert "proxy listening" in lines[0]
    assert "someone@example.com" not in lines[0] and "duration_ms" not in lines[0]


def test_interleaves_spans_and_logs_in_time_order():
    """A judge reasons about a sequence of actions, so a span-recorded action and a log-recorded
    one must appear in the order they happened, not grouped by which signal carried them."""
    spans = _payload(
        _span(name="tool", attrs={"tool_name": "Bash", "full_command": "second"}),
    )
    # span startTimeUnixNano is "1" via _span(); give the log an earlier and a later stamp
    logs = _logs_payload(
        _log(event_name="e", attrs={"output": "first"}, time_ns="0"),
        _log(event_name="e", attrs={"output": "third"}, time_ns="9"),
    )
    joined = "\n".join(distill_transcript([_rec(0, spans)], [_rec(0, logs)]))
    assert joined.index("first") < joined.index("second") < joined.index("third")


def test_log_records_without_content_are_dropped():
    logs = _logs_payload(_log(event_name="codex.telemetry", attrs={"duration_ms": "3", "id": "x"}))
    assert distill_transcript([], [_rec(0, logs)]) == ()


def test_keeps_the_agent_action_and_strips_identity_and_telemetry_noise():
    payload = _payload(
        _span(name="claude_code.tool", attrs={**_NOISE, "tool_name": "Bash", "full_command": "id"})
    )
    line = distill_transcript([_rec(0, payload)])[0]
    # the action survives
    assert "tool_name=Bash" in line and "full_command=id" in line
    # none of the identity / telemetry noise does
    for noisy in (
        "user.email",
        "operator@example.com",
        "session.id",
        "organization.id",
        "terminal.type",
        "duration_ms",
        "input_tokens",
        "cache_read_tokens",
        "ttft_ms",
        "request_id",
        "stop_reason",
        "tool_use_id",
        "xorcise.run_id",
    ):
        assert noisy not in line


def test_drops_content_less_lifecycle_and_llm_request_spans():
    # An llm_request span (pure telemetry, no content) and a blocked_on_user span contribute
    # nothing; only the tool span with a real command/output survives.
    llm = _span(name="claude_code.llm_request", attrs={**_NOISE, "model": "claude-opus-4-8"})
    blocked = _span(
        name="claude_code.tool.blocked_on_user",
        attrs={**_NOISE, "decision": "unknown", "source": "unknown"},
    )
    tool = _span(
        name="claude_code.tool",
        attrs={"tool_name": "Bash", "full_command": "curl http://10.200.1.10/api/account/2"},
        events=[
            {
                "name": "tool.output",
                "attributes": [
                    {"key": "output", "value": {"stringValue": '{"note":"XORCISE{x}"}'}}
                ],
            }
        ],
    )
    lines = distill_transcript([_rec(0, _payload(llm, blocked, tool))])
    assert len(lines) == 1  # only the tool span
    assert "account/2" in lines[0] and "XORCISE{x}" in lines[0]
    assert "llm_request" not in lines[0] and "blocked_on_user" not in lines[0]


def test_surfaces_tool_output_event_content():
    tool = _span(
        name="claude_code.tool",
        attrs={"tool_name": "Bash", "full_command": "curl .../mission"},
        events=[
            {
                "name": "tool.output",
                "attributes": [{"key": "output", "value": {"stringValue": "objective: read acct"}}],
            }
        ],
    )
    line = distill_transcript([_rec(0, _payload(tool))])[0]
    assert "tool.output" in line and "objective: read acct" in line


def test_dedupes_a_command_repeated_between_attr_and_event():
    # The tool span carries full_command AND an event that repeats the same command; it must appear
    # once, while the distinct output is kept.
    cmd = "curl http://10.200.1.10/api/account/2"
    tool = _span(
        name="claude_code.tool",
        attrs={"tool_name": "Bash", "full_command": cmd},
        events=[
            {
                "name": "tool.output",
                "attributes": [
                    {"key": "bash_command", "value": {"stringValue": cmd}},
                    {"key": "output", "value": {"stringValue": "admin note"}},
                ],
            }
        ],
    )
    line = distill_transcript([_rec(0, _payload(tool))])[0]
    assert line.count(cmd) == 1  # command not duplicated
    assert "admin note" in line  # the distinct output stays


def test_preserves_seq_order_across_records():
    p0 = _payload(_span(name="tool", attrs={"tool_name": "Bash", "full_command": "one"}))
    p1 = _payload(_span(name="tool", attrs={"tool_name": "Bash", "full_command": "two"}))
    lines = distill_transcript([_rec(1, p1), _rec(0, p0)])  # out of order
    joined = "\n".join(lines)
    assert joined.index("one") < joined.index("two")


def test_keeps_genai_prompt_and_completion_when_a_span_carries_them():
    # A genai-style harness puts real content on the span (prompt/completion) — that MUST survive
    # even though other gen_ai.* telemetry keys are dropped.
    payload = _payload(
        _span(
            name="chat",
            attrs={
                "gen_ai.system": "anthropic",
                "gen_ai.request.model": "x",
                "gen_ai.prompt": "find the flag",
                "gen_ai.completion": "the flag is XORCISE{x}",
            },
        )
    )
    line = distill_transcript([_rec(0, payload)])[0]
    assert "find the flag" in line and "the flag is XORCISE{x}" in line
    assert "anthropic" not in line and "gen_ai.request.model" not in line


def test_malformed_payload_is_skipped_never_raises():
    good = _payload(_span(name="tool", attrs={"tool_name": "Bash", "full_command": "ok"}))
    lines = distill_transcript([_rec(0, "not json at all"), _rec(1, good)])
    assert any("ok" in ln for ln in lines)


def test_serialized_telemetry_values_are_dropped():
    # lmnr.span.path carries a double-encoded OTLP AnyValue blob; "path" is a content segment so the
    # key slips the allowlist, but the value is telemetry noise and must not reach the judge (L2).
    span = _span(
        name="action",
        attrs={
            "lmnr.span.path": '{"values":[{"stringValue":"conversation"},{"stringValue":"run"}]}',
            "full_command": "whoami",
        },
    )
    lines = distill_transcript([_rec(0, _payload(span))])
    assert any("whoami" in ln for ln in lines)  # real content kept
    assert all("stringValue" not in ln for ln in lines)  # the telemetry blob is gone
    assert all("lmnr.span.path" not in ln for ln in lines)


def test_non_telemetry_json_content_is_kept():
    # A genuine JSON-shaped tool output (no OTLP AnyValue markers) is content, not telemetry — keep.
    span = _span(name="tool", attrs={"output": '{"user":"root","uid":0}'})
    lines = distill_transcript([_rec(0, _payload(span))])
    assert any('"user":"root"' in ln for ln in lines)


def test_distilled_is_far_smaller_than_the_raw_payloads():
    # A realistic noisy tool span: the distilled line must be a fraction of the raw JSON.
    tool = _span(
        name="claude_code.tool",
        attrs={**_NOISE, "tool_name": "Bash", "full_command": "curl .../account/2"},
        events=[
            {
                "name": "tool.output",
                "attributes": [{"key": "output", "value": {"stringValue": "admin note"}}],
            }
        ],
    )
    recs = [_rec(i, _payload(tool)) for i in range(5)]
    raw_bytes = sum(len(r.payload.encode()) for r in recs)
    distilled_bytes = sum(len(ln.encode()) for ln in distill_transcript(recs))
    assert distilled_bytes < raw_bytes // 4  # aggressive reduction

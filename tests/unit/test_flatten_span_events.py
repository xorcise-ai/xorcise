# tests/unit/test_flatten_span_events.py
"""flatten() surfaces OTLP span events into FlatSpan.events."""

from __future__ import annotations

from xorcise.core.otel.flatten import FlatSpanEvent, flatten


def _payload(spans: list[dict[str, object]]) -> dict[str, object]:
    return {
        "resourceSpans": [
            {"resource": {}, "scopeSpans": [{"scope": {"name": "t"}, "spans": spans}]}
        ]
    }


def test_span_events_are_surfaced():
    payload = _payload(
        [
            {
                "spanId": "aa",
                "name": "exec_command",
                "startTimeUnixNano": "1720000000000000000",
                "attributes": [{"key": "tool_name", "value": {"stringValue": "shell"}}],
                "events": [
                    {
                        "name": "session_telemetry",
                        "timeUnixNano": "1720000000500000000",
                        "attributes": [
                            {"key": "command", "value": {"stringValue": "cat > hello.py"}},
                            {"key": "exit_code", "value": {"intValue": "0"}},
                        ],
                    }
                ],
            }
        ]
    )
    (span,) = flatten(payload)
    assert span.attrs["tool_name"] == "shell"  # attrs still work
    assert len(span.events) == 1
    ev = span.events[0]
    assert isinstance(ev, FlatSpanEvent)
    assert ev.name == "session_telemetry"
    assert ev.time_ns == 1_720_000_000_500_000_000
    assert ev.attrs == {"command": "cat > hello.py", "exit_code": "0"}


def test_span_without_events_has_empty_tuple():
    (span,) = flatten(_payload([{"spanId": "bb", "name": "noop", "attributes": []}]))
    assert span.events == ()


def test_malformed_events_are_total_never_raise():
    # events not a list, and a list with a non-dict entry + a dict with no attributes
    p1 = _payload([{"spanId": "c1", "name": "x", "events": "not-a-list"}])
    p2 = _payload(
        [{"spanId": "c2", "name": "y", "events": ["nope", {"name": "e", "attributes": "bad"}]}]
    )
    (s1,) = flatten(p1)
    (s2,) = flatten(p2)
    assert s1.events == ()
    assert len(s2.events) == 1 and s2.events[0].name == "e" and s2.events[0].attrs == {}

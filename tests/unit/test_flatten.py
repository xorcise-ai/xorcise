# tests/unit/test_flatten.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from xorcise.core.otel.flatten import FlatSpan, flatten, flatten_logs

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/otlp/openhands_real_run.json"


def _fixture_spans() -> list[FlatSpan]:
    doc = json.loads(_FIXTURE.read_text())
    spans: list[FlatSpan] = []
    for rec in doc["records"]:
        spans.extend(flatten(rec["payload"]))
    return spans


def test_real_fixture_yields_all_35_spans():
    spans = _fixture_spans()
    assert len(spans) == 35
    names = [s.name for s in spans]
    assert names.count("litellm.completion") == 10
    assert names.count("TerminalAction") == 7


def test_llm_span_carries_gen_ai_and_lmnr_type():
    llm = [s for s in _fixture_spans() if s.name == "litellm.completion"][0]
    assert llm.attrs["lmnr.span.type"] == "LLM"
    assert "gen_ai.request.model" in llm.attrs
    assert llm.scope == "lmnr.tracer"


def test_nested_lmnr_input_blob_preserved_verbatim():
    term = [s for s in _fixture_spans() if s.name == "TerminalAction"][0]
    raw = term.attrs["lmnr.span.input"]
    assert isinstance(raw, str)
    assert json.loads(raw)["action"]["command"]  # the blob is intact, parseable JSON


def test_structural_fields_present():
    s = _fixture_spans()[0]
    assert s.span_id and s.trace_id  # base64 strings, kept verbatim
    assert s.start_ns > 0
    assert s.resource.get("service.name")


def test_totality_on_bad_input():
    cases: tuple[object, ...] = (
        None,
        "x",
        3,
        [],
        {},
        {"resourceSpans": None},
        {"resourceSpans": [42]},
    )
    for bad in cases:
        assert flatten(bad) == []


def test_missing_span_fields_do_not_raise():
    payload: dict[str, Any] = {"resourceSpans": [{"scopeSpans": [{"spans": [{}]}]}]}
    out = flatten(payload)
    assert len(out) == 1
    assert out[0].name == "" and dict(out[0].attrs) == {} and out[0].start_ns == 0


def test_attr_scalar_types_and_lossless_complex():
    payload = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "name": "s",
                                "attributes": [
                                    {"key": "i", "value": {"intValue": 7}},
                                    {"key": "d", "value": {"doubleValue": 1.5}},
                                    {"key": "b", "value": {"boolValue": True}},
                                    {
                                        "key": "arr",
                                        "value": {"arrayValue": {"values": [{"stringValue": "a"}]}},
                                    },
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    attrs = flatten(payload)[0].attrs
    assert attrs["i"] == "7" and attrs["d"] == "1.5" and attrs["b"] == "True"
    assert "arr" in attrs and json.loads(attrs["arr"])  # preserved as JSON, not dropped


def test_flatten_logs_uses_observed_time_when_time_is_zero():
    # Codex's OTLP exporter emits log records with timeUnixNano="0" and the real time only in
    # observedTimeUnixNano. The string "0" is truthy, so a `a or b` fallback would keep 0 and every
    # event would collapse to one timestamp — fall back to observedTimeUnixNano when time is 0.
    payload = {
        "resourceLogs": [
            {
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "timeUnixNano": "0",
                                "observedTimeUnixNano": "1784702886780792281",
                                "body": {"stringValue": "codex.user_prompt"},
                                "attributes": [
                                    {
                                        "key": "event.name",
                                        "value": {"stringValue": "codex.user_prompt"},
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    (rec,) = flatten_logs(payload)
    assert rec.time_ns == 1784702886780792281


def test_flatten_logs_prefers_time_over_observed_when_present():
    payload = {
        "resourceLogs": [
            {
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "timeUnixNano": "1000",
                                "observedTimeUnixNano": "2000",
                                "body": {"stringValue": "e"},
                                "attributes": [],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    (rec,) = flatten_logs(payload)
    assert rec.time_ns == 1000  # real send time wins when non-zero


def test_totality_on_infinity_timestamp():
    # json.loads decodes the Infinity token to float('inf'); int(inf) raises OverflowError.
    payload = {
        "resourceSpans": [
            {"scopeSpans": [{"spans": [{"name": "s", "startTimeUnixNano": float("inf")}]}]}
        ]
    }
    out = flatten(payload)
    assert len(out) == 1 and out[0].start_ns == 0  # defaulted, not raised


def test_flatten_stamps_raw_seq():
    payload = {"resourceSpans": [{"scopeSpans": [{"spans": [{"name": "s"}]}]}]}
    assert flatten(payload, raw_seq=7)[0].raw_seq == 7
    assert flatten(payload)[0].raw_seq == 0  # default


def test_totality_on_non_serializable_complex_attr():
    payload = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "name": "s",
                                "attributes": [{"key": "a", "value": {"arrayValue": {1, 2, 3}}}],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    out = flatten(payload)
    assert len(out) == 1 and "a" not in out[0].attrs  # dropped, not raised

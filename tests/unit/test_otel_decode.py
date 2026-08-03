# tests/unit/test_otel_decode.py
from __future__ import annotations

import importlib
import json
from typing import Any

import pytest

from xorcise.core.otel.decode import RUN_ID_ATTR, route_otlp_json

# ---------------------------------------------------------------------------
# Protobuf tests — guarded: skip if opentelemetry-proto is not installed
# ---------------------------------------------------------------------------
try:
    _PROTO_AVAILABLE = (
        importlib.util.find_spec("opentelemetry.proto.collector.trace.v1.trace_service_pb2")
        is not None
    )
except ModuleNotFoundError:
    # opentelemetry-proto (the `collector` extra) is not installed: find_spec RAISES rather than
    # returning None when the parent package `opentelemetry.proto` is absent (e.g. CI on
    # xorcise[dev]). Treat as "proto unavailable" so the protobuf tests skip, not error collection.
    _PROTO_AVAILABLE = False
_skip_proto = pytest.mark.skipif(not _PROTO_AVAILABLE, reason="opentelemetry-proto not installed")


def _rs(
    run_id: str | None,
    span_names: list[str],
    *,
    span_attrs: dict[str, str] | None = None,
) -> dict[str, Any]:
    resource: dict[str, Any] = {"attributes": []}
    if run_id is not None:
        resource["attributes"].append({"key": RUN_ID_ATTR, "value": {"stringValue": run_id}})
    span: dict[str, Any] = {"name": span_names[0] if span_names else "op", "spanId": "s"}
    if span_attrs:
        span["attributes"] = [
            {"key": k, "value": {"stringValue": v}} for k, v in span_attrs.items()
        ]
    spans: list[dict[str, Any]] = [{"name": n, "spanId": n} for n in span_names] or [span]
    if span_attrs:  # attach the prompt-bearing attrs to the first span
        attrs: list[dict[str, Any]] = spans[0].setdefault("attributes", [])
        attrs.extend({"key": k, "value": {"stringValue": v}} for k, v in span_attrs.items())
    return {"resource": resource, "scopeSpans": [{"spans": spans}]}


@pytest.mark.unit
def test_single_run_is_routed_with_span_count() -> None:
    body = json.dumps({"resourceSpans": [_rs("run-1", ["a", "b"])]})
    result = route_otlp_json(body)
    assert result.accepted_spans == 2
    assert result.dropped_spans == 0
    assert [run_id for run_id, _ in result.routed] == ["run-1"]
    # RAW payload is the resourceSpans subset, semantically equal to the input
    assert json.loads(result.routed[0][1]) == {"resourceSpans": [_rs("run-1", ["a", "b"])]}


@pytest.mark.unit
def test_multiple_runs_split_into_separate_payloads() -> None:
    body = json.dumps({"resourceSpans": [_rs("run-1", ["a"]), _rs("run-2", ["b", "c"])]})
    result = route_otlp_json(body)
    assert sorted(run_id for run_id, _ in result.routed) == ["run-1", "run-2"]
    assert result.accepted_spans == 3
    assert result.dropped_spans == 0


@pytest.mark.unit
def test_prompt_sentinel_is_used_when_attribute_absent() -> None:
    # no resource attr, but the agent echoed the mission prompt into a span attribute
    prompt = "Run abc — solve it. Marker: xorcise.run_id=run-xyz do not remove."
    body = json.dumps({"resourceSpans": [_rs(None, ["a", "b"], span_attrs={"input": prompt})]})
    result = route_otlp_json(body)
    assert [run_id for run_id, _ in result.routed] == ["run-xyz"]
    assert result.accepted_spans == 2
    assert result.dropped_spans == 0


@pytest.mark.unit
def test_resource_attribute_wins_over_sentinel() -> None:
    prompt = "xorcise.run_id=run-FROM-PROMPT"
    body = json.dumps(
        {"resourceSpans": [_rs("run-FROM-ATTR", ["a"], span_attrs={"input": prompt})]}
    )
    result = route_otlp_json(body)
    assert [run_id for run_id, _ in result.routed] == ["run-FROM-ATTR"]


@pytest.mark.unit
def test_no_attr_and_no_sentinel_is_counted_dropped() -> None:
    body = json.dumps({"resourceSpans": [_rs(None, ["a", "b"]), _rs("", ["c"])]})
    result = route_otlp_json(body)
    assert result.routed == ()
    assert result.accepted_spans == 0
    assert result.dropped_spans == 3


@pytest.mark.unit
def test_empty_request_is_zero_zero() -> None:
    result = route_otlp_json(json.dumps({"resourceSpans": []}))
    assert result == route_otlp_json(json.dumps({}))
    assert result.accepted_spans == 0 and result.dropped_spans == 0 and result.routed == ()


@pytest.mark.unit
def test_malformed_json_raises_value_error() -> None:
    with pytest.raises(ValueError):
        route_otlp_json("{not json")


# ---------------------------------------------------------------------------
# Protobuf path tests (require opentelemetry-proto extra)
# ---------------------------------------------------------------------------


def _build_proto_bytes(run_id: str, span_names: list[str]) -> bytes:
    """Construct a serialised ExportTraceServiceRequest for testing."""
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )
    from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
    from opentelemetry.proto.resource.v1.resource_pb2 import Resource
    from opentelemetry.proto.trace.v1.trace_pb2 import (
        ResourceSpans,
        ScopeSpans,
        Span,
    )

    attr = KeyValue(key=RUN_ID_ATTR, value=AnyValue(string_value=run_id))
    resource = Resource(attributes=[attr])
    spans = [Span(name=n, span_id=n.encode().ljust(8, b"\x00")[:8]) for n in span_names]
    scope_spans = ScopeSpans(spans=spans)
    resource_spans = ResourceSpans(resource=resource, scope_spans=[scope_spans])
    req = ExportTraceServiceRequest(resource_spans=[resource_spans])
    return bytes(req.SerializeToString())


@pytest.mark.unit
@_skip_proto
def test_protobuf_routes_to_run_with_span_count() -> None:
    from xorcise.core.otel.decode import route_otlp_protobuf

    body = _build_proto_bytes("run-proto-1", ["span-a", "span-b"])
    result = route_otlp_protobuf(body)
    assert result.accepted_spans == 2
    assert result.dropped_spans == 0
    run_ids = [rid for rid, _ in result.routed]
    assert run_ids == ["run-proto-1"]


@pytest.mark.unit
def test_protobuf_json_regression() -> None:
    """Existing JSON path must still route correctly after the refactor."""
    body = json.dumps({"resourceSpans": [_rs("run-json-1", ["x", "y"])]})
    result = route_otlp_json(body)
    assert result.accepted_spans == 2
    assert [rid for rid, _ in result.routed] == ["run-json-1"]


@pytest.mark.unit
@_skip_proto
def test_malformed_protobuf_raises_value_error() -> None:
    from xorcise.core.otel.decode import route_otlp_protobuf

    with pytest.raises(ValueError):
        route_otlp_protobuf(b"\xff\xff\xff")


def _logs_payload(attrs: list[dict[str, Any]], event_bodies: list[str]) -> str:
    records = [{"body": {"stringValue": b}} for b in event_bodies]
    scope_logs = [{"logRecords": records}]
    rl = {"resource": {"attributes": attrs}, "scopeLogs": scope_logs}
    return json.dumps({"resourceLogs": [rl]})


def test_route_otlp_logs_by_resource_attr() -> None:
    # logs route by the same xorcise.run_id resource attribute as spans.
    from xorcise.core.otel.decode import route_otlp_logs_json

    attrs = [{"key": RUN_ID_ATTR, "value": {"stringValue": "run-a"}}]
    res = route_otlp_logs_json(_logs_payload(attrs, ["e1", "e2"]))
    assert [r[0] for r in res.routed] == ["run-a"]
    assert res.accepted_spans == 2  # counts log records


def test_route_otlp_logs_by_content_sentinel_fallback() -> None:
    from xorcise.core.otel.decode import route_otlp_logs_json

    res = route_otlp_logs_json(_logs_payload([], ["a prompt carrying xorcise.run_id=run-b inline"]))
    assert [r[0] for r in res.routed] == ["run-b"]


def test_route_otlp_logs_unroutable_is_dropped() -> None:
    from xorcise.core.otel.decode import route_otlp_logs_json

    res = route_otlp_logs_json(_logs_payload([], ["no id here"]))
    assert res.routed == ()
    assert res.dropped_spans == 1

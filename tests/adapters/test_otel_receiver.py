# tests/adapters/test_otel_receiver.py
from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from xorcise.core.otel.decode import RUN_ID_ATTR
from xorcise.core.otel.ingest.embedded import create_otel_app
from xorcise.core.otel.store import InMemoryTraceStore

pytestmark = pytest.mark.adapters

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


def _trace(run_id: str | None, names: list[str]) -> dict[str, object]:
    attrs: list[dict[str, object]] = []
    if run_id is not None:
        attrs.append({"key": RUN_ID_ATTR, "value": {"stringValue": run_id}})
    resource: dict[str, object] = {"attributes": attrs}
    scope_spans = [{"spans": [{"name": n} for n in names]}]
    return {"resourceSpans": [{"resource": resource, "scopeSpans": scope_spans}]}


def test_healthz_ok() -> None:
    client = TestClient(create_otel_app(InMemoryTraceStore()))
    assert client.get("/healthz").json()["status"] == "ok"


def test_routed_trace_is_persisted_unmodified() -> None:
    store = InMemoryTraceStore()
    client = TestClient(create_otel_app(store))
    body = _trace("run-1", ["a", "b"])
    resp = client.post("/v1/traces", content=json.dumps(body))
    assert resp.status_code == 200
    assert resp.json() == {}  # full success
    records = store.read("run-1")
    assert len(records) == 1
    assert json.loads(records[0].payload) == body  # RAW, semantically equal


def test_unknown_run_id_is_rejected_not_blended() -> None:
    store = InMemoryTraceStore()
    client = TestClient(create_otel_app(store))
    resp = client.post("/v1/traces", content=json.dumps(_trace(None, ["x", "y"])))
    assert resp.status_code == 200
    assert resp.json()["partialSuccess"]["rejectedSpans"] == 2
    assert store.read("") == []


def test_malformed_body_is_400() -> None:
    client = TestClient(create_otel_app(InMemoryTraceStore()))
    assert client.post("/v1/traces", content="{nope").status_code == 400


def test_gzip_encoded_json_body_is_decompressed_and_persisted() -> None:
    # OTLP/HTTP clients MAY gzip the payload and set Content-Encoding: gzip (the OpenHands/
    # Laminar exporter does, unconditionally). A spec-compliant receiver must decompress it
    # before decode; otherwise the raw gzip bytes fail to parse and the export 400s.
    import gzip

    store = InMemoryTraceStore()
    client = TestClient(create_otel_app(store))
    body = _trace("run-gz", ["a", "b"])
    resp = client.post(
        "/v1/traces",
        content=gzip.compress(json.dumps(body).encode("utf-8")),
        headers={"content-encoding": "gzip"},
    )
    assert resp.status_code == 200
    assert resp.json() == {}
    assert len(store.read("run-gz")) == 1


def test_gzip_with_corrupt_body_is_still_400() -> None:
    # A gzip Content-Encoding with a body that isn't valid gzip must fail closed as malformed,
    # not raise out of the handler.
    client = TestClient(create_otel_app(InMemoryTraceStore()))
    resp = client.post("/v1/traces", content=b"not-gzip", headers={"content-encoding": "gzip"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Protobuf receiver tests (require opentelemetry-proto extra)
# ---------------------------------------------------------------------------


def _build_proto_bytes(run_id: str, span_names: list[str]) -> bytes:
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


@pytest.mark.adapters
@_skip_proto
def test_protobuf_body_is_persisted_under_run() -> None:
    store = InMemoryTraceStore()
    client = TestClient(create_otel_app(store))
    body = _build_proto_bytes("run-pb-1", ["alpha", "beta"])
    resp = client.post(
        "/v1/traces",
        content=body,
        headers={"content-type": "application/x-protobuf"},
    )
    assert resp.status_code == 200

    # Record is stored under the correct run_id key.
    records = store.read("run-pb-1")
    assert len(records) == 1

    # RAW is normalised to canonical OTLP/JSON.
    parsed = json.loads(records[0].payload)

    # run_id attribute is present with the exact value used in the request.
    rs = parsed["resourceSpans"][0]
    attrs = rs["resource"]["attributes"]
    run_id_attr = next((a for a in attrs if a["key"] == RUN_ID_ATTR), None)
    assert run_id_attr is not None, f"{RUN_ID_ATTR!r} attribute missing from stored payload"
    assert run_id_attr["value"]["stringValue"] == "run-pb-1"

    # All spans were actually persisted (not silently zero-routed).
    total_spans = sum(
        len(ss["spans"]) for rs_item in parsed["resourceSpans"] for ss in rs_item["scopeSpans"]
    )
    assert total_spans == 2  # one scope-span group with ["alpha", "beta"]


@_skip_proto
def test_gzip_encoded_protobuf_body_is_decompressed_and_persisted() -> None:
    # The real OpenHands/Laminar export: protobuf body + Content-Encoding: gzip. This is the
    # exact shape that produced the 400 in the field.
    import gzip

    store = InMemoryTraceStore()
    client = TestClient(create_otel_app(store))
    body = _build_proto_bytes("run-pbgz", ["alpha", "beta"])
    resp = client.post(
        "/v1/traces",
        content=gzip.compress(body),
        headers={"content-type": "application/x-protobuf", "content-encoding": "gzip"},
    )
    assert resp.status_code == 200
    assert len(store.read("run-pbgz")) == 1


@_skip_proto
def test_malformed_protobuf_body_is_400() -> None:
    client = TestClient(create_otel_app(InMemoryTraceStore()))
    resp = client.post(
        "/v1/traces",
        content=b"\xff\xff",
        headers={"content-type": "application/x-protobuf"},
    )
    assert resp.status_code == 400


def test_spans_for_a_sealed_run_are_dropped_not_persisted() -> None:
    from xorcise.core.otel.store import InMemorySealStore

    store = InMemoryTraceStore()
    seal_store = InMemorySealStore()
    seal_store.seal("run-sealed")
    client = TestClient(create_otel_app(store, seal_store))

    resp = client.post("/v1/traces", content=json.dumps(_trace("run-sealed", ["x", "y"])))
    assert resp.status_code == 200
    # nothing persisted under the sealed run
    assert store.read("run-sealed") == []
    # the two late spans are reported as rejected
    assert resp.json()["partialSuccess"]["rejectedSpans"] == 2


def test_spans_for_an_unsealed_run_persist_with_seal_store_present() -> None:
    from xorcise.core.otel.store import InMemorySealStore

    store = InMemoryTraceStore()
    client = TestClient(create_otel_app(store, InMemorySealStore()))
    resp = client.post("/v1/traces", content=json.dumps(_trace("run-open", ["a"])))
    assert resp.status_code == 200
    assert len(store.read("run-open")) == 1


# ---------------------------------------------------------------------------
# OTLP LOGS signal — /v1/logs, the peer of /v1/traces
# ---------------------------------------------------------------------------


def _logs(run_id: str | None, event_names: list[str]) -> dict[str, object]:
    attrs: list[dict[str, object]] = []
    if run_id is not None:
        attrs.append({"key": RUN_ID_ATTR, "value": {"stringValue": run_id}})
    resource: dict[str, object] = {"attributes": attrs}
    log_records = [{"body": {"stringValue": n}} for n in event_names]
    scope = {"name": "com.anthropic.claude_code.events"}
    scope_logs = [{"scope": scope, "logRecords": log_records}]
    return {"resourceLogs": [{"resource": resource, "scopeLogs": scope_logs}]}


def test_routed_logs_are_persisted_to_the_log_store() -> None:
    logs = InMemoryTraceStore()
    client = TestClient(create_otel_app(InMemoryTraceStore(), log_store=logs))
    body = _logs("run-L", ["claude_code.assistant_response", "claude_code.user_prompt"])
    resp = client.post("/v1/logs", content=json.dumps(body))
    assert resp.status_code == 200
    assert resp.json() == {}  # full success
    records = logs.read("run-L")
    assert len(records) == 1
    assert json.loads(records[0].payload) == body  # RAW, semantically equal


def test_unknown_run_id_logs_rejected_not_blended() -> None:
    logs = InMemoryTraceStore()
    client = TestClient(create_otel_app(InMemoryTraceStore(), log_store=logs))
    resp = client.post("/v1/logs", content=json.dumps(_logs(None, ["a", "b"])))
    assert resp.status_code == 200
    assert resp.json()["partialSuccess"]["rejectedLogRecords"] == 2
    assert logs.read("") == []


def test_logs_and_traces_persist_to_separate_stores() -> None:
    traces = InMemoryTraceStore()
    logs = InMemoryTraceStore()
    client = TestClient(create_otel_app(traces, log_store=logs))
    client.post("/v1/logs", content=json.dumps(_logs("run-x", ["e1", "e2"])))
    assert traces.read("run-x") == []  # logs never leak into the trace store
    assert len(logs.read("run-x")) == 1


def test_malformed_logs_body_is_400() -> None:
    client = TestClient(create_otel_app(InMemoryTraceStore(), log_store=InMemoryTraceStore()))
    assert client.post("/v1/logs", content="{nope").status_code == 400


def test_logs_for_a_sealed_run_are_dropped_not_persisted() -> None:
    from xorcise.core.otel.store import InMemorySealStore

    logs = InMemoryTraceStore()
    seal_store = InMemorySealStore()
    seal_store.seal("run-sealed")
    client = TestClient(create_otel_app(InMemoryTraceStore(), seal_store, log_store=logs))
    resp = client.post("/v1/logs", content=json.dumps(_logs("run-sealed", ["x", "y"])))
    assert resp.status_code == 200
    assert logs.read("run-sealed") == []
    assert resp.json()["partialSuccess"]["rejectedLogRecords"] == 2

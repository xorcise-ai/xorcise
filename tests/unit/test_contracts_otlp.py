from __future__ import annotations

from xorcise.core.contracts.otlp import IngestAck, SpanEnvelope, TraceRef
from xorcise.core.contracts.telemetry import TraceRecord


def test_span_envelope_and_ack() -> None:
    span = SpanEnvelope(run_id="r1", span_id="s1", name="op")
    ack = IngestAck(run_id="r1", accepted=1)
    assert span.span_id == "s1"
    assert ack.accepted == 1


def test_trace_ref_keyed_by_run() -> None:
    assert TraceRef(run_id="r1").run_id == "r1"


def test_trace_record_shape() -> None:
    rec = TraceRecord(run_id="r1", seq=0, payload="line")
    assert rec.seq == 0
    assert rec.payload == "line"

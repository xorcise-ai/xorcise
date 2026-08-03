"""Integration: the agent's OTel lands under the correct run_id.

In-process (no Docker): exercises the seam contracts the agent feeds. The
OTLP-decode bridge that wires the agent's HTTP spans into this store lives in core.otel;
here we assert the store keeps spans run-scoped.

This file also asserted the MCP submission mirror was run-scoped. That mirror was a
canned stub and has been removed — agent submissions go to REST (`POST /artifacts`),
whose run-scoping is covered by the REST run-control tests.
"""

import pytest

from xorcise.core.contracts.otlp import SpanEnvelope
from xorcise.core.otel.ingest import StubOtelIngest


@pytest.mark.integration
def test_otel_lands_under_correct_run_id() -> None:
    ingest = StubOtelIngest()
    ingest.receive(
        [
            SpanEnvelope(run_id="run-A", span_id="s1", name="connect"),
            SpanEnvelope(run_id="run-A", span_id="s2", name="act"),
        ]
    )
    ingest.receive([SpanEnvelope(run_id="run-B", span_id="s3", name="connect")])

    a = list(ingest.stream("run-A"))
    b = list(ingest.stream("run-B"))
    assert [s.span_id for s in a] == ["s1", "s2"]
    assert [s.span_id for s in b] == ["s3"]
    assert all(s.run_id == "run-A" for s in a)
    assert ingest.persist("run-A") == 2

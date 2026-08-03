"""OTLP ingest (embedded default). The real OTLP/HTTP receiver lives in
embedded.create_otel_app: decodes, routes, and persists RAW payloads
by run_id. StubOtelIngest remains the in-process span-stream seam for fan-out — it is
unchanged here.
"""

from __future__ import annotations

from collections.abc import Iterator

from xorcise.core.contracts.otlp import IngestAck, SpanEnvelope
from xorcise.core.otel.ports import OtelIngest


class StubOtelIngest(OtelIngest):
    def __init__(self) -> None:
        self._spans: dict[str, list[SpanEnvelope]] = {}

    def receive(self, spans: list[SpanEnvelope]) -> IngestAck:
        run_id = spans[0].run_id if spans else ""
        for span in spans:
            self._spans.setdefault(span.run_id, []).append(span)
        return IngestAck(run_id=run_id, accepted=len(spans))

    def stream(self, run_id: str) -> Iterator[SpanEnvelope]:
        yield from self._spans.get(run_id, [])

    def persist(self, run_id: str) -> int:
        return len(self._spans.get(run_id, []))

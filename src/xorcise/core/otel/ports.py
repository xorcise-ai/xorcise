"""otel part-island ports (in-process ABCs, beside owner). LAYER: PART-ISLAND.

OtelIngest = receive/stream/persist; TraceStore = append/read/stream by run_id.
SqliteTraceStore + real OTLP decode are the live implementations; the StubOtelIngest
seam stays for future fan-out and a ClickHouse TraceStore is a possible future
behind this ABC.
Imports only contracts (+ stdlib).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime

from xorcise.core.contracts.otlp import IngestAck, SpanEnvelope
from xorcise.core.contracts.telemetry import TraceRecord


class OtelIngest(ABC):
    @abstractmethod
    def receive(self, spans: list[SpanEnvelope]) -> IngestAck: ...

    @abstractmethod
    def stream(self, run_id: str) -> Iterator[SpanEnvelope]: ...

    @abstractmethod
    def persist(self, run_id: str) -> int: ...


class TraceStore(ABC):
    @abstractmethod
    def append(self, record: TraceRecord) -> None: ...

    @abstractmethod
    def read(self, run_id: str) -> list[TraceRecord]: ...

    @abstractmethod
    def stream(self, run_id: str) -> Iterator[TraceRecord]: ...

    @abstractmethod
    def read_since(self, run_id: str, after_seq: int) -> list[TraceRecord]: ...


class SealStore(ABC):
    @abstractmethod
    def seal(self, run_id: str) -> None: ...

    @abstractmethod
    def is_sealed(self, run_id: str) -> bool: ...

    @abstractmethod
    def sealed_at(self, run_id: str) -> datetime | None: ...

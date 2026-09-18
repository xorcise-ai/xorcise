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
    def seal(self, run_id: str, digest: str | None = None) -> None:
        """Freeze the run. `digest` is computed by the CALLER, not here.

        The digest must cover artifacts as well as telemetry, and artifacts live in another
        part-island this one may not import (the dependency rule) — so it is assembled in the rest
        layer, which is allowed to read across modules, and handed down. Keeping it a parameter
        also leaves this store honest about what it is: durable storage, not a hashing policy.
        """

    @abstractmethod
    def is_sealed(self, run_id: str) -> bool: ...

    @abstractmethod
    def sealed_at(self, run_id: str) -> datetime | None: ...

    @abstractmethod
    def attach_digest(self, run_id: str, digest: str) -> None:
        """Record the digest for an already-sealed run, first-wins.

        Separate from seal() so the caller can close admission BEFORE hashing the evidence —
        hashing first means anything admitted meanwhile is hashed out of existence.
        """

    @abstractmethod
    def evidence_digest(self, run_id: str) -> str | None:
        """The digest recorded at seal time; None when unsealed OR sealed before digests existed.

        Those two cases are deliberately not distinguished here — neither is evidence of tampering,
        and `is_sealed` already separates them for any caller that cares.
        """

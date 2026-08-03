"""Trace store implementations: InMemoryTraceStore (tests) and SqliteTraceStore (default)."""

from __future__ import annotations

from collections.abc import Iterator

from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.otel.ports import TraceStore


class InMemoryTraceStore(TraceStore):
    def __init__(self) -> None:
        self._records: dict[str, list[TraceRecord]] = {}

    def append(self, record: TraceRecord) -> None:
        self._records.setdefault(record.run_id, []).append(record)

    def read(self, run_id: str) -> list[TraceRecord]:
        return list(self._records.get(run_id, []))

    def stream(self, run_id: str) -> Iterator[TraceRecord]:
        yield from self._records.get(run_id, [])

    def read_since(self, run_id: str, after_seq: int) -> list[TraceRecord]:
        return [r for r in self._records.get(run_id, []) if r.seq > after_seq]


from xorcise.core.otel.store.seal import InMemorySealStore, SqliteSealStore  # noqa: E402
from xorcise.core.otel.store.sqlite import (  # noqa: E402  (re-export)
    SqliteLogStore,
    SqliteTraceStore,
)

__all__ = [
    "InMemoryTraceStore",
    "SqliteTraceStore",
    "SqliteLogStore",
    "InMemorySealStore",
    "SqliteSealStore",
]

"""Trace store implementations: InMemoryTraceStore (tests) and SqliteTraceStore (default)."""

from __future__ import annotations

import threading
from collections.abc import Iterator

from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.otel.ports import TraceStore


class InMemoryTraceStore(TraceStore):
    def __init__(self) -> None:
        self._records: dict[str, list[TraceRecord]] = {}
        self._lock = threading.Lock()

    def append_next(self, run_id: str, payload: str) -> int:
        # No seal state here: the receiver's own seal check is all a test double needs.
        with self._lock:
            records = self._records.setdefault(run_id, [])
            seq = max((r.seq for r in records), default=-1) + 1  # MAX + 1, as the SQL store
            records.append(TraceRecord(run_id=run_id, seq=seq, payload=payload))
            return seq

    def append(self, record: TraceRecord) -> None:
        self._records.setdefault(record.run_id, []).append(record)

    def read(self, run_id: str) -> list[TraceRecord]:
        return list(self._records.get(run_id, []))

    def stream(self, run_id: str) -> Iterator[TraceRecord]:
        yield from self._records.get(run_id, [])

    def read_since(self, run_id: str, after_seq: int) -> list[TraceRecord]:
        return [r for r in self._records.get(run_id, []) if r.seq > after_seq]


from xorcise.core.otel.store.run_rows import delete_for_runs, held_run_ids  # noqa: E402
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
    "delete_for_runs",
    "held_run_ids",
]

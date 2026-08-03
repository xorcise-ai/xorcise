"""Seal stores: InMemorySealStore (tests) + SqliteSealStore (default).

The seal marker freezes a run's RAW trace at terminal. It lives in the
otel part-island — never on the runs module — so the hot-path append guard can check
it without importing another domain module (the dependency rule). Idempotent: first seal wins.
"""

from __future__ import annotations

from datetime import UTC, datetime

from xorcise.core.db import session_scope
from xorcise.core.otel.ports import SealStore
from xorcise.core.otel.store.models import TraceSealRow


class InMemorySealStore(SealStore):
    def __init__(self) -> None:
        self._sealed: dict[str, datetime] = {}

    def seal(self, run_id: str) -> None:
        self._sealed.setdefault(run_id, datetime.now(UTC))

    def is_sealed(self, run_id: str) -> bool:
        return run_id in self._sealed

    def sealed_at(self, run_id: str) -> datetime | None:
        return self._sealed.get(run_id)


class SqliteSealStore(SealStore):
    def seal(self, run_id: str) -> None:
        with session_scope() as s:
            if s.get(TraceSealRow, run_id) is None:  # idempotent: first seal wins
                s.add(TraceSealRow(run_id=run_id))

    def is_sealed(self, run_id: str) -> bool:
        with session_scope() as s:
            return s.get(TraceSealRow, run_id) is not None

    def sealed_at(self, run_id: str) -> datetime | None:
        with session_scope() as s:
            row = s.get(TraceSealRow, run_id)
            return row.sealed_at if row is not None else None

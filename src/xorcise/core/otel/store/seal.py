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
        self._digests: dict[str, str] = {}

    def seal(self, run_id: str, digest: str | None = None) -> None:
        self._sealed.setdefault(run_id, datetime.now(UTC))
        if digest is not None:
            self._digests.setdefault(run_id, digest)

    def is_sealed(self, run_id: str) -> bool:
        return run_id in self._sealed

    def sealed_at(self, run_id: str) -> datetime | None:
        return self._sealed.get(run_id)

    def evidence_digest(self, run_id: str) -> str | None:
        return self._digests.get(run_id)

    def attach_digest(self, run_id: str, digest: str) -> None:
        self._digests.setdefault(run_id, digest)


class SqliteSealStore(SealStore):
    def seal(self, run_id: str, digest: str | None = None) -> None:
        with session_scope() as s:
            # First seal wins, digest included. A later seal must NOT overwrite it: whoever can
            # re-seal could otherwise launder edited evidence into a clean verification.
            if s.get(TraceSealRow, run_id) is None:
                s.add(TraceSealRow(run_id=run_id, evidence_digest=digest))

    def is_sealed(self, run_id: str) -> bool:
        with session_scope() as s:
            return s.get(TraceSealRow, run_id) is not None

    def sealed_at(self, run_id: str) -> datetime | None:
        with session_scope() as s:
            row = s.get(TraceSealRow, run_id)
            return row.sealed_at if row is not None else None

    def evidence_digest(self, run_id: str) -> str | None:
        with session_scope() as s:
            row = s.get(TraceSealRow, run_id)
            return row.evidence_digest if row is not None else None

    def attach_digest(self, run_id: str, digest: str) -> None:
        """Record the digest for an ALREADY-sealed run, first-wins.

        Separate from `seal()` so admission can be closed before the evidence is hashed. Never
        overwrites: whoever could replace a digest could launder edited evidence into a clean
        verification, which is the one thing the seal exists to prevent.
        """
        with session_scope() as s:
            row = s.get(TraceSealRow, run_id)
            if row is not None and not row.evidence_digest:
                row.evidence_digest = digest

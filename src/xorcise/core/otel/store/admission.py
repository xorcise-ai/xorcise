"""The per-run admission lock that orders telemetry admission against sealing (#147, #184).

Checking the seal, then appending, in separate steps let a batch that passed the check land after
the seal, while the evidence was being hashed: the digest missed it and an untouched run reported
MISMATCH. Admitting a batch and sealing a run now both start their transaction by updating the
run's `otel_admissions` row. A row update takes the row's write lock on every SQL engine (SQLite
takes the database's), so the second of the two waits until the first commits and then reads what
it wrote: a batch is either committed before the seal, and hashed, or sees the seal and is refused.

Plain UPDATE and INSERT only, nothing engine-specific, so the barrier holds for any database URL
and across processes (a separate collector), not just between threads of one server.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from xorcise.core.db import session_scope
from xorcise.core.otel.store.models import AdmissionRow, TraceSealRow

# Two writers creating a run's first admission row at once: on an engine with row locks, one INSERT
# fails on the primary key. The retry then finds the row and updates it. SQLite never gets here,
# because its writers already take turns.
_ATTEMPTS = 3


def hold_admission(s: Session, run_id: str) -> None:
    """Take the run's admission lock for the rest of `s`'s transaction. Call it FIRST."""
    held = s.execute(
        update(AdmissionRow)
        .where(AdmissionRow.run_id == run_id)
        .values(batches=AdmissionRow.batches + 1)
    )
    if held.rowcount == 0:  # type: ignore[attr-defined]
        s.add(AdmissionRow(run_id=run_id, batches=1))
        s.flush()


def is_sealed_in(s: Session, run_id: str) -> bool:
    """The seal as `s` sees it. After `hold_admission`, a seal that committed first is visible."""
    return bool(s.scalar(select(exists().where(TraceSealRow.run_id == run_id))))


def under_admission[T](run_id: str, work: Callable[[Session], T]) -> T:
    """Run `work` in one transaction that holds the run's admission lock, committing on return."""
    for attempt in range(_ATTEMPTS):
        try:
            with session_scope() as s:
                hold_admission(s, run_id)
                return work(s)
        except IntegrityError:
            if attempt == _ATTEMPTS - 1:
                raise
    raise AssertionError("unreachable")  # pragma: no cover

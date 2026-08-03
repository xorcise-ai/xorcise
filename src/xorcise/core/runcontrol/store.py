"""Submission stores: InMemory (tests) + Sqlite (default). Run-scoped append + read."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select

from xorcise.core.db import session_scope
from xorcise.core.runcontrol.models import RunSubmissionRow


def _utc(value: datetime | None) -> datetime | None:
    """SQLite drops timezone metadata; persisted timestamps are UTC by contract."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


# Submission kinds that are agent-submitted work artifacts (the flag is the "flag" artifact)
# — as opposed to intel/complete control markers. The grader scores these, and the
# operator artifacts view surfaces exactly these for review.
ARTIFACT_KINDS = frozenset({"flag", "artifact"})


@dataclass(frozen=True)
class RunSubmission:
    run_id: str
    kind: str
    name: str
    payload: str
    seq: int
    # XORCISE-core-clock stamp (run_submissions.created_at). The unified terrain timeline anchors
    # each run-control interaction to its own created_at (per-path anchoring). None for the
    # in-memory store (no clock) and back-compat with positional construction.
    created_at: datetime | None = None


class SubmissionStore(ABC):
    @abstractmethod
    def record(self, run_id: str, kind: str, name: str, payload: str) -> None: ...

    @abstractmethod
    def list_for_run(self, run_id: str) -> tuple[RunSubmission, ...]: ...

    @abstractmethod
    def count(self, run_id: str, kind: str) -> int: ...


class InMemorySubmissionStore(SubmissionStore):
    def __init__(self) -> None:
        self._rows: list[RunSubmission] = []

    def record(self, run_id: str, kind: str, name: str, payload: str) -> None:
        self._rows.append(RunSubmission(run_id, kind, name, payload, len(self._rows) + 1))

    def list_for_run(self, run_id: str) -> tuple[RunSubmission, ...]:
        return tuple(r for r in self._rows if r.run_id == run_id)

    def count(self, run_id: str, kind: str) -> int:
        return sum(1 for r in self._rows if r.run_id == run_id and r.kind == kind)


class SqliteSubmissionStore(SubmissionStore):
    def record(self, run_id: str, kind: str, name: str, payload: str) -> None:
        with session_scope() as s:
            s.add(RunSubmissionRow(run_id=run_id, kind=kind, name=name, payload=payload))

    def list_for_run(self, run_id: str) -> tuple[RunSubmission, ...]:
        with session_scope() as s:
            rows = s.scalars(
                select(RunSubmissionRow)
                .where(RunSubmissionRow.run_id == run_id)
                .order_by(RunSubmissionRow.seq)
            ).all()
            return tuple(
                RunSubmission(
                    r.run_id, r.kind, r.name, r.payload, r.seq, created_at=_utc(r.created_at)
                )
                for r in rows
            )

    def count(self, run_id: str, kind: str) -> int:
        with session_scope() as s:
            return int(
                s.scalar(
                    select(func.count())
                    .select_from(RunSubmissionRow)
                    .where(RunSubmissionRow.run_id == run_id, RunSubmissionRow.kind == kind)
                )
                or 0
            )


def disclosed_intel_count(run_id: str) -> int:
    """How many intel were disclosed to *run_id* (kind='intel' submissions written by get_intel).

    The disclosure-provenance signal: within the run-control module (reads only its own store), so
    the delivery layer can surface it on ResultConditions.intel_disclosed without a results-table
    migration (the rows already exist)."""
    return SqliteSubmissionStore().count(run_id, "intel")

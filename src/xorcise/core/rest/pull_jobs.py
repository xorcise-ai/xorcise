"""Pull job store (delivery/rest layer) — job-based GUI pulls with byte progress.

A library pull downloads a multi-GB fused image, far past what a browser will hold one
request open for. So POST /missions/{id}/pull-jobs starts the pull in the background and
returns a job_id; this store tracks each job's status, phase, and byte progress, and
GET /missions/pull-jobs/{job_id} reads them back so the GUI can poll.

Cloned from ingest_jobs.py: DB-backed, so jobs survive a
server restart and are visible across processes; a job left `pulling` after a restart is
orphaned (its worker thread died with the process) and reconcile_pull_jobs() marks it error
on boot. `start()` additionally dedups on an active job for the same mission_id — the
server-side double-pull guard (two tabs / card + detail / GUI + CLI).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, Float, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from xorcise.core.db import Base, session_scope

_PULLING = "pulling"
_INSTALLED = "installed"
_ERROR = "error"
_CANCELLED = "cancelled"

# phase: resolving → pulling_image → downloading_bundle → installing → done
_PHASE_RESOLVING = "resolving"
_PHASE_DONE = "done"


class PullJobRow(Base):
    """One background pull job. bytes_* aggregate docker's per-layer progress (image bytes)."""

    __tablename__ = "pull_jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(16), default=_PULLING)
    phase: Mapped[str] = mapped_column(String(32), default=_PHASE_RESOLVING)
    bytes_current: Mapped[int] = mapped_column(BigInteger, default=0)
    bytes_total: Mapped[int] = mapped_column(BigInteger, default=0)
    started_at: Mapped[float] = mapped_column(Float, default=0.0)  # epoch seconds, for ETA
    image: Mapped[str | None] = mapped_column(String(255), default=None)
    detail: Mapped[str | None] = mapped_column(Text, default=None)
    # Cooperative-cancel flag. Kept SEPARATE from `status` (which stays 'pulling') so the
    # active-job dedup in active_for() still sees the job as in-flight while its worker unwinds —
    # a second pull must not start atop a still-dying worker. The worker flips status to
    # 'cancelled' once it actually stops (XOR pull-cancel).
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)


@dataclass
class PullJob:
    job_id: str
    mission_id: str
    status: str = _PULLING  # pulling → installed | error | cancelled
    phase: str = _PHASE_RESOLVING
    bytes_current: int = 0
    bytes_total: int = 0
    started_at: float = 0.0
    image: str | None = None
    detail: str | None = None  # error detail when status == error
    cancel_requested: bool = False  # a cancel was requested; worker still unwinding until cancelled


def _to_job(row: PullJobRow) -> PullJob:
    return PullJob(
        job_id=row.job_id,
        mission_id=row.mission_id,
        status=row.status,
        phase=row.phase,
        bytes_current=row.bytes_current,
        bytes_total=row.bytes_total,
        started_at=row.started_at,
        image=row.image,
        detail=row.detail,
        cancel_requested=row.cancel_requested,
    )


class PullJobStore:
    """DB-backed store of job_id → pull job. Stateless: every call reads/writes the pull_jobs
    table, so jobs survive a server restart and are shared across processes."""

    def start(self, mission_id: str) -> tuple[str, bool]:
        """Start (or join) a pull job for a mission; returns (job_id, created).

        Server-side double-pull guard: inside one session, an existing still-'pulling' job for
        the same mission_id is returned as-is (created=False) so a second POST — from another
        tab, the detail page, or a reload — joins the in-flight pull instead of racing it."""
        with session_scope() as s:
            existing = s.scalar(
                select(PullJobRow).where(
                    PullJobRow.mission_id == mission_id, PullJobRow.status == _PULLING
                )
            )
            if existing is not None:
                return existing.job_id, False
            job_id = uuid4().hex
            s.add(
                PullJobRow(
                    job_id=job_id,
                    mission_id=mission_id,
                    status=_PULLING,
                    phase=_PHASE_RESOLVING,
                    bytes_current=0,
                    bytes_total=0,
                    started_at=time.time(),
                )
            )
            return job_id, True

    def set_progress(
        self, job_id: str, *, phase: str, bytes_current: int, bytes_total: int
    ) -> None:
        """Record phase + aggregated byte progress. Callers throttle (~2 writes/s) — docker
        emits layer events far faster than SQLite likes being written."""
        with session_scope() as s:
            row = s.scalar(select(PullJobRow).where(PullJobRow.job_id == job_id))
            if row is not None:
                row.phase = phase
                row.bytes_current = bytes_current
                row.bytes_total = bytes_total

    def finish_ok(self, job_id: str, *, image: str) -> None:
        with session_scope() as s:
            row = s.scalar(select(PullJobRow).where(PullJobRow.job_id == job_id))
            if row is not None:
                row.status = _INSTALLED
                row.phase = _PHASE_DONE
                row.image = image

    def finish_error(self, job_id: str, detail: str) -> None:
        with session_scope() as s:
            row = s.scalar(select(PullJobRow).where(PullJobRow.job_id == job_id))
            if row is not None:
                row.status = _ERROR
                row.detail = detail

    def request_cancel(self, job_id: str) -> PullJob | None:
        """Ask an in-flight pull to stop; returns the job (updated view) or None if unknown.

        Sets cancel_requested only while the job is still 'pulling' — a terminal job
        (installed/error/cancelled) is returned unchanged, so a cancel that races the pull's
        completion is a harmless idempotent no-op. The status stays 'pulling' (the dedup guard
        must keep seeing the job as active until its worker finishes); the worker flips it to
        'cancelled' via finish_cancelled()."""
        with session_scope() as s:
            row = s.scalar(select(PullJobRow).where(PullJobRow.job_id == job_id))
            if row is None:
                return None
            if row.status == _PULLING:
                row.cancel_requested = True
            return _to_job(row)

    def is_cancel_requested(self, job_id: str) -> bool:
        """Whether a cancel has been requested for this job (False if unknown). The pull worker
        polls this (throttled) to decide whether to abort."""
        with session_scope() as s:
            row = s.scalar(select(PullJobRow).where(PullJobRow.job_id == job_id))
            return bool(row.cancel_requested) if row is not None else False

    def finish_cancelled(self, job_id: str) -> None:
        """Terminal 'cancelled' — the worker acknowledged the cancel and stopped before install.
        Nothing was installed (parity with finish_error's not-installed contract)."""
        with session_scope() as s:
            row = s.scalar(select(PullJobRow).where(PullJobRow.job_id == job_id))
            if row is not None:
                row.status = _CANCELLED

    def get(self, job_id: str) -> PullJob | None:
        with session_scope() as s:
            row = s.scalar(select(PullJobRow).where(PullJobRow.job_id == job_id))
            return _to_job(row) if row is not None else None

    def active_for(self, mission_id: str) -> PullJob | None:
        """The still-'pulling' job for this mission, if any — reload-resume + dedup lookup."""
        with session_scope() as s:
            row = s.scalar(
                select(PullJobRow).where(
                    PullJobRow.mission_id == mission_id, PullJobRow.status == _PULLING
                )
            )
            return _to_job(row) if row is not None else None


def reconcile_pull_jobs() -> int:
    """Mark every still-'pulling' job as error on boot. Returns the count.

    A job that is 'pulling' in a freshly-started process is orphaned: its background worker
    died with the previous process, so the GUI would poll (and the dedup would block re-pulls)
    forever. Terminal jobs (installed/error) are left untouched — same contract as
    reconcile_ingest_jobs."""
    with session_scope() as s:
        rows = s.scalars(select(PullJobRow).where(PullJobRow.status == _PULLING)).all()
        for row in rows:
            row.status = _ERROR
            row.detail = "interrupted by a server restart"
        return len(rows)


_STORE = PullJobStore()


def pull_job_store() -> PullJobStore:
    """The process-wide pull job store (stateless DB facade)."""
    return _STORE

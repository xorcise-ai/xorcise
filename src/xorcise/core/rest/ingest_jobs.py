"""Ingest job store (delivery/rest layer).

`mission ingest` runs a host-local Docker build that can take minutes, far past the CLI's read
timeout. So POST /missions/ingest starts the build in the background and returns a job_id; this
store tracks each job's status, result, and progress log lines, and GET /missions/ingest/{id}
reads them back so the CLI can poll and stream the ingestion status.

DB-backed: job status is persisted, so it survives a server restart and
is visible across processes — the store is now a thin stateless facade over the ingest_jobs table,
not a process-local map. A job left `building` after a restart is orphaned (its worker thread died
with the process); reconcile_ingest_jobs() marks such jobs error on boot so the poll ends cleanly.
The rest layer sits above db in the inward-only layers, so importing session_scope/Base is allowed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from xorcise.core.db import Base, session_scope

_BUILDING = "building"
_INSTALLED = "installed"
_ERROR = "error"


class IngestJobRow(Base):
    """One background ingest job. logs is a JSON array of progress lines."""

    __tablename__ = "ingest_jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default=_BUILDING)
    logs_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    slug: Mapped[str | None] = mapped_column(String(255), default=None)
    image: Mapped[str | None] = mapped_column(String(255), default=None)
    detail: Mapped[str | None] = mapped_column(Text, default=None)


@dataclass
class IngestJob:
    job_id: str
    status: str = _BUILDING  # building → installed | error
    logs: list[str] = None  # type: ignore[assignment]  # replaced in __post_init__
    slug: str | None = None
    image: str | None = None
    detail: str | None = None  # error detail when status == error

    def __post_init__(self) -> None:
        if self.logs is None:
            self.logs = []


def _to_job(row: IngestJobRow) -> IngestJob:
    return IngestJob(
        job_id=row.job_id,
        status=row.status,
        logs=json.loads(row.logs_json),
        slug=row.slug,
        image=row.image,
        detail=row.detail,
    )


class IngestJobStore:
    """DB-backed store of job_id → ingest job. Stateless: every call reads/writes the ingest_jobs
    table, so jobs survive a server restart and are shared across processes."""

    def start(self) -> str:
        job_id = uuid4().hex
        with session_scope() as s:
            s.add(IngestJobRow(job_id=job_id, status=_BUILDING, logs_json="[]"))
        return job_id

    def append_log(self, job_id: str, line: str) -> None:
        with session_scope() as s:
            row = s.scalar(select(IngestJobRow).where(IngestJobRow.job_id == job_id))
            if row is not None:
                logs = json.loads(row.logs_json)
                logs.append(line)
                row.logs_json = json.dumps(logs)

    def finish_ok(self, job_id: str, *, slug: str, image: str) -> None:
        with session_scope() as s:
            row = s.scalar(select(IngestJobRow).where(IngestJobRow.job_id == job_id))
            if row is not None:
                row.status = _INSTALLED
                row.slug = slug
                row.image = image
                logs = json.loads(row.logs_json)
                logs.append(f"installed {slug} ({image})")
                row.logs_json = json.dumps(logs)

    def finish_error(self, job_id: str, detail: str) -> None:
        with session_scope() as s:
            row = s.scalar(select(IngestJobRow).where(IngestJobRow.job_id == job_id))
            if row is not None:
                row.status = _ERROR
                row.detail = detail
                logs = json.loads(row.logs_json)
                logs.append(f"error: {detail}")
                row.logs_json = json.dumps(logs)

    def get(self, job_id: str) -> IngestJob | None:
        with session_scope() as s:
            row = s.scalar(select(IngestJobRow).where(IngestJobRow.job_id == job_id))
            # _to_job builds a fresh list from the JSON, so a reader never observes a
            # concurrently-mutating log list (the background worker appends to the row).
            return _to_job(row) if row is not None else None


def reconcile_ingest_jobs() -> int:
    """Mark every still-'building' job as error on boot. Returns the count.

    A job that is 'building' in a freshly-started process is orphaned: the background worker that
    was building it died with the previous process and will never finish it, so the CLI/GUI would
    poll forever. Marking it error ends the poll cleanly. Terminal jobs (installed/error) are left
    untouched — this only reconciles the in-flight state that cannot survive a restart."""
    with session_scope() as s:
        rows = s.scalars(select(IngestJobRow).where(IngestJobRow.status == _BUILDING)).all()
        for row in rows:
            row.status = _ERROR
            row.detail = "interrupted by a server restart"
            logs = json.loads(row.logs_json)
            logs.append("error: interrupted by a server restart")
            row.logs_json = json.dumps(logs)
        return len(rows)


_STORE = IngestJobStore()


def job_store() -> IngestJobStore:
    """The process-wide ingest job store (stateless DB facade)."""
    return _STORE

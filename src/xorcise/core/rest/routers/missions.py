"""Missions router — real wiring (browse, pull, ingest job + manifest)."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from pydantic import BaseModel

from xorcise.core.config import get_settings
from xorcise.core.contracts.catalog import CatalogEntry
from xorcise.core.contracts.errors import NotFoundError
from xorcise.core.contracts.mission import MissionManifest, MissionMetadata
from xorcise.core.contracts.terrain import ResolvedTerrainV2
from xorcise.core.missions.errors import MissionCollisionError
from xorcise.core.rest.catalog_view import build_catalog_view_deps, fetch_manifest, list_catalog
from xorcise.core.rest.ingest import ingest_bundle
from xorcise.core.rest.ingest_jobs import job_store
from xorcise.core.rest.mission_pull import (
    MissionNotInCatalogError,
    PullCancelled,
    PullError,
    PullProgressSink,
    build_pull_deps,
    pull_mission,
)
from xorcise.core.rest.pull_jobs import PullJob, pull_job_store

router = APIRouter(prefix="/missions", tags=["missions"])


class IngestRequest(BaseModel):
    """Ingest a local bundle directory into Your Own (builds its fused image)."""

    bundle_dir: str


class IngestStarted(BaseModel):
    job_id: str


class IngestJobView(BaseModel):
    job_id: str
    status: str  # building | installed | error
    logs: list[str]
    slug: str | None = None
    image: str | None = None
    detail: str | None = None


class PullJobStarted(BaseModel):
    job_id: str


class PullJobView(BaseModel):
    """Poll view of a background pull. percent/eta_seconds are server-computed: percent is
    None while the total is unknown (indeterminate UI), and neither is trusted to be
    monotonic — docker's layer totals grow as layers are discovered mid-pull."""

    job_id: str
    mission_id: str
    status: str  # pulling | installed | error | cancelled
    phase: str  # resolving | pulling_image | downloading_bundle | installing | done
    bytes_current: int
    bytes_total: int
    percent: float | None = None
    eta_seconds: float | None = None
    detail: str | None = None
    cancel_requested: bool = False  # a cancel was requested; status stays pulling until cancelled
    entry: CatalogEntry | None = None  # the installed entry, once status == installed


def _installed_entry(
    m: MissionMetadata, image: str | None, source: Literal["your_own", "library"]
) -> CatalogEntry:
    """A CatalogEntry from an installed manifest's metadata. `source` reflects the install
    origin — "library" for a remote pull, "your_own" for a local ingest."""
    from xorcise.core.rest.catalog_view import base_compat_of

    compat = base_compat_of(image)
    return CatalogEntry(
        source=source,
        mission_id=m.mission_id,
        name=m.name,
        summary=m.summary,
        proficiency=m.proficiency,
        specialty=m.specialty,
        type=m.type,
        skills=m.skills,
        technologies=m.technologies,
        installed=True,
        image=image,
        base_version=compat.base_major,
        compatible=compat.compatible,
        compat_hint=compat.hint,
    )


@router.get("")
def list_missions() -> list[CatalogEntry]:
    """Browse Your Own (local store) + the free XORCISE library (no account/key)."""
    return list(list_catalog(build_catalog_view_deps(get_settings())))


def _run_ingest(job_id: str, bundle_dir: str) -> None:
    """Background worker: build + install a local bundle, streaming status to the job.

    Routes the install write through the server so the controller stays the single writer of the
    installed-mission set (parity with /pull). The fused-image build is host-local
    Docker; in the local topology the server shares the host filesystem. Any failure is recorded on
    the job (never crashes the worker) so both the CLI and the GUI see a clean error, not a hung
    poll."""
    store = job_store()
    bundle = Path(bundle_dir).expanduser()
    store.append_log(job_id, f"starting ingest of {bundle}")
    if not bundle.is_dir():
        store.finish_error(job_id, f"not a directory: {bundle}")
        return
    store.append_log(job_id, "building fused image (this can take a while)…")
    try:
        ic = ingest_bundle(bundle, get_settings())
    except Exception as exc:  # noqa: BLE001 — worker boundary: record the failure, don't die
        store.finish_error(job_id, str(exc))
        return
    store.finish_ok(job_id, slug=ic.slug, image=ic.mission_ref.image)


@router.post("/ingest", status_code=202)
def ingest(payload: IngestRequest, background: BackgroundTasks) -> IngestStarted:
    """Start a local-bundle ingest and return a job id immediately.

    The host-local Docker build can take minutes — far past the CLI's read timeout and long enough
    to hang a browser request — so it runs on a background task; poll GET /missions/ingest/{id}
    for status + progress logs (the CLI and the GUI both do). Routes the install through the
    controller so the server remains the single writer (parity with /pull)."""
    job_id = job_store().start()
    background.add_task(_run_ingest, job_id, payload.bundle_dir)
    return IngestStarted(job_id=job_id)


@router.get("/ingest/{job_id}")
def ingest_status(job_id: str, since: int = 0) -> IngestJobView:
    """Ingest job status + progress logs. `since` = log-index cursor (default 0=all)."""
    job = job_store().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no ingest job '{job_id}'")
    logs = job.logs[since:] if since > 0 else job.logs
    return IngestJobView(
        job_id=job.job_id,
        status=job.status,
        logs=logs,
        slug=job.slug,
        image=job.image,
        detail=job.detail,
    )


@router.post("/{mission_id}/pull")
def pull(mission_id: str) -> CatalogEntry:
    """Pull a free-library mission (image-pull) so it becomes selectable/runnable."""
    # A pull job already downloading this mission (GUI) must not race a second, synchronous
    # pull (CLI): both would run docker pull and race install_pulled's atomic rename.
    if pull_job_store().active_for(mission_id) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"a pull for '{mission_id}' is already in progress — wait for it to finish",
        )
    try:
        ic = pull_mission(mission_id, build_pull_deps(get_settings()))
    except MissionNotInCatalogError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MissionCollisionError as exc:  # id already owned by a your_own install
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PullError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _installed_entry(ic.manifest.metadata, ic.mission_ref.image, ic.origin)


def _job_progress_sink(job_id: str) -> PullProgressSink:
    """Forward pull progress into the job row, throttled to ~2 writes/s — docker emits layer
    events far faster than SQLite likes being written. Phase transitions always land, and they
    reuse the last known byte counts (a phase report carries 0/0) so the bar stays full through
    downloading_bundle/installing instead of dropping back to indeterminate."""
    store = pull_job_store()
    last_write = 0.0
    last_phase = ""
    last_bytes = (0, 0)

    def sink(phase: str, current: int, total: int) -> None:
        nonlocal last_write, last_phase, last_bytes
        if total > 0:
            last_bytes = (current, total)
        now = time.monotonic()
        if phase == last_phase and now - last_write < 0.5:
            return
        last_write, last_phase = now, phase
        cur, tot = last_bytes
        store.set_progress(job_id, phase=phase, bytes_current=cur, bytes_total=tot)

    return sink


_CANCEL_PROBE_INTERVAL = 0.5  # seconds between DB reads of the cancel flag (docker fires layer
# events far faster than SQLite likes being read — the probe caches between reads)


def _cancel_probe(job_id: str) -> Callable[[], bool]:
    """A throttled `should_cancel` for the pull spine: reads the job's cancel_requested flag at
    most every _CANCEL_PROBE_INTERVAL and caches it between reads. The spine calls this per
    download event, so an un-throttled DB read here would hammer SQLite. First call always reads
    (time.monotonic() ≫ 0), so a cancel requested before the pull even starts is honoured."""
    store = pull_job_store()
    last_read = 0.0
    cached = False

    def probe() -> bool:
        nonlocal last_read, cached
        now = time.monotonic()
        if now - last_read >= _CANCEL_PROBE_INTERVAL:
            last_read = now
            cached = store.is_cancel_requested(job_id)
        return cached

    return probe


def _run_pull(job_id: str, mission_id: str) -> None:
    """Background worker: pull + install a library mission, streaming byte progress to the
    job. Any failure is recorded on the job (never crashes the worker) so the GUI poll ends
    cleanly — the same worker-boundary contract as _run_ingest. A cancel request (via the
    throttled probe) aborts the pull and records `cancelled` — distinct from `error`."""
    store = pull_job_store()
    try:
        ic = pull_mission(
            mission_id,
            build_pull_deps(get_settings()),
            progress=_job_progress_sink(job_id),
            should_cancel=_cancel_probe(job_id),
        )
    except PullCancelled:  # cooperative cancel — a clean stop, not a failure
        store.finish_cancelled(job_id)
        return
    except Exception as exc:  # noqa: BLE001 — worker boundary: record the failure, don't die
        store.finish_error(job_id, str(exc))
        return
    store.finish_ok(job_id, image=ic.mission_ref.image)


def _pull_job_view(job: PullJob) -> PullJobView:
    """Server-computed poll view: percent stays None while the total is unknown; ETA comes
    from the average byte rate since the job started (None until bytes flow)."""
    percent: float | None = None
    eta: float | None = None
    if job.bytes_total > 0:
        percent = min(100.0, job.bytes_current * 100.0 / job.bytes_total)
        elapsed = time.time() - job.started_at
        if job.bytes_current > 0 and elapsed > 0:
            rate = job.bytes_current / elapsed
            eta = max(0.0, (job.bytes_total - job.bytes_current) / rate)
    entry: CatalogEntry | None = None
    if job.status == "installed":
        percent, eta = 100.0, 0.0
        from xorcise.core.missions import get_installed

        ic = get_installed(job.mission_id, Path(get_settings().missions_root))
        if ic is not None:
            entry = _installed_entry(ic.manifest.metadata, ic.mission_ref.image, ic.origin)
    return PullJobView(
        job_id=job.job_id,
        mission_id=job.mission_id,
        status=job.status,
        phase=job.phase,
        bytes_current=job.bytes_current,
        bytes_total=job.bytes_total,
        percent=percent,
        eta_seconds=eta,
        detail=job.detail,
        cancel_requested=job.cancel_requested,
        entry=entry,
    )


@router.post("/{mission_id}/pull-jobs", status_code=202)
def start_pull_job(mission_id: str, background: BackgroundTasks) -> PullJobStarted:
    """Start (or join) a background pull for a library mission (202 + job_id, idempotent).

    The image download can take minutes — far past what a browser will hold one request open
    for — so it runs on a background task; poll GET /missions/pull-jobs/{job_id}. A POST
    while a pull for this mission is active returns the SAME job_id without starting a
    second worker (server-side double-pull guard)."""
    job_id, created = pull_job_store().start(mission_id)
    if created:
        background.add_task(_run_pull, job_id, mission_id)
    return PullJobStarted(job_id=job_id)


@router.get("/pull-jobs/{job_id}")
def pull_job_status(job_id: str) -> PullJobView:
    """Pull job status + progress (poll target for the GUI)."""
    job = pull_job_store().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no pull job '{job_id}'")
    return _pull_job_view(job)


@router.get("/pull-jobs")
def active_pull_job(mission_id: str) -> PullJobView | None:
    """The active ('pulling') job for a mission, or null — lets a reloaded GUI resume its
    progress bar and keeps a second view's Pull button disabled."""
    job = pull_job_store().active_for(mission_id)
    return _pull_job_view(job) if job is not None else None


@router.post("/pull-jobs/{job_id}/cancel")
def cancel_pull_job(job_id: str) -> PullJobView:
    """Ask an in-flight pull to stop (GUI Cancel button / CLI Ctrl-C).

    Sets the job's cooperative-cancel flag; the background worker checks it (per download event,
    throttled) and aborts before install, recording `cancelled`. Returns the current job view so
    the caller sees `cancel_requested` immediately. Idempotent: cancelling an already-terminal job
    (it finished racing the cancel) returns its view unchanged. 404 if the job is unknown."""
    job = pull_job_store().request_cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no pull job '{job_id}'")
    return _pull_job_view(job)


@router.delete("/{mission_id}", status_code=204)
def delete_mission(mission_id: str) -> Response:
    """Uninstall an installed mission — local (your_own) or pulled (library).

    Removes the local install directory so the mission leaves Your Own / drops back to
    not-installed in the library. 404 if nothing is installed under that id. Routes the write
    through the server's install root (single writer, parity with ingest/pull). The fused image is
    intentionally left in the local store; pruning it is a separate concern.
    """
    from xorcise.core.missions.runtime import delete_installed

    install_root = Path(get_settings().missions_root)
    if not delete_installed(mission_id, install_root):
        raise HTTPException(status_code=404, detail=f"no installed mission '{mission_id}'")
    return Response(status_code=204)


@router.get("/{mission_id}/manifest")
def mission_manifest(mission_id: str) -> MissionManifest:
    """The full mission.json for an id — Your Own copy if installed, else the library."""
    try:
        return fetch_manifest(mission_id, build_catalog_view_deps(get_settings()))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no mission '{mission_id}'") from exc


@router.get("/{mission_id}/terrain", response_model=ResolvedTerrainV2)
def mission_terrain(mission_id: str) -> ResolvedTerrainV2:
    """The mission's projected v2 terrain — the same base graph a run of it starts from.

    Runs the run-scoped projector over the manifest (installed copy, else the library) with
    no run attached: infra scaffold + authored/static-ips mission plane, every element in its
    base state, no updates. This is what the mission detail page draws so the ACTUAL terrain
    map — not a simplification — is visible before a run (or a pull). Unknown id → 404."""
    # Lazy: same plane-isolation rule as runs.py's terrain endpoint — keep the runs module off
    # this router's import path at boot (the projector itself is pure, contracts-only).
    from xorcise.core.runs.terrain_v2 import project_terrain_v2

    try:
        manifest = fetch_manifest(mission_id, build_catalog_view_deps(get_settings()))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no mission '{mission_id}'") from exc
    # run_id "" — mission-scoped, there is no run; the graph is identical to a fresh run's.
    return project_terrain_v2("", mission_id, manifest)

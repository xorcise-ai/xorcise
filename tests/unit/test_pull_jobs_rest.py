"""Job-based pull endpoints: 202 + poll, server-side double-pull dedup, the 409 guard on the
legacy sync pull, and the active-job lookup the GUI uses for reload-resume. Background tasks
run inline under TestClient, so a started job is terminal by the time the POST returns."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from xorcise.core.rest.pull_jobs import pull_job_store
from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.unit


def _client() -> TestClient:
    return TestClient(build_rest_app())


def _stub_catalog(monkeypatch) -> None:
    """Force the in-memory fixture library at the shared source seam (same trick as
    test_missions_rest.py) so these tests exercise the job wiring without a live remote."""
    from xorcise.core.catalog import StubCatalogSource
    from xorcise.core.rest import catalog_view

    monkeypatch.setattr(
        catalog_view, "build_catalog_source", lambda settings: StubCatalogSource(enabled=True)
    )


def test_pull_job_installs_a_library_mission(migrated_home, monkeypatch):
    """POST returns 202 + job_id; the job pulls + installs async and the poll view carries the
    server-computed percent and the installed entry."""
    _stub_catalog(monkeypatch)
    c = _client()
    r = c.post("/api/missions/sqli-login/pull-jobs")
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    job = c.get(f"/api/missions/pull-jobs/{job_id}").json()  # bg task ran inline
    assert job["status"] == "installed"
    assert job["phase"] == "done"
    assert job["percent"] == 100.0
    # StubDockerDriver's synthetic layer events flowed through the throttled sink.
    assert job["bytes_total"] > 0
    assert job["bytes_current"] == job["bytes_total"]
    assert job["entry"]["installed"] is True
    assert job["entry"]["source"] == "library"

    listed = {e["mission_id"]: e for e in c.get("/api/missions").json()}
    assert listed["sqli-login"]["installed"] is True


def test_pull_job_unknown_mission_ends_in_error(migrated_home, monkeypatch):
    """An unknown id still 202s; the failure is recorded on the job, never a hung poll."""
    _stub_catalog(monkeypatch)
    c = _client()
    r = c.post("/api/missions/nope/pull-jobs")
    assert r.status_code == 202
    job = c.get(f"/api/missions/pull-jobs/{r.json()['job_id']}").json()
    assert job["status"] == "error"
    assert "catalog" in job["detail"]


def test_pull_job_status_unknown_id_404(migrated_home):
    assert _client().get("/api/missions/pull-jobs/does-not-exist").status_code == 404


def test_start_is_idempotent_while_a_job_is_active(migrated_home, monkeypatch):
    """A second POST while a pull is in flight joins the SAME job (no second worker)."""
    _stub_catalog(monkeypatch)
    # Seed an in-flight job directly (TestClient runs workers inline, so a POSTed job would
    # already be terminal by the time we could re-POST).
    active_id, created = pull_job_store().start("sqli-login")
    assert created is True

    r = _client().post("/api/missions/sqli-login/pull-jobs")
    assert r.status_code == 202
    assert r.json()["job_id"] == active_id
    # still the one in-flight job, untouched by the joined POST
    job = pull_job_store().get(active_id)
    assert job is not None and job.status == "pulling"


def test_sync_pull_conflicts_409_while_a_job_is_active(migrated_home, monkeypatch):
    """CLI vs GUI: the legacy sync POST /pull refuses to race an active pull job."""
    _stub_catalog(monkeypatch)
    pull_job_store().start("sqli-login")
    r = _client().post("/api/missions/sqli-login/pull")
    assert r.status_code == 409
    assert "already in progress" in r.json()["detail"]


def test_sync_pull_still_works_when_no_job_is_active(migrated_home, monkeypatch):
    """The guard must not break the existing CLI pull flow."""
    _stub_catalog(monkeypatch)
    r = _client().post("/api/missions/sqli-login/pull")
    assert r.status_code == 200 and r.json()["installed"] is True


def test_active_job_lookup_for_reload_resume(migrated_home, monkeypatch):
    """GET /missions/pull-jobs?mission_id= returns the in-flight job (or null)."""
    _stub_catalog(monkeypatch)
    c = _client()
    assert c.get("/api/missions/pull-jobs", params={"mission_id": "sqli-login"}).json() is None

    active_id, _ = pull_job_store().start("sqli-login")
    got = c.get("/api/missions/pull-jobs", params={"mission_id": "sqli-login"}).json()
    assert got is not None
    assert got["job_id"] == active_id and got["status"] == "pulling"
    assert got["percent"] is None  # total unknown → indeterminate UI


def test_cancel_endpoint_flags_an_active_job(migrated_home, monkeypatch):
    """POST .../cancel sets cancel_requested on an in-flight job; status stays 'pulling' (the
    worker flips it to 'cancelled' once it unwinds)."""
    _stub_catalog(monkeypatch)
    active_id, _ = pull_job_store().start("sqli-login")  # seed in-flight (no inline worker)

    r = _client().post(f"/api/missions/pull-jobs/{active_id}/cancel")
    assert r.status_code == 200
    body = r.json()
    assert body["cancel_requested"] is True
    assert body["status"] == "pulling"
    # The flag is persisted for the worker's throttled probe to read.
    assert pull_job_store().is_cancel_requested(active_id) is True


def test_cancel_endpoint_unknown_job_404(migrated_home):
    assert _client().post("/api/missions/pull-jobs/does-not-exist/cancel").status_code == 404


def test_cancel_endpoint_is_idempotent_on_a_terminal_job(migrated_home, monkeypatch):
    """Cancelling a job that already finished (raced the cancel) returns its view unchanged."""
    _stub_catalog(monkeypatch)
    c = _client()
    job_id = c.post("/api/missions/sqli-login/pull-jobs").json()["job_id"]  # runs inline → done
    r = c.post(f"/api/missions/pull-jobs/{job_id}/cancel")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "installed"  # unchanged
    assert body["cancel_requested"] is False


def test_worker_records_cancelled_when_cancel_is_requested(migrated_home, monkeypatch):
    """End-to-end: with the cancel probe tripped, the inline worker aborts the pull and records
    `cancelled` (not `error`), and nothing is installed."""
    _stub_catalog(monkeypatch)
    from xorcise.core.rest.routers import missions as ch

    # Force the worker's cancel probe to True so the pull aborts on its first checkpoint.
    monkeypatch.setattr(ch, "_cancel_probe", lambda job_id: lambda: True)

    c = _client()
    job_id = c.post("/api/missions/sqli-login/pull-jobs").json()["job_id"]  # runs inline
    job = c.get(f"/api/missions/pull-jobs/{job_id}").json()
    assert job["status"] == "cancelled"
    # not-installed: the mission is still available in the catalog, never in Your Own.
    listed = {e["mission_id"]: e for e in c.get("/api/missions").json()}
    assert listed["sqli-login"]["installed"] is False


def test_cancel_probe_reads_first_then_throttles_then_refreshes(monkeypatch):
    """The worker's throttled cancel probe (_cancel_probe) — the sole production glue between a
    requested cancel and the pull spine. Directly exercises the real closure with a counting fake
    store and a fake clock (the inline-worker tests stub it out, so its throttle/first-read/cache
    semantics are otherwise unverified)."""
    import time

    from xorcise.core.rest.routers import missions as ch

    reads = {"n": 0}
    flag = {"v": False}

    class _FakeStore:
        def is_cancel_requested(self, job_id: str) -> bool:
            reads["n"] += 1
            return flag["v"]

    clock = {"t": 1000.0}
    monkeypatch.setattr(ch, "pull_job_store", lambda: _FakeStore())
    # _cancel_probe reads time.monotonic() via `import time`; patch the module the probe sees.
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])

    probe = ch._cancel_probe("j1")
    # First call reads the store (last_read starts at 0.0 ≪ now) — an up-front cancel is honoured.
    assert probe() is False
    assert reads["n"] == 1
    # The flag flips, but within the throttle window the cached value is returned; no new read.
    flag["v"] = True
    assert probe() is False
    assert reads["n"] == 1
    # Past the interval the probe re-reads and observes the newly-set flag (a mid-pull cancel).
    clock["t"] += ch._CANCEL_PROBE_INTERVAL + 0.01
    assert probe() is True
    assert reads["n"] == 2


def test_progress_sink_throttles_same_phase_and_keeps_bytes_on_phase_change(migrated_home):
    """The sink writes phase transitions immediately, drops rapid same-phase updates, and a
    phase report (which carries 0/0) reuses the last known byte counts."""
    from xorcise.core.rest.routers.missions import _job_progress_sink

    job_id, _ = pull_job_store().start("sqli-login")
    sink = _job_progress_sink(job_id)

    sink("pulling_image", 0, 0)  # phase change → written
    sink("pulling_image", 512, 1024)  # same phase, < 0.5s → throttled (bytes remembered)
    sink("pulling_image", 1024, 1024)  # throttled too
    job = pull_job_store().get(job_id)
    assert job is not None
    assert job.phase == "pulling_image" and job.bytes_total == 0  # throttled writes dropped

    sink("installing", 0, 0)  # phase change → written, with the REMEMBERED bytes
    job = pull_job_store().get(job_id)
    assert job is not None
    assert job.phase == "installing"
    assert job.bytes_current == 1024 and job.bytes_total == 1024

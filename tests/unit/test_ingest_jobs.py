"""The ingest job store (status transitions, logs, snapshot
isolation) and its DB-backed durability: jobs now survive a server restart and stale in-flight
jobs are reconciled on boot."""

from __future__ import annotations

import pytest

from xorcise.core.rest.ingest_jobs import IngestJobStore, reconcile_ingest_jobs

pytestmark = pytest.mark.unit


def test_start_creates_a_building_job(migrated_home) -> None:
    s = IngestJobStore()
    jid = s.start()
    job = s.get(jid)
    assert job is not None
    assert job.status == "building"
    assert job.logs == []
    assert job.slug is None and job.image is None and job.detail is None


def test_append_log_accumulates(migrated_home) -> None:
    s = IngestJobStore()
    jid = s.start()
    s.append_log(jid, "a")
    s.append_log(jid, "b")
    got = s.get(jid)
    assert got is not None and got.logs == ["a", "b"]


def test_finish_ok_marks_installed_with_result_and_log(migrated_home) -> None:
    s = IngestJobStore()
    jid = s.start()
    s.finish_ok(jid, slug="c1", image="xorcise/mission-c1:1")
    job = s.get(jid)
    assert job is not None
    assert job.status == "installed"
    assert job.slug == "c1" and job.image == "xorcise/mission-c1:1"
    assert any("installed" in line for line in job.logs)


def test_finish_error_marks_error_with_detail_and_log(migrated_home) -> None:
    s = IngestJobStore()
    jid = s.start()
    s.finish_error(jid, "boom")
    job = s.get(jid)
    assert job is not None
    assert job.status == "error"
    assert job.detail == "boom"
    assert any("boom" in line for line in job.logs)


def test_get_unknown_job_is_none(migrated_home) -> None:
    assert IngestJobStore().get("does-not-exist") is None


def test_get_returns_a_snapshot_not_the_live_log_list(migrated_home) -> None:
    """A reader must not observe a concurrently-mutating log list (background worker appends)."""
    s = IngestJobStore()
    jid = s.start()
    s.append_log(jid, "a")
    snap = s.get(jid)
    s.append_log(jid, "b")  # mutate after the snapshot
    assert snap is not None and snap.logs == ["a"]  # snapshot is frozen


def test_status_survives_a_new_store_instance(migrated_home) -> None:
    # the store is DB-backed, so a job written by one instance (before a restart)
    # is readable by a fresh instance (after the restart) — no longer lost with the process.
    jid = IngestJobStore().start()
    IngestJobStore().append_log(jid, "line-1")
    IngestJobStore().finish_ok(jid, slug="c1", image="img:1")
    job = IngestJobStore().get(jid)  # a brand-new instance, as after a restart
    assert job is not None
    assert job.status == "installed" and job.slug == "c1"
    assert "line-1" in job.logs


def test_reconcile_marks_stale_building_jobs_error(migrated_home) -> None:
    # a job still 'building' after a restart is orphaned — its worker thread died
    # with the process and will never finish. Boot reconcile marks it error so the CLI/GUI poll ends
    # cleanly instead of hanging forever. Terminal (installed/error) jobs are left untouched.
    s = IngestJobStore()
    building = s.start()
    done = s.start()
    s.finish_ok(done, slug="c1", image="img:1")

    assert reconcile_ingest_jobs() == 1  # only the building job was stale

    stale = s.get(building)
    assert stale is not None and stale.status == "error"
    assert stale.detail and "restart" in stale.detail
    finished = s.get(done)
    assert finished is not None and finished.status == "installed"  # untouched

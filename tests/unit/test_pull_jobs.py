"""Pull job store — DB-backed durability, the active-job dedup (server-side double-pull
guard), progress rows, and boot reconcile. Mirrors test_ingest_jobs.py for the shared
contract; the dedup + byte-progress surface is new."""

from __future__ import annotations

import pytest

from xorcise.core.rest.pull_jobs import PullJobStore, reconcile_pull_jobs

pytestmark = pytest.mark.unit


def test_start_creates_a_pulling_job(migrated_home) -> None:
    s = PullJobStore()
    jid, created = s.start("sqli-login")
    assert created is True
    job = s.get(jid)
    assert job is not None
    assert job.mission_id == "sqli-login"
    assert job.status == "pulling"
    assert job.phase == "resolving"
    assert job.bytes_current == 0 and job.bytes_total == 0
    assert job.started_at > 0
    assert job.image is None and job.detail is None


def test_start_dedups_on_an_active_job_for_the_same_mission(migrated_home) -> None:
    # The server-side double-pull guard: a second start while a pull is in flight JOINS the
    # existing job instead of racing a second docker pull + install.
    s = PullJobStore()
    first, created_first = s.start("sqli-login")
    second, created_second = s.start("sqli-login")
    assert created_first is True
    assert created_second is False
    assert second == first


def test_start_does_not_dedup_across_missions(migrated_home) -> None:
    s = PullJobStore()
    a, _ = s.start("sqli-login")
    b, created = s.start("idor")
    assert created is True and b != a


def test_start_after_terminal_job_creates_a_fresh_one(migrated_home) -> None:
    s = PullJobStore()
    done, _ = s.start("sqli-login")
    s.finish_ok(done, image="img:1")
    again, created = s.start("sqli-login")
    assert created is True and again != done

    failed, _ = s.start("idor")
    s.finish_error(failed, "boom")
    retry, created = s.start("idor")
    assert created is True and retry != failed


def test_set_progress_records_phase_and_bytes(migrated_home) -> None:
    s = PullJobStore()
    jid, _ = s.start("sqli-login")
    s.set_progress(jid, phase="pulling_image", bytes_current=512, bytes_total=1024)
    job = s.get(jid)
    assert job is not None
    assert job.phase == "pulling_image"
    assert job.bytes_current == 512 and job.bytes_total == 1024
    assert job.status == "pulling"  # progress never flips status


def test_finish_ok_marks_installed_with_image(migrated_home) -> None:
    s = PullJobStore()
    jid, _ = s.start("sqli-login")
    s.finish_ok(jid, image="xorcise/mission-sqli-login:1")
    job = s.get(jid)
    assert job is not None
    assert job.status == "installed"
    assert job.phase == "done"
    assert job.image == "xorcise/mission-sqli-login:1"


def test_finish_error_marks_error_with_detail(migrated_home) -> None:
    s = PullJobStore()
    jid, _ = s.start("sqli-login")
    s.finish_error(jid, "no registry")
    job = s.get(jid)
    assert job is not None
    assert job.status == "error" and job.detail == "no registry"


def test_get_unknown_job_is_none(migrated_home) -> None:
    assert PullJobStore().get("does-not-exist") is None


def test_active_for_finds_only_the_in_flight_job(migrated_home) -> None:
    s = PullJobStore()
    assert s.active_for("sqli-login") is None
    jid, _ = s.start("sqli-login")
    active = s.active_for("sqli-login")
    assert active is not None and active.job_id == jid
    s.finish_ok(jid, image="img:1")
    assert s.active_for("sqli-login") is None  # terminal jobs are not "active"


def test_status_survives_a_new_store_instance(migrated_home) -> None:
    # DB-backed like the ingest store: a job written by one instance (before a restart) is
    # readable by a fresh instance (after the restart).
    jid, _ = PullJobStore().start("sqli-login")
    PullJobStore().finish_ok(jid, image="img:1")
    job = PullJobStore().get(jid)
    assert job is not None and job.status == "installed" and job.image == "img:1"


def test_reconcile_marks_stale_pulling_jobs_error(migrated_home) -> None:
    # A job still 'pulling' after a restart is orphaned — its worker died with the process.
    # Boot reconcile marks it error so the GUI poll ends AND the dedup stops blocking re-pulls.
    s = PullJobStore()
    stale, _ = s.start("sqli-login")
    done, _ = s.start("idor")
    s.finish_ok(done, image="img:1")

    assert reconcile_pull_jobs() == 1  # only the in-flight job was stale

    orphan = s.get(stale)
    assert orphan is not None and orphan.status == "error"
    assert orphan.detail and "restart" in orphan.detail
    finished = s.get(done)
    assert finished is not None and finished.status == "installed"  # untouched
    # the mission is pullable again after the reconcile
    _, created = s.start("sqli-login")
    assert created is True


def test_request_cancel_flags_an_active_job_without_changing_status(migrated_home) -> None:
    # Cancel is a SEPARATE flag: status stays 'pulling' so the dedup guard still sees the job as
    # in-flight while its worker unwinds. The worker flips it to 'cancelled' via finish_cancelled.
    s = PullJobStore()
    jid, _ = s.start("sqli-login")
    assert s.is_cancel_requested(jid) is False
    job = s.request_cancel(jid)
    assert job is not None and job.cancel_requested is True and job.status == "pulling"
    assert s.is_cancel_requested(jid) is True
    # The job is still the active one for the mission (a second pull must not start atop it).
    active = s.active_for("sqli-login")
    assert active is not None and active.job_id == jid


def test_request_cancel_unknown_job_is_none(migrated_home) -> None:
    assert PullJobStore().request_cancel("does-not-exist") is None
    assert PullJobStore().is_cancel_requested("does-not-exist") is False


def test_request_cancel_on_terminal_job_is_a_noop(migrated_home) -> None:
    # A cancel that races the pull's completion is a harmless idempotent no-op — the terminal
    # status is returned unchanged and the flag is not set.
    s = PullJobStore()
    jid, _ = s.start("sqli-login")
    s.finish_ok(jid, image="img:1")
    job = s.request_cancel(jid)
    assert job is not None and job.status == "installed" and job.cancel_requested is False


def test_finish_cancelled_marks_cancelled(migrated_home) -> None:
    s = PullJobStore()
    jid, _ = s.start("sqli-login")
    s.request_cancel(jid)
    s.finish_cancelled(jid)
    job = s.get(jid)
    assert job is not None and job.status == "cancelled"
    # A cancelled job is terminal — not 'active', so the mission is pullable again.
    assert s.active_for("sqli-login") is None
    _, created = s.start("sqli-login")
    assert created is True


def test_pull_mission_aborts_when_cancel_requested_up_front(tmp_path) -> None:
    """should_cancel=True from the outset raises PullCancelled and installs nothing."""
    from xorcise.core.catalog import StubCatalogSource
    from xorcise.core.contracts.errors import PullCancelled
    from xorcise.core.missions import get_installed
    from xorcise.core.rest.mission_pull import PullDeps, pull_mission
    from xorcise.core.runner.docker import StubDockerDriver

    deps = PullDeps(
        source=StubCatalogSource(enabled=True), driver=StubDockerDriver(), install_root=tmp_path
    )
    with pytest.raises(PullCancelled):
        pull_mission("sqli-login", deps, should_cancel=lambda: True)
    assert get_installed("sqli-login", tmp_path) is None  # nothing written


def test_pull_mission_aborts_mid_download_and_installs_nothing(tmp_path) -> None:
    """Cancel checked per download event: the abort fires inside the image pull (before the
    bundle/install phases) and leaves the mission not-installed. Distinct from PullError."""
    from xorcise.core.catalog import StubCatalogSource
    from xorcise.core.contracts.errors import PullCancelled
    from xorcise.core.missions import get_installed
    from xorcise.core.rest.mission_pull import PullDeps, pull_mission
    from xorcise.core.runner.docker import StubDockerDriver

    phases: list[str] = []
    # Cancel only AFTER the first layer event, so we prove the abort happens mid-pull, not before.
    calls = {"n": 0}

    def should_cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    deps = PullDeps(
        source=StubCatalogSource(enabled=True), driver=StubDockerDriver(), install_root=tmp_path
    )
    with pytest.raises(PullCancelled):
        pull_mission(
            "sqli-login",
            deps,
            progress=lambda p, c, t: phases.append(p),
            should_cancel=should_cancel,
        )
    # The bundle/install phases never ran (aborted during pulling_image).
    assert "downloading_bundle" not in phases
    assert "installing" not in phases
    assert get_installed("sqli-login", tmp_path) is None


def test_pull_mission_cancel_at_bundle_install_boundary_installs_nothing(tmp_path) -> None:
    """A cancel that arrives only after the image download finishes trips the pre-install
    checkpoint: 'downloading_bundle' is reported but 'installing' never is, and nothing is
    installed (guards the bundle→install abort that the up-front/mid-download tests never reach)."""
    from xorcise.core.catalog import StubCatalogSource
    from xorcise.core.contracts.errors import PullCancelled
    from xorcise.core.missions import get_installed
    from xorcise.core.rest.mission_pull import PullDeps, pull_mission
    from xorcise.core.runner.docker import StubDockerDriver

    phases: list[str] = []
    deps = PullDeps(
        source=StubCatalogSource(enabled=True), driver=StubDockerDriver(), install_root=tmp_path
    )
    with pytest.raises(PullCancelled):
        pull_mission(
            "sqli-login",
            deps,
            progress=lambda p, c, t: phases.append(p),
            # Cancel only once the download phase is reported — the abort lands at the
            # downloading_bundle→install checkpoint, past the up-front and per-layer checks.
            should_cancel=lambda: "downloading_bundle" in phases,
        )
    assert "downloading_bundle" in phases
    assert "installing" not in phases
    assert get_installed("sqli-login", tmp_path) is None


def test_pull_mission_cancel_on_cached_image_installs_nothing(tmp_path) -> None:
    """When the image is already local no layer events fire, so only the phase-boundary
    checkpoints can honour a cancel — this proves they do, the pull is skipped, and nothing is
    installed (covers the image_exists=True branch with a cancel)."""
    from xorcise.core.catalog import StubCatalogSource
    from xorcise.core.contracts.errors import PullCancelled
    from xorcise.core.missions import get_installed
    from xorcise.core.rest.mission_pull import PullDeps, pull_mission
    from xorcise.core.runner.docker import StubDockerDriver

    class _CachedDriver(StubDockerDriver):
        def image_exists(self, image: str) -> bool:
            return True  # already in the local store → pull skipped, on_layer never fires

    driver = _CachedDriver()
    calls = {"n": 0}

    def should_cancel() -> bool:
        calls["n"] += 1
        return calls["n"] >= 2  # pass the up-front check, trip the next (pre-bundle) checkpoint

    deps = PullDeps(source=StubCatalogSource(enabled=True), driver=driver, install_root=tmp_path)
    with pytest.raises(PullCancelled):
        pull_mission("sqli-login", deps, should_cancel=should_cancel)
    assert driver.pulled == []  # the pull was skipped (image cached)
    assert get_installed("sqli-login", tmp_path) is None


def test_pull_mission_reports_phases_and_aggregated_bytes(tmp_path) -> None:
    """The pull spine reports phase transitions and sums docker layer events into totals
    (the StubDockerDriver emits two synthetic 'Downloading' events for one layer)."""
    from xorcise.core.catalog import StubCatalogSource
    from xorcise.core.rest.mission_pull import PullDeps, pull_mission
    from xorcise.core.runner.docker import StubDockerDriver

    events: list[tuple[str, int, int]] = []
    deps = PullDeps(
        source=StubCatalogSource(enabled=True),
        driver=StubDockerDriver(),
        install_root=tmp_path,
    )
    pull_mission("sqli-login", deps, progress=lambda p, c, t: events.append((p, c, t)))

    phases = [p for p, _, _ in events]
    assert phases[0] == "resolving"
    assert "pulling_image" in phases
    assert "downloading_bundle" in phases
    assert phases[-1] == "installing"
    # per-layer events aggregated into running byte totals
    assert ("pulling_image", 512, 1024) in events
    assert ("pulling_image", 1024, 1024) in events

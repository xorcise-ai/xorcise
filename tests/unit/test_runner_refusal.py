"""A server with no runner refuses the work instead of faking it (#168).

Only `all` and `runner` own the Docker and Headscale planes. Every other role was wired with
stubs that answer success and do nothing: `POST /runs` answered 201 for a run that sat at
`created` for good, a pull or an ingest recorded a mission as installed with no image, and a
release marked an environment released while its container, started under role all, kept
running. Now each builder that would hand out a runner adapter refuses with
RunnerUnavailableError, the REST app answers it with a 503, a release is recorded as failed, and
the background jobs that only make sense with a runner do not start. Forced stubs keep the stubs:
tests and `--stub` depend on them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from xorcise.core import runs
from xorcise.core.config import get_settings
from xorcise.core.rest.runner_wiring import RunnerUnavailableError, runner_available
from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.unit

NO_RUNNER = "this server has no runner to {action}: role 'control' does not run Docker or Headscale"


@pytest.fixture
def role(monkeypatch):
    def _set(name: str, *, stubs: bool) -> None:
        monkeypatch.setenv("XORCISE_ROLE", name)
        monkeypatch.setenv("XORCISE_USE_STUBS", "1" if stubs else "0")
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


def _client() -> TestClient:
    return TestClient(build_rest_app(), raise_server_exceptions=False)


REQUESTS = [
    ("/api/runs", {"agent": "a1", "mission": "c1"}, "launch runs"),
    ("/api/missions/c1/pull", None, "pull missions"),
    ("/api/missions/c1/update", None, "pull missions"),
    ("/api/missions/c1/pull-jobs", None, "pull missions"),
    ("/api/missions/ingest", {"bundle_dir": "."}, "build missions"),
]


@pytest.mark.parametrize(("route", "body", "action"), REQUESTS)
def test_work_that_needs_a_runner_answers_503_and_starts_nothing(
    route, body, action, role, monkeypatch, migrated_home
) -> None:
    jobs: list[str] = []
    monkeypatch.setattr(
        "xorcise.core.rest.ingest_jobs.IngestJobStore.start", lambda *a: jobs.append("ingest")
    )
    monkeypatch.setattr(
        "xorcise.core.rest.pull_jobs.PullJobStore.start", lambda *a: jobs.append("pull")
    )
    role("control", stubs=False)
    resp = _client().post(route, json=body)
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert NO_RUNNER.format(action=action) in detail
    assert "Use role 'all'" in detail
    assert runs.list_runs() == []
    assert jobs == [], "refused before a background job was started"


@pytest.mark.parametrize(("route", "body", "action"), REQUESTS)
def test_forced_stubs_keep_the_stubs_on_any_role(route, body, action, role, migrated_home) -> None:
    role("control", stubs=True)
    assert _client().post(route, json=body).status_code != 503


@pytest.mark.parametrize("name", ["all", "runner"])
def test_a_role_that_owns_the_planes_has_a_runner(name, role) -> None:
    role(name, stubs=False)
    assert runner_available(get_settings())


def _builders() -> list[tuple[str, Callable[[Any], object]]]:
    from xorcise.core.rest.ingest import build_bundle_builder
    from xorcise.core.rest.mission_pull import build_pull_deps
    from xorcise.core.rest.run_create import (
        build_run_create_deps,
        build_teardown_control,
        build_teardown_fence,
    )

    return [
        ("launch runs", build_run_create_deps),
        ("pull missions", build_pull_deps),
        ("build missions", build_bundle_builder),
        ("release a run's environment", build_teardown_control),
        ("release a run's environment", build_teardown_fence),
    ]


@pytest.mark.parametrize("index", range(5))
def test_each_builder_refuses_rather_than_hand_out_a_stub(index, role, migrated_home) -> None:
    """The choke point: every route and job that needs a runner gets it from one of these, so
    a remote runner later plugs in here and nowhere else."""
    action, build = _builders()[index]
    role("control", stubs=False)
    with pytest.raises(RunnerUnavailableError, match=NO_RUNNER.format(action=action)):
        build(get_settings())


# --- releasing an environment -------------------------------------------------------------------


def _finished_run(run_id: str = "rid1", *, cidr: str = "10.200.1.0/24") -> str:
    runs.reserve_run(run_id, "a1", "c", network_cidr=cidr, entry_cidrs=cidr)
    runs.finalize_run(run_id, budget_seconds=300, prompt="P")
    runs.mark_terminal(run_id, "done", datetime(2026, 6, 25, 12, 0, tzinfo=UTC))
    return run_id


def test_a_release_is_recorded_as_failed_once_never_as_done(role, migrated_home) -> None:
    """The stubs used to report the container and the tailnet nodes removed, so the run read
    `released` and nothing would ever try again, while both were still up."""
    from xorcise.core.rest.run_teardown import teardown_run

    role("control", stubs=False)
    rid = _finished_run()
    outcome = teardown_run(rid)
    assert not outcome.released
    assert outcome.error.count("has no runner") == 1, "one sentence, not one per half"
    state = runs.release_state(rid)
    assert state is not None
    assert state.released_at is None
    assert state.attempts == 1


def test_a_delete_that_needs_a_release_says_why_once_and_can_be_forced(role, migrated_home) -> None:
    role("control", stubs=False)
    rid = _finished_run()
    resp = _client().delete(f"/api/runs/{rid}")
    assert resp.status_code == 409
    body = resp.json()
    assert body["forceable"] is True
    assert body["reason"] == "unreleased"
    assert body["detail"].count("has no runner") == 1
    assert "back" not in body["detail"], "nothing is down, so nothing will come back"
    assert runs.get(rid) is not None
    assert _client().delete(f"/api/runs/{rid}?force=true").status_code == 204
    assert runs.get(rid) is None


def test_a_run_without_an_environment_is_still_deleted(role, migrated_home) -> None:
    role("control", stubs=False)
    _finished_run("static", cidr="")
    assert _client().delete("/api/runs/static").status_code == 204


# --- what runs at start-up ------------------------------------------------------------------------


def test_the_boot_convergence_is_skipped_and_says_so(role, monkeypatch, migrated_home) -> None:
    """Through the stubs every container reads as gone, so converging would close live runs and
    drop reservations whose environments are still up. A bare "done" would claim the runs were
    checked."""
    from xorcise.core.cli.commands.system import boot_reconcile_lines
    from xorcise.core.rest import reconcile

    role("control", stubs=False)
    runs.reserve_run("reserved", "a1", "c", network_cidr="10.200.2.0/24", entry_cidrs="")
    reconcile.reconcile_all_on_startup()
    status = reconcile.boot_reconcile_status()
    assert status.state == "done"
    assert status.error.startswith("runs not checked: this server has no runner")
    assert runs.get("reserved") is not None
    lines = boot_reconcile_lines(status.model_dump(mode="json"))
    assert any("runs not checked" in line for line in lines)


def _started(monkeypatch) -> list[str]:
    started: list[str] = []
    monkeypatch.setattr(
        "xorcise.core.rest.release_sweeper.ReleaseSweeper.start",
        lambda self: started.append("sweeper"),
    )
    monkeypatch.setattr(
        "xorcise.core.rest.run_readiness.ReadinessWatchdog.start",
        lambda self: started.append("readiness"),
    )
    monkeypatch.setenv("XORCISE_NESTED_CONTAINER_CHECK", "skip")
    get_settings.cache_clear()
    return started


def test_the_jobs_that_need_a_runner_do_not_start(role, monkeypatch, migrated_home) -> None:
    """The release sweeper would retry a release that can never succeed, forever; the readiness
    gate would close runs out as deploy_failed because the stub sees no container."""
    role("control", stubs=False)
    started = _started(monkeypatch)
    with TestClient(build_rest_app()):
        pass  # startup and shutdown hooks run
    assert "sweeper" not in started
    assert "readiness" not in started


def test_the_same_jobs_start_with_forced_stubs(role, monkeypatch, migrated_home) -> None:
    """The control for the test above: the spies do see both jobs start when they should."""
    role("control", stubs=True)
    started = _started(monkeypatch)
    with TestClient(build_rest_app()):
        pass
    assert sorted(started) == ["readiness", "sweeper"]

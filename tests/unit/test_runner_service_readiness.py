"""Runner status verdict: deploy failure + inner-service readiness.

`deploy()` returns as soon as the outer container starts — the mission compose + per-run router
come up asynchronously INSIDE it. Two failure modes were previously invisible, both reported as a
flat READY:
  * the outer container exited (its entrypoint runs `set -eu`, so a failed `compose up` kills it) —
    the run then sat in limbo with a live agent chasing a target that never existed;
  * the outer container is alive but the inner services have not come up yet.

status() now distinguishes them: FAILED (exited), PENDING (still starting), READY (all up).
Unknown/unreportable state degrades to READY so stub-mode and other drivers are unaffected.
"""

from __future__ import annotations

import pytest

from xorcise.core.contracts.control import DeployRequest, MissionRef, NetworkSpec, RunState
from xorcise.core.contracts.errors import NotFoundError
from xorcise.core.runner.docker import ContainerState, ServiceState, StubDockerDriver
from xorcise.core.runner.service import RunnerControlService


def _req(run_id: str = "run-1") -> DeployRequest:
    return DeployRequest(
        run_id=run_id,
        mission=MissionRef(mission_id="c", image="xorcise/mission-c:0"),
        network=NetworkSpec(tailnet="10.200.1.0/24", auth_key="k"),
    )


def test_status_is_failed_when_the_outer_container_exited_nonzero():
    # The incident: `compose up` collided on a stale subnet, `set -eu` killed the entrypoint, the
    # container went Exited(1) — and status still said READY, so nothing terminated the run.
    driver = StubDockerDriver()
    svc = RunnerControlService(driver)
    svc.deploy(_req())
    driver.container_states["run-1"] = ContainerState(status="exited", exit_code=1)
    result = svc.status("run-1")
    assert result.state == RunState.FAILED
    assert result.ready is False


def test_status_is_failed_when_the_outer_container_exited_zero():
    # The outer container is the run's lifecycle handle — it is meant to `wait` for the whole run.
    # ANY exit before teardown is abnormal, so a clean exit is a failure too, not a READY.
    driver = StubDockerDriver()
    svc = RunnerControlService(driver)
    svc.deploy(_req())
    driver.container_states["run-1"] = ContainerState(status="exited", exit_code=0)
    assert svc.status("run-1").state == RunState.FAILED


def test_status_failure_is_seen_across_a_restart_despite_the_deploy_cache():
    # A fresh service (empty _deployed) must read the failure from live Docker, and the in-memory
    # cache of the deploying process must not mask it either.
    driver = StubDockerDriver()
    RunnerControlService(driver).deploy(_req("run-x"))
    driver.container_states["run-x"] = ContainerState(status="exited", exit_code=1)
    assert RunnerControlService(driver).status("run-x").state == RunState.FAILED


def test_status_is_pending_while_an_inner_service_is_not_running_yet():
    # Outer alive, inner still starting → PENDING (not READY): the agent must not be handed an
    # objective against a target that has not come up.
    driver = StubDockerDriver()
    svc = RunnerControlService(driver)
    svc.deploy(_req())
    driver.service_states["run-1"] = (
        ServiceState(name="web", status="running"),
        ServiceState(name="db", status="created"),
    )
    result = svc.status("run-1")
    assert result.state == RunState.PENDING
    assert result.ready is False


def test_status_is_ready_once_every_inner_service_runs():
    driver = StubDockerDriver()
    svc = RunnerControlService(driver)
    svc.deploy(_req())
    driver.service_states["run-1"] = (
        ServiceState(name="web", status="running"),
        ServiceState(name="xorcise-router", status="running"),
    )
    result = svc.status("run-1")
    assert result.state == RunState.READY
    assert result.ready is True


def test_status_is_ready_when_inner_state_is_unreportable():
    # Graceful degradation: a driver that cannot enumerate inner services (stub mode, a non-Docker
    # driver) must not wedge every run at PENDING forever.
    driver = StubDockerDriver()
    svc = RunnerControlService(driver)
    svc.deploy(_req())
    assert driver.service_states == {}  # nothing reported
    assert svc.status("run-1").state == RunState.READY


def test_absent_container_still_raises_not_found():
    # Unchanged: absent ⇒ NotFound (reconcile reads it as gone), never a false FAILED.
    with pytest.raises(NotFoundError):
        RunnerControlService(StubDockerDriver()).status("ghost")

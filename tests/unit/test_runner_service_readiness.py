"""Runner status verdict: deploy failure + inner-service readiness.

`deploy()` returns as soon as the outer container starts — the mission compose + per-run router
come up asynchronously INSIDE it. Two failure modes were previously invisible, both reported as a
flat READY:
  * the outer container exited (its entrypoint runs `set -eu`, so a failed `compose up` kills it) —
    the run then sat in limbo with a live agent chasing a target that never existed;
  * the outer container is alive but the inner services have not come up yet.

status() now distinguishes them: FAILED (exited), PENDING (still starting), READY (all up).
A driver with NOTHING to report (stub / non-Docker: `()`) degrades to READY so stub mode is
unaffected — but an environment that was ASKED and could not answer (`None`: the inner daemon is
not running) is PENDING, because reading it as READY let a wedged daemon pass the readiness gate.
Each not-ready verdict carries a `detail` saying why, which the gate reports and records.
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


def test_status_is_pending_when_the_inner_daemon_cannot_be_asked():
    """#43 finding 3. `compose ps` failing ("Cannot connect to the Docker daemon") used to parse
    to () and read as READY — indistinguishable from "all services running". A wedged inner daemon
    then latched _ever_ready and squatted its subnet until the 30-minute budget watchdog, instead
    of being closed out at the readiness window."""
    driver = StubDockerDriver()
    svc = RunnerControlService(driver)
    svc.deploy(_req())
    driver.service_states["run-1"] = None  # asked, no answer
    result = svc.status("run-1")
    assert result.state == RunState.PENDING
    assert result.ready is False
    assert "inner Docker daemon is not answering" in result.detail


def test_pending_names_the_services_still_starting():
    driver = StubDockerDriver()
    svc = RunnerControlService(driver)
    svc.deploy(_req())
    driver.service_states["run-1"] = (
        ServiceState(name="web", status="running"),
        ServiceState(name="db", status="created"),
    )
    assert svc.status("run-1").detail == "waiting for mission services: db (created)"


def test_failed_carries_the_exit_code():
    driver = StubDockerDriver()
    svc = RunnerControlService(driver)
    svc.deploy(_req())
    driver.container_states["run-1"] = ContainerState(status="exited", exit_code=137)
    assert svc.status("run-1").detail == "the mission environment exited with exit code 137"
    driver.container_states["run-1"] = ContainerState(status="exited", exit_code=None)
    assert svc.status("run-1").detail == "the mission environment exited"


def test_environment_logs_come_from_the_driver_and_are_empty_without_one():
    driver = StubDockerDriver()
    svc = RunnerControlService(driver)
    svc.deploy(_req())
    assert svc.environment_logs("run-1") == ""  # the stub keeps no logs
    driver.logs["run-1"] = "compose up: service web exited (1)"
    assert svc.environment_logs("run-1") == "compose up: service web exited (1)"


def test_absent_container_still_raises_not_found():
    # Unchanged: absent ⇒ NotFound (reconcile reads it as gone), never a false FAILED.
    with pytest.raises(NotFoundError):
        RunnerControlService(StubDockerDriver()).status("ghost")

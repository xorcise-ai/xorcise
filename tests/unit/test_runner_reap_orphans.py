"""Reap orphan run environments: compose projects still holding a network for a dead run.

A leftover network keeps its /24 allocated. The allocator now reconciles against live Docker so it
can no longer hand that subnet out twice, but the leak itself must also be cleaned up — otherwise
the run pool bleeds a subnet per imperfect teardown until it is exhausted.

Safety is the whole story here: reaping the WRONG project would delete the Headscale control plane's
own network. Only projects whose name is a run id (a 32-char hex uuid4) are ever considered, and any
run the server still considers live is kept.
"""

from __future__ import annotations

from xorcise.core.runner.docker import StubDockerDriver
from xorcise.core.runner.service import RunnerControlService

LIVE = "a" * 32
ORPHAN = "b" * 32


def test_reaps_a_run_project_the_server_no_longer_knows_about():
    driver = StubDockerDriver()
    driver.compose_projects = {LIVE, ORPHAN}
    reaped = RunnerControlService(driver).reap_orphan_environments([LIVE])
    assert reaped == [ORPHAN]
    assert driver.removed_projects == [ORPHAN]


def test_never_reaps_a_project_that_is_not_a_run_id():
    # The control plane's own compose project lives on the same daemon. Removing its network would
    # take down Headscale for every run — so anything that is not run-id-shaped is untouchable.
    driver = StubDockerDriver()
    driver.compose_projects = {"xorcise-headscale", "my-dev-stack", ORPHAN}
    reaped = RunnerControlService(driver).reap_orphan_environments([])
    assert reaped == [ORPHAN]
    assert driver.removed_projects == [ORPHAN]


def test_keeps_every_live_run():
    driver = StubDockerDriver()
    driver.compose_projects = {LIVE, ORPHAN}
    assert RunnerControlService(driver).reap_orphan_environments([LIVE, ORPHAN]) == []
    assert driver.removed_projects == []


def test_is_idempotent_when_nothing_is_orphaned():
    driver = StubDockerDriver()
    driver.compose_projects = set()
    assert RunnerControlService(driver).reap_orphan_environments([LIVE]) == []

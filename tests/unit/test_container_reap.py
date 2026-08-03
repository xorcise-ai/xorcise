"""Per-run mission containers are reaped (no leak / no GC gap)."""

from __future__ import annotations

import pytest

from xorcise.core.config import get_settings
from xorcise.core.rest.reap import reap_managed_containers
from xorcise.core.runner.docker import ContainerSpec, StubDockerDriver

pytestmark = pytest.mark.unit


def test_stub_reap_managed_removes_running_and_returns_ids():
    d = StubDockerDriver()
    a = d.run(ContainerSpec(image="img", name="run-a"))
    b = d.run(ContainerSpec(image="img", name="run-b"))
    reaped = d.reap_managed()
    assert set(reaped) == {a.container_id, b.container_id}
    assert d.running == {}  # nothing left running


def test_reap_spine_reaps_all_managed_regardless_of_run_state():
    """A terminal run AND an abandoned (never-terminal) run are both reaped (the leak)."""
    d = StubDockerDriver()
    d.run(ContainerSpec(image="i", name="run-terminal"))
    d.run(ContainerSpec(image="i", name="run-abandoned"))  # agent crashed before /complete
    reaped = reap_managed_containers(get_settings(), driver=d)
    assert len(reaped) == 2
    assert d.running == {}


def test_reap_spine_is_noop_under_stubs_without_a_driver(migrated_home):
    """conftest forces XORCISE_USE_STUBS=1 → no real Docker plane → nothing to reap, no error."""
    assert reap_managed_containers(get_settings()) == []

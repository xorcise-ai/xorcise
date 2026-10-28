"""reap_managed and run teardown: what is reported removed is what was removed (#66).

The outer run container descends from docker:dind, whose image declares a /var/lib/docker
VOLUME. Each run therefore owns an anonymous volume holding its inner daemon's image store,
and only `remove(v=True)` takes it along. These tests fake the docker SDK client, so no daemon is
needed.
"""

from __future__ import annotations

import pytest

from xorcise.core.runner.docker import MANAGED_LABEL, RUN_ID_LABEL
from xorcise.core.runner.docker.driver import DockerSdkDriver

pytestmark = pytest.mark.unit


class _Container:
    def __init__(self, name: str, labels: dict[str, str], *, fail: Exception | None = None) -> None:
        self.name = name
        self.labels = labels
        self._fail = fail
        self.removed = False
        self.removed_volumes = False

    def remove(self, force: bool = False, v: bool = False) -> None:
        if self._fail is not None:
            raise self._fail
        self.removed = True
        self.removed_volumes = v


class _Network:
    def __init__(self, project: str, *, fail: Exception | None = None) -> None:
        self.project = project
        self.attrs: dict[str, object] = {"Containers": {}}
        self._fail = fail
        self.removed = False

    def reload(self) -> None:
        pass

    def remove(self) -> None:
        if self._fail is not None:
            raise self._fail
        self.removed = True


def _matches(labels: dict[str, str], label_filter: str) -> bool:
    key, _, value = label_filter.partition("=")
    return labels.get(key) == value


class _Containers:
    def __init__(self, owner: _Client) -> None:
        self._owner = owner

    def list(self, all: bool = False, filters: dict[str, str] | None = None) -> list[_Container]:
        label = (filters or {}).get("label", "")
        return [c for c in self._owner.items if not c.removed and _matches(c.labels, label)]

    def get(self, name: str) -> _Container:
        import docker.errors  # type: ignore[import-untyped]

        for c in self._owner.items:
            if c.name == name and not c.removed:
                return c
        raise docker.errors.NotFound(name)


class _Networks:
    def __init__(self, owner: _Client) -> None:
        self._owner = owner

    def list(self, filters: dict[str, str] | None = None) -> list[_Network]:
        project = (filters or {}).get("label", "").partition("=")[2]
        return [n for n in self._owner.networks_ if n.project == project and not n.removed]


class _Volumes:
    def list(self, filters: dict[str, str] | None = None) -> list[object]:
        return []


class _Client:
    def __init__(self, items: list[_Container], networks: list[_Network] | None = None) -> None:
        self.items = items
        self.networks_ = networks or []
        self.containers = _Containers(self)
        self.networks = _Networks(self)
        self.volumes = _Volumes()


def _run(name: str, **kw: Exception) -> _Container:
    return _Container(name, {MANAGED_LABEL: "true", RUN_ID_LABEL: name}, **kw)


def test_run_teardown_removes_the_inner_daemon_volume_with_the_container():
    run = _run("run-a")
    DockerSdkDriver(client=_Client([run])).stop_by_name("run-a")
    assert run.removed
    assert run.removed_volumes, "the run's anonymous /var/lib/docker volume must go with it"


def test_reap_removes_every_managed_container_by_id_with_its_volume():
    runs = [_run("run-a"), _run("run-b")]
    unscoped = _Container("throwaway", {MANAGED_LABEL: "true"})  # read_image_file's shape
    bystander = _Container("unrelated", {})
    reaped = DockerSdkDriver(client=_Client([*runs, unscoped, bystander])).reap_managed()
    assert sorted(reaped) == ["run-a", "run-b", "throwaway"]
    assert all(c.removed and c.removed_volumes for c in [*runs, unscoped])
    assert not bystander.removed


def test_reap_reports_only_what_it_removed_and_carries_on_past_a_failure(caplog):
    stuck = _run("run-stuck", fail=RuntimeError("removal already in progress"))
    fine = _run("run-fine")
    stuck_net = _Network("run-stuck")
    reaped = DockerSdkDriver(client=_Client([stuck, fine], [stuck_net])).reap_managed()
    assert reaped == ["run-fine"], "a container that could not be removed is not reported reaped"
    assert fine.removed
    # The operator is told once, in one line, and the stuck run is not swept again for nothing.
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "run-stuck" in warnings[0].getMessage() and warnings[0].exc_info is None
    assert not stuck_net.removed


def test_reap_counts_a_container_already_gone_as_reaped():
    import docker.errors

    raced = _run("run-raced", fail=docker.errors.NotFound("gone"))
    assert DockerSdkDriver(client=_Client([raced])).reap_managed() == ["run-raced"]


def test_one_run_whose_network_will_not_go_does_not_stop_the_others():
    a, b = _run("run-a"), _run("run-b")
    stuck = _Network("run-a", fail=RuntimeError("network has active endpoints"))
    other = _Network("run-b")
    reaped = DockerSdkDriver(client=_Client([a, b], [stuck, other])).reap_managed()
    assert sorted(reaped) == ["run-a", "run-b"]
    assert other.removed, "run-b's network is still swept after run-a's sweep failed"

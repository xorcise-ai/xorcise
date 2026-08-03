"""Teardown must fully release a run's environment — containers AND network — across the two ways
it leaks.

1. The outer container already died (it exits when its entrypoint fails): the old code returned
   early and removed NOTHING, leaking the compose network that holds the run's /24.
2. macOS host-daemon mode: the mission stack is composed against the HOST daemon as siblings that
   carry only the compose PROJECT label, not our run-id label. Removing containers by the run-id
   label misses them, so `network.remove()` 403s ("network ... has active endpoints") — the run
   `e397d231` failure — a repaired-Laravel `vp-api` kept the network alive for hours.

The fake models a real daemon: a network refuses removal while a live container is still
attached, so the tests reproduce the 403 rather than asserting call order.
"""

from __future__ import annotations

from typing import Any

import pytest

from xorcise.core.runner.docker.driver import DockerSdkDriver

pytestmark = pytest.mark.adapters


class _Container:
    def __init__(self, cid: str, labels: dict[str, str], networks: set[str]) -> None:
        self.id = cid
        self.name = cid
        self.labels = labels
        self.attached = set(networks)
        self.alive = True

    def remove(self, force: bool = False) -> None:
        self.alive = False
        self.attached.clear()


class _Network:
    def __init__(self, name: str, project: str, containers: list[_Container]) -> None:
        self.id = name
        self.name = name
        self._containers = containers  # shared daemon container list
        self.attrs: dict[str, Any] = {"Labels": {"com.docker.compose.project": project}}
        self.removed = False

    def _endpoints(self) -> list[_Container]:
        return [c for c in self._containers if c.alive and self.name in c.attached]

    def reload(self) -> None:  # docker-py refreshes .attrs via inspect
        self.attrs["Containers"] = {c.id: {"Name": c.name} for c in self._endpoints()}

    def remove(self) -> None:
        if self._endpoints():
            from docker.errors import APIError  # type: ignore[import-untyped]

            raise APIError(f"network {self.name} has active endpoints")
        self.removed = True


class _Networks:
    def __init__(self, nets: list[_Network]) -> None:
        self._nets = nets

    def list(self, filters: dict[str, str] | None = None, **_kw: Any) -> list[_Network]:
        if not filters or "label" not in filters:
            return list(self._nets)
        want = filters["label"]
        return [
            n
            for n in self._nets
            if n.attrs["Labels"]["com.docker.compose.project"]
            == want.partition("com.docker.compose.project=")[2]
        ]


class _Containers:
    def __init__(self, containers: list[_Container]) -> None:
        self._c = containers

    def list(self, all: bool = False, filters: dict[str, str] | None = None) -> list[_Container]:
        want = (filters or {}).get("label")
        out = []
        for c in self._c:
            if not c.alive:
                continue
            if want:
                key, _, val = want.partition("=")
                if c.labels.get(key) != val:
                    continue
            out.append(c)
        return out

    def get(self, cid: str) -> _Container:
        for c in self._c:
            if c.id == cid and c.alive:
                return c
        from docker.errors import NotFound

        raise NotFound(cid)


class _Volumes:
    def list(self, **_kw: Any) -> list[Any]:
        return []


class _Client:
    def __init__(self, containers: list[_Container], nets: list[_Network]) -> None:
        self.containers = _Containers(containers)
        self.networks = _Networks(nets)
        self.volumes = _Volumes()


def test_teardown_removes_an_unlabelled_mission_container_blocking_the_network() -> None:
    # THE e397d231 bug: a mission service composed against the host daemon carries only the
    # compose-project label, so the run-id-labelled sweep misses it and the network 403s. Teardown
    # must force-remove whatever is ATTACHED to the network, regardless of its labels.
    outer = _Container("run1", {"xorcise.run_id": "run1"}, {"run1_default"})
    vp_api = _Container("vp-api", {"com.docker.compose.project": "run1"}, {"run1_default"})
    net = _Network("run1_default", "run1", [outer, vp_api])
    driver = DockerSdkDriver(client=_Client([outer, vp_api], [net]))

    assert driver.stop_by_name("run1") is True
    assert net.removed is True  # no 403 — the network was actually released
    assert not vp_api.alive and not outer.alive  # the mission container went too


def test_teardown_removes_the_network_when_the_container_is_already_gone() -> None:
    # The outer container exited and was reaped; the empty network must still be released.
    net = _Network("run2_default", "run2", [])
    driver = DockerSdkDriver(client=_Client([], [net]))
    assert driver.stop_by_name("run2") is False  # nothing was there to stop...
    assert net.removed is True  # ...but its network is still released


def test_teardown_reports_true_when_the_outer_container_existed() -> None:
    outer = _Container("run3", {"xorcise.run_id": "run3"}, {"run3_default"})
    net = _Network("run3_default", "run3", [outer])
    driver = DockerSdkDriver(client=_Client([outer], [net]))
    assert driver.stop_by_name("run3") is True
    assert net.removed is True


def test_list_compose_projects_reports_projects_that_still_hold_networks() -> None:
    nets = [
        _Network("a_default", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", []),
        _Network("hs_default", "xorcise-headscale", []),
    ]
    driver = DockerSdkDriver(client=_Client([], nets))
    assert driver.list_compose_projects() == {
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "xorcise-headscale",
    }

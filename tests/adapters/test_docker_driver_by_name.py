"""DockerSdkDriver.stop_by_name + inspect_by_name against an injected fake client.

Daemon-free (the pattern): the driver takes an injected client, so we exercise the real
by-name code paths — including the docker.errors.NotFound handling and the best-effort image tag —
without a Docker daemon. The daemon-bound paths live in test_docker_driver_real.py."""

from __future__ import annotations

import pytest

from xorcise.core.runner.docker.driver import DockerSdkDriver

pytestmark = pytest.mark.adapters

# The runner extra (docker SDK) is not installed in the CI test lanes. These tests need no daemon
# (they inject a fake client) but do need docker.errors importable — the driver's by-name methods
# catch docker.errors.NotFound — so skip the whole module when the SDK is absent, rather than
# erroring collection (a collection error interrupts the entire pytest run, failing every lane).
NotFound = pytest.importorskip("docker.errors").NotFound


class _FakeImage:
    def __init__(self, tags: list[str]) -> None:
        self.tags = tags


class _FakeContainer:
    def __init__(self, cid: str, tags: list[str], run_id: str = "run-1") -> None:
        self.id = cid
        self.image = _FakeImage(tags)
        self.name = run_id
        self.labels = {"xorcise.run_id": run_id, "xorcise.managed": "true"}
        self.removed_force: bool | None = None

    def remove(self, force: bool = False) -> None:
        self.removed_force = force


class _FakeContainers:
    def __init__(self, present: dict[str, _FakeContainer]) -> None:
        self._present = present

    def get(self, name: str) -> _FakeContainer:
        if name not in self._present:
            raise NotFound(f"no such container: {name}")
        return self._present[name]

    def list(self, *, all: bool = False, filters=None) -> list[_FakeContainer]:
        run_id = (filters or {}).get("label", "=").rsplit("=", 1)[-1]
        return [c for c in self._present.values() if c.labels["xorcise.run_id"] == run_id]


class _FakeResources:
    def list(self, *, filters=None) -> list[object]:
        return []


class _FakeClient:
    def __init__(self, present: dict[str, _FakeContainer]) -> None:
        self.containers = _FakeContainers(present)
        self.networks = _FakeResources()
        self.volumes = _FakeResources()


def test_stop_by_name_removes_present_container_and_returns_true() -> None:
    c = _FakeContainer("cid-1", ["img:1"])
    driver = DockerSdkDriver(client=_FakeClient({"run-1": c}))
    assert driver.stop_by_name("run-1") is True
    assert c.removed_force is True  # force-removed


def test_stop_by_name_absent_returns_false() -> None:
    driver = DockerSdkDriver(client=_FakeClient({}))
    assert driver.stop_by_name("ghost") is False  # NotFound -> False, no raise


def test_inspect_by_name_returns_identity_with_image_tag() -> None:
    driver = DockerSdkDriver(client=_FakeClient({"run-1": _FakeContainer("cid-1", ["img:1"])}))
    handle = driver.inspect_by_name("run-1")
    assert handle is not None
    assert handle.container_id == "cid-1" and handle.image == "img:1"


def test_inspect_by_name_untagged_image_is_empty_string() -> None:
    driver = DockerSdkDriver(client=_FakeClient({"run-1": _FakeContainer("cid-1", [])}))
    handle = driver.inspect_by_name("run-1")
    assert handle is not None and handle.image == ""  # best-effort tag, never raises


def test_inspect_by_name_absent_returns_none() -> None:
    driver = DockerSdkDriver(client=_FakeClient({}))
    assert driver.inspect_by_name("ghost") is None

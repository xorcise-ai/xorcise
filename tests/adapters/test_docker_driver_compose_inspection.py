from __future__ import annotations

import io
import tarfile
from typing import Any

import pytest

from xorcise.core.runner.docker.driver import DockerSdkDriver

pytestmark = pytest.mark.adapters


def _archive(content: bytes) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        member = tarfile.TarInfo("docker-compose.yml")
        member.size = len(content)
        tar.addfile(member, io.BytesIO(content))
    return out.getvalue()


class _Image:
    id = "sha256:mission-image"


class _Images:
    def get(self, image: str) -> _Image:
        assert image == "registry.example/mission:1"
        return _Image()


class _InspectionContainer:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.removed = False

    def get_archive(self, path: str):
        assert path == "/mission/docker-compose.yml"
        return iter([_archive(self.content)]), {"size": len(self.content)}

    def remove(self, *, force: bool) -> None:
        assert force
        self.removed = True


class _Containers:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.created: list[tuple[str, dict[str, Any]]] = []
        self.last: _InspectionContainer | None = None

    def create(self, image: str, **kwargs: Any) -> _InspectionContainer:
        self.created.append((image, kwargs))
        self.last = _InspectionContainer(self.content)
        return self.last


class _Client:
    def __init__(self, content: bytes) -> None:
        self.images = _Images()
        self.containers = _Containers(content)


def test_inspects_stopped_image_cleans_up_and_caches_by_image_id() -> None:
    client = _Client(b"services:\n  web:\n    ports: ['5000:5000']\n  db:\n    expose: [5432]\n")
    driver = DockerSdkDriver(client=client)

    assert driver.published_port_services("registry.example/mission:1") == ("web",)
    assert driver.published_port_services("registry.example/mission:1") == ("web",)

    assert client.containers.created == [
        (
            "sha256:mission-image",
            {"entrypoint": ["/bin/true"], "platform": "linux/amd64"},
        )
    ]
    assert client.containers.last is not None and client.containers.last.removed


def test_invalid_compose_fails_closed_and_still_removes_container() -> None:
    client = _Client(b"not-services: true\n")

    with pytest.raises(RuntimeError, match="services mapping"):
        DockerSdkDriver(client=client).published_port_services("registry.example/mission:1")

    assert client.containers.last is not None and client.containers.last.removed

"""DockerSdkDriver's image-store API (list / remove / container usage), daemon-free.

The driver takes an injected client, so these assert what it reads from and hands to docker-py
without a daemon. Removal is never forced: a 409 conflict surfaces as ImageInUseError, and an
image that is already gone counts as removed."""

from __future__ import annotations

from typing import Any

import docker.errors  # type: ignore[import-untyped]
import pytest
import requests

from xorcise.core.contracts.errors import ImageInUseError
from xorcise.core.runner.docker import ImageRecord
from xorcise.core.runner.docker.driver import DockerSdkDriver


def _api_error(status: int, message: str) -> docker.errors.APIError:
    response = requests.Response()
    response.status_code = status
    return docker.errors.APIError(message, response=response, explanation=message)


class _Image:
    def __init__(self, image_id: str, attrs: dict[str, Any]) -> None:
        self.id = image_id
        self.attrs = attrs

    @property
    def tags(self) -> list[str]:  # docker-py filters the dangling placeholder the same way
        return [t for t in self.attrs.get("RepoTags") or [] if t != "<none>:<none>"]


class _Images:
    def __init__(self, images: list[_Image], remove_error: Exception | None = None) -> None:
        self._images = images
        self.remove_error = remove_error
        self.removed: list[tuple[str, bool]] = []

    def list(self) -> list[_Image]:
        return self._images

    def remove(self, image: str, force: bool = False) -> None:
        if self.remove_error is not None:
            raise self.remove_error
        self.removed.append((image, force))


class _Container:
    def __init__(self, image_id: str) -> None:
        self.attrs = {"Image": image_id}


class _Containers:
    def __init__(self, containers: list[_Container]) -> None:
        self._containers = containers
        self.list_kwargs: dict[str, Any] = {}

    def list(self, **kwargs: Any) -> list[_Container]:
        self.list_kwargs = kwargs
        return self._containers


class _FakeClient:
    def __init__(
        self,
        images: list[_Image] | None = None,
        containers: list[_Container] | None = None,
        remove_error: Exception | None = None,
    ) -> None:
        self.images = _Images(images or [], remove_error)
        self.containers = _Containers(containers or [])


def test_list_images_maps_id_tags_labels_size_and_digests() -> None:
    client = _FakeClient(
        images=[
            _Image(
                "sha256:aaa",
                {
                    "RepoTags": ["xorcise/mission-sqli:local"],
                    "RepoDigests": ["reg.example/xorcise/mis-sqli@sha256:ddd"],
                    "Config": {"Labels": {"ai.xorcise.base.version": "2"}},
                    "Size": 1_500_000_000,
                },
            ),
            # dangling, no labels, no size: every optional field defaults rather than crashing
            _Image("sha256:bbb", {"RepoTags": ["<none>:<none>"], "Config": {"Labels": None}}),
            # dangling after a re-pull moved its tag: the digest still names where it came from
            _Image(
                "sha256:ccc",
                {
                    "RepoTags": [],
                    "RepoDigests": ["reg.example/xorcise/mis-vault@sha256:eee"],
                    "Config": {"Labels": {"ai.xorcise.mission.id": "vault"}},
                    "Size": 7,
                },
            ),
        ]
    )
    assert DockerSdkDriver(client=client).list_images() == [
        ImageRecord(
            id="sha256:aaa",
            tags=("xorcise/mission-sqli:local",),
            labels={"ai.xorcise.base.version": "2"},
            size_bytes=1_500_000_000,
            digests=("reg.example/xorcise/mis-sqli@sha256:ddd",),
        ),
        ImageRecord(id="sha256:bbb", tags=(), labels={}, size_bytes=0, digests=()),
        ImageRecord(
            id="sha256:ccc",
            tags=(),
            labels={"ai.xorcise.mission.id": "vault"},
            size_bytes=7,
            digests=("reg.example/xorcise/mis-vault@sha256:eee",),
        ),
    ]


def test_remove_image_never_forces() -> None:
    client = _FakeClient()
    DockerSdkDriver(client=client).remove_image("xorcise/mission-sqli:local")
    assert client.images.removed == [("xorcise/mission-sqli:local", False)]


def test_remove_image_conflict_raises_image_in_use() -> None:
    client = _FakeClient(remove_error=_api_error(409, "image is being used by stopped container"))
    with pytest.raises(ImageInUseError, match="xorcise/mission-sqli:local"):
        DockerSdkDriver(client=client).remove_image("xorcise/mission-sqli:local")


def test_remove_image_already_gone_is_success() -> None:
    client = _FakeClient(remove_error=docker.errors.ImageNotFound("No such image"))
    DockerSdkDriver(client=client).remove_image("xorcise/mission-gone:1")  # must not raise


def test_remove_image_other_daemon_errors_propagate() -> None:
    client = _FakeClient(remove_error=_api_error(500, "driver failed"))
    with pytest.raises(docker.errors.APIError, match="driver failed"):
        DockerSdkDriver(client=client).remove_image("xorcise/mission-sqli:local")


def test_container_image_ids_cover_containers_in_every_state() -> None:
    client = _FakeClient(containers=[_Container("sha256:aaa"), _Container("sha256:ccc")])
    assert DockerSdkDriver(client=client).container_image_ids() == {"sha256:aaa", "sha256:ccc"}
    # a stopped container still pins its image (docker refuses to delete it), so include them
    assert client.containers.list_kwargs == {"all": True}

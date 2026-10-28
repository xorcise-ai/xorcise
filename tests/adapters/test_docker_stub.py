from __future__ import annotations

import pytest

from tests.adapters._contracts import DockerDriverContract
from xorcise.core.contracts.errors import ImageInUseError
from xorcise.core.runner.docker import DockerDriver, ImageRecord, StubDockerDriver


class TestStubDockerDriver(DockerDriverContract):
    @pytest.fixture
    def driver(self) -> DockerDriver:
        return StubDockerDriver()


def _seeded(*records: ImageRecord) -> StubDockerDriver:
    d = StubDockerDriver()
    for record in records:
        d.images[record.id] = record
    return d


_TWO_TAGS = ImageRecord(
    id="sha256:aaa",
    tags=("xorcise/mission-sqli:1", "xorcise/mission-sqli:2"),
    labels={},
    size_bytes=10,
)


def test_stub_lists_seeded_images_and_sees_their_tags() -> None:
    d = _seeded(_TWO_TAGS)
    assert d.list_images() == [_TWO_TAGS]
    assert d.image_exists("xorcise/mission-sqli:2") is True


def test_stub_untags_until_the_last_tag_removes_the_image() -> None:
    d = _seeded(_TWO_TAGS)
    d.remove_image("xorcise/mission-sqli:1")
    assert [r.tags for r in d.list_images()] == [("xorcise/mission-sqli:2",)]
    d.remove_image("xorcise/mission-sqli:2")
    assert d.list_images() == []
    assert d.image_exists("xorcise/mission-sqli:2") is False
    assert d.removed == ["xorcise/mission-sqli:1", "xorcise/mission-sqli:2"]


def test_stub_refuses_to_delete_an_image_a_container_uses() -> None:
    d = _seeded(_TWO_TAGS)
    d.container_images.add("sha256:aaa")
    d.remove_image("xorcise/mission-sqli:1")  # untagging a still-tagged image is allowed
    with pytest.raises(ImageInUseError):
        d.remove_image("xorcise/mission-sqli:2")
    assert [r.tags for r in d.list_images()] == [("xorcise/mission-sqli:2",)]
    assert d.container_image_ids() == {"sha256:aaa"}


def test_stub_removes_an_image_by_id_unless_a_container_uses_it() -> None:
    dangling = ImageRecord(id="sha256:bbb", tags=(), labels={}, size_bytes=5)
    d = _seeded(_TWO_TAGS, dangling)
    d.container_images.add("sha256:aaa")
    with pytest.raises(ImageInUseError):
        d.remove_image("sha256:aaa")
    d.remove_image("sha256:bbb")
    assert d.list_images() == [_TWO_TAGS]
    assert d.removed == ["sha256:bbb"]


def test_deploy_runs_present_image_without_pulling() -> None:
    """Locally-present/locally-built images run with no registry pull (no force-pull)."""
    from xorcise.core.contracts.control import DeployRequest, MissionRef, NetworkSpec
    from xorcise.core.runner.service import RunnerControlService

    d = StubDockerDriver()
    svc = RunnerControlService(d)
    svc.deploy(
        DeployRequest(
            run_id="r1",
            mission=MissionRef(mission_id="c", image="img:1"),
            network=NetworkSpec(tailnet="10.0.0.0/24", auth_key="k"),
        )
    )
    assert d.pulled == [] and any(h.image == "img:1" for h in d.running.values())

"""read_image_file: how the confinement pass gets a mission's network list.

It reads /mission/docker-compose.yml out of the fused image — the only authoritative source,
since the installed bundle carries the manifest but not the compose.
"""

import io
import tarfile

import pytest

from xorcise.core.contracts.errors import ImageNotInstalledError, NotFoundError
from xorcise.core.runner.docker import MANAGED_LABEL
from xorcise.core.runner.docker.driver import DockerSdkDriver

pytestmark = pytest.mark.unit


def _tar_bytes(name: str, body: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name)
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


class _Container:
    def __init__(self, blob: bytes | None) -> None:
        self._blob = blob
        self.removed = False
        self.removed_volumes = False

    def get_archive(self, path: str) -> tuple[list[bytes], dict[str, int]]:
        if self._blob is None:
            import docker.errors  # type: ignore[import-untyped]

            raise docker.errors.NotFound(f"Could not find the file {path}")
        return [self._blob], {"size": len(self._blob)}

    def remove(self, force: bool = False, v: bool = False) -> None:
        self.removed = True
        self.removed_volumes = v


class _Client:
    def __init__(self, container: object | None = None) -> None:
        self._container = container
        self.containers = self
        self.create_kwargs: dict[str, object] = {}

    def create(self, image: str, **kw: object) -> object:
        self.create_kwargs = kw
        if self._container is None:
            import docker.errors

            raise docker.errors.ImageNotFound(f"No such image: {image}")
        return self._container


def test_reads_the_file_and_always_removes_the_container():
    container = _Container(_tar_bytes("docker-compose.yml", b"services: {}\n"))
    drv = DockerSdkDriver(client=_Client(container))
    assert drv.read_image_file("img", "/mission/docker-compose.yml") == "services: {}\n"
    assert container.removed, "the throwaway container must never be left behind"


def test_the_throwaway_container_leaves_no_volume_and_is_reapable():
    """Every fused image descends from docker:dind, whose config declares a /var/lib/docker
    VOLUME — so `create` allocates an anonymous one. Without `v=True` that volume is orphaned on
    EVERY deploy, and it carries no compose-project label, so the per-run volume sweep never finds
    it. The managed label is the other half: a server killed between create and remove would
    otherwise leave a container `reap_managed` cannot see."""
    container = _Container(_tar_bytes("docker-compose.yml", b"services: {}\n"))
    client = _Client(container)
    DockerSdkDriver(client=client).read_image_file("img", "/mission/docker-compose.yml")
    assert container.removed_volumes, "the anonymous dind volume must be removed with it"
    assert client.create_kwargs["labels"] == {MANAGED_LABEL: "true"}


def test_a_missing_compose_path_raises_a_domain_error():
    """A mission may author `environment.compose_file: compose.yaml`. If the path we ask for is
    not in the image, docker raises NotFound from get_archive — which, untranslated, is a 500."""
    container = _Container(None)
    drv = DockerSdkDriver(client=_Client(container))
    with pytest.raises(NotFoundError, match="no /mission/compose.yaml"):
        drv.read_image_file("img", "/mission/compose.yaml")
    assert container.removed, "the throwaway container must go even when the read fails"


def test_absent_image_raises_the_domain_error_not_a_raw_docker_404():
    """Regression: this call runs BEFORE run()'s own presence guard, so an image pruned out of
    the local store surfaced as an unhandled docker.errors.ImageNotFound — a 500 — instead of the
    409 the REST layer already renders with a '(re)build it' remediation."""
    drv = DockerSdkDriver(client=_Client(None))
    with pytest.raises(ImageNotInstalledError, match="not in the local store"):
        drv.read_image_file("missing:tag", "/mission/docker-compose.yml")

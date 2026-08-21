import contextlib

import pytest

from xorcise.core.runner.docker import DockerDriver
from xorcise.core.runner.docker.driver import DockerSdkDriver


def test_real_driver_is_a_docker_driver():
    # The class structure is checkable without the docker SDK (import is lazy in __init__).
    assert issubclass(DockerSdkDriver, DockerDriver)


def _docker_available() -> bool:
    try:
        import docker  # type: ignore[import-untyped]

        docker.from_env().ping()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _docker_available(), reason="needs the docker SDK + a running daemon")
def test_image_exists_false_for_absent_image() -> None:
    """Exercise the real image_exists (images.get → ImageNotFound → False) — no registry/network.

    The full pull/run DockerDriverContract against the real driver needs a *pullable* image,
    which depends on a published registry (deferred); that contract coverage is the recorded
    follow-up. This asserts the local-store-first query the real driver actually relies on.
    """
    assert DockerSdkDriver().image_exists("xorcise/definitely-absent:0") is False


@pytest.mark.skipif(not _docker_available(), reason="needs the docker SDK + a running daemon")
def test_run_fails_loud_on_absent_local_image() -> None:
    """The fused image is local-only — run() must fail loud (no auto-pull of a missing
    image, which yields a cryptic 'pull access denied') so the caller knows to re-ingest."""
    from xorcise.core.contracts.errors import ImageNotInstalledError
    from xorcise.core.runner.docker import ContainerSpec

    with pytest.raises(ImageNotInstalledError, match="not in the local store"):
        DockerSdkDriver().run(ContainerSpec(image="xorcise/definitely-absent:0", name="x"))


@pytest.mark.skipif(not _docker_available(), reason="needs the docker SDK + a running daemon")
def test_reap_managed_removes_managed_containers_only() -> None:
    """reap_managed force-removes every xorcise.managed container — including an EXITED
    one (the 3-day-old-orphan case from the bug) — and leaves unmanaged containers untouched.

    Uses created-state containers (no run/privileged needed) so it stays light; gated on a daemon.
    """
    import docker

    from xorcise.core.runner.docker import MANAGED_LABEL, RUN_ID_LABEL

    cli = docker.from_env()
    img = "alpine:3.19"
    try:
        cli.images.get(img)
    except docker.errors.ImageNotFound:
        try:
            cli.images.pull(img)
        except Exception:
            pytest.skip(f"cannot obtain {img} (offline)")

    managed = {"xor191t-managed-a", "xor191t-managed-b"}
    # A managed container with NO run id — the read_image_file throwaway's shape. It is not
    # reachable by _remove_run_resources (project == run id), so it used to be REPORTED reaped yet
    # left behind; reap_managed now force-removes it directly.
    unscoped = "xor191t-managed-norun"
    bystander = "xor191t-bystander"
    everything = managed | {unscoped, bystander}
    for n in everything:  # idempotent: clear any leftovers from a prior failed run
        with contextlib.suppress(Exception):
            cli.containers.get(n).remove(force=True)
    for n in managed:
        cli.containers.create(img, name=n, labels={MANAGED_LABEL: "true", RUN_ID_LABEL: n})
    cli.containers.create(img, name=unscoped, labels={MANAGED_LABEL: "true"})  # no RUN_ID_LABEL
    cli.containers.create(img, name=bystander)  # unmanaged — must survive
    try:
        reaped = set(DockerSdkDriver().reap_managed())
        assert managed <= reaped
        assert unscoped in reaped
        remaining = {c.name for c in cli.containers.list(all=True)}
        assert not (managed & remaining)  # both run-scoped managed gone
        assert (
            unscoped not in remaining
        )  # the label-less managed container gone too, not just claimed
        assert bystander in remaining  # unmanaged untouched
    finally:
        with contextlib.suppress(Exception):
            cli.containers.get(bystander).remove(force=True)

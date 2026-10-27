import contextlib
from typing import Any

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


def _fail_on_a_live_runner(cli: Any, ours: set[str]) -> None:
    """Fail before a test calls `reap_managed`, if this daemon runs a deployed XORCISE.

    `reap_managed` is host-wide: it removes EVERY xorcise.managed container on the daemon, not
    only the ones a test made. Running this lane on a host whose runner holds live runs would
    tear those runs down mid-mission, so any run's managed container the test did not create
    fails the test before anything is removed.

    Only a container carrying a run id belongs to a run. One without — the nested-support probe,
    the image-file read — is a throwaway that holds nothing of any run's, and the probe is left
    behind whenever a server is stopped mid-probe, as the e2e lane stops its servers. Counting it
    failed every adapters run after an e2e run, for nothing reaping it could break."""
    from xorcise.core.runner.docker import MANAGED_LABEL, RUN_ID_LABEL

    live = sorted(
        c.name
        for c in cli.containers.list(all=True, filters={"label": f"{MANAGED_LABEL}=true"})
        if c.name not in ours and (c.labels or {}).get(RUN_ID_LABEL)
    )
    if live:
        pytest.fail(
            f"this Docker daemon holds {len(live)} XORCISE-managed container(s) this test did not "
            f"create ({', '.join(live[:5])}{', …' if len(live) > 5 else ''}). reap_managed is "
            "host-wide and would remove them, so run the adapters lane on a host with no deployed "
            "runner (or `xorcise down` first)",
            pytrace=False,
        )


def test_the_live_runner_guard_counts_runs_not_throwaways() -> None:
    """A managed container with no run id (a nested-support probe stranded by a server stopped
    mid-probe) does not fail the guard; one with a run id does (Guru's #212 review)."""
    from types import SimpleNamespace

    from xorcise.core.runner.docker import MANAGED_LABEL, RUN_ID_LABEL

    def daemon(*containers: SimpleNamespace) -> SimpleNamespace:
        return SimpleNamespace(containers=SimpleNamespace(list=lambda **_: list(containers)))

    probe = SimpleNamespace(name="eager_turing", labels={MANAGED_LABEL: "true"})
    run = SimpleNamespace(name="run-1", labels={MANAGED_LABEL: "true", RUN_ID_LABEL: "run-1"})
    _fail_on_a_live_runner(daemon(probe), set())  # a throwaway: no failure
    with pytest.raises(pytest.fail.Exception, match="run-1"):
        _fail_on_a_live_runner(daemon(probe, run), set())


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
    _fail_on_a_live_runner(cli, everything)
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


@pytest.mark.skipif(not _docker_available(), reason="needs the docker SDK + a running daemon")
def test_teardown_and_reap_take_the_anonymous_volume_with_the_container() -> None:
    """A run container's anonymous volume (the dind /var/lib/docker, holding the inner daemon's
    image store) goes with it on both teardown and reap, not left dangling per run (#66)."""
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

    names = ["xor66t-stopped", "xor66t-reaped"]
    for n in names:  # idempotent: clear any leftovers from a prior failed run
        with contextlib.suppress(Exception):
            cli.containers.get(n).remove(force=True, v=True)
    _fail_on_a_live_runner(cli, set(names))
    volumes = {}
    for n in names:
        # A sourceless volume mount is an anonymous volume, as the dind image's VOLUME makes.
        c = cli.containers.create(
            img,
            name=n,
            mounts=[docker.types.Mount(target="/var/lib/docker", source=None, type="volume")],
            labels={MANAGED_LABEL: "true", RUN_ID_LABEL: n},
        )
        c.reload()
        volumes[n] = next(m["Name"] for m in c.attrs["Mounts"] if m["Type"] == "volume")
    try:
        driver = DockerSdkDriver()
        assert driver.stop_by_name("xor66t-stopped") is True
        assert "xor66t-reaped" in driver.reap_managed()
        left = {v.name for v in cli.volumes.list()}
        assert not (set(volumes.values()) & left), "an anonymous run volume outlived its container"
    finally:
        for n in names:
            with contextlib.suppress(Exception):
                cli.containers.get(n).remove(force=True, v=True)
        for v in volumes.values():
            with contextlib.suppress(Exception):
                cli.volumes.get(v).remove(force=True)

"""DockerSdkDriver.pull wiring: split ref + auth_config, with an injected fake client.
Pull + run also carry an explicit platform (default linux/amd64) so amd64-only mission
images resolve on an arm64 host (Apple Silicon) instead of 404-ing on a missing arm64 manifest.

Daemon-free: the driver takes an injected client, so we assert exactly what it hands docker-py
without a real Docker daemon (the daemon-bound paths live in test_docker_driver_real.py).
The pull now uses the LOW-LEVEL streaming api.pull (the high-level images.pull discards
docker's per-layer progressDetail events) and forwards byte progress to a callback."""

from __future__ import annotations

from typing import Any

import pytest

from xorcise.core.runner.docker import ContainerSpec, PullProgress
from xorcise.core.runner.docker.driver import DockerSdkDriver

pytestmark = pytest.mark.adapters


class _FakeApi:
    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, str | None, dict[str, str] | None, str | None]] = []
        self.events = events or []

    def pull(
        self, repository, tag=None, auth_config=None, stream=False, decode=False, platform=None
    ):
        assert stream and decode  # streaming events are the whole point of the low-level API
        self.calls.append((repository, tag, auth_config, platform))
        yield from self.events


class _FakeImages:
    def get(self, image):  # image_exists → present
        return object()


class _FakeContainers:
    def __init__(self) -> None:
        self.run_kwargs: dict[str, Any] = {}

    def run(self, image, **kwargs):
        self.run_kwargs = {"image": image, **kwargs}
        return type("C", (), {"id": "cid-1"})()


class _FakeClient:
    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self.api = _FakeApi(events)
        self.images = _FakeImages()
        self.containers = _FakeContainers()


def test_pull_splits_ref_and_passes_auth_config_and_default_platform() -> None:
    client = _FakeClient()
    DockerSdkDriver(client=client).pull(
        "registry.example.com/xorcise/mission-sqli-login:100bdaf-base1",
        auth=("AWS", "secret"),
    )
    assert client.api.calls == [
        (
            "registry.example.com/xorcise/mission-sqli-login",
            "100bdaf-base1",
            {"username": "AWS", "password": "secret"},
            "linux/amd64",  # default platform so arm64 hosts pull the amd64 image
        )
    ]


def test_pull_anonymous_passes_no_auth_config() -> None:
    client = _FakeClient()
    DockerSdkDriver(client=client).pull("reg.example/xorcise/mission-x:base1")
    assert client.api.calls == [("reg.example/xorcise/mission-x", "base1", None, "linux/amd64")]


def test_pull_honors_custom_platform() -> None:
    client = _FakeClient()
    DockerSdkDriver(client=client, platform="linux/arm64").pull("reg.example/chal-x:base1")
    assert client.api.calls[0][3] == "linux/arm64"


def test_pull_empty_platform_passes_none_for_native_selection() -> None:
    # An empty platform restores docker's native host-platform selection (no override).
    client = _FakeClient()
    DockerSdkDriver(client=client, platform="").pull("reg.example/chal-x:base1")
    assert client.api.calls[0][3] is None


def test_pull_forwards_layer_progress_events() -> None:
    """Status-only events are forwarded with zeroed counts, not dropped.

    They used to be skipped for carrying no bytes, but they are the ONLY signal for the phases
    where no bytes move — "Pulling fs layer"/"Waiting" while docker negotiates, and "Already
    exists" for a layer already in the local store. Without them the caller cannot tell a healthy
    cached pull from a stalled one; measured on a live fully-cached pull, docker emitted three
    status-only events and no byte events whatsoever, and the UI showed "Downloading… 0 B".
    """
    client = _FakeClient(
        events=[
            {"status": "Pulling fs layer", "id": "l1", "progressDetail": {}},
            {"status": "Already exists", "id": "l0", "progressDetail": {}},
            {"status": "Downloading", "id": "l1", "progressDetail": {"current": 10, "total": 100}},
            {"status": "Downloading", "id": "l2", "progressDetail": {"current": 5, "total": 50}},
            {"status": "Extracting", "id": "l1", "progressDetail": {"current": 60, "total": 100}},
        ]
    )
    got: list[PullProgress] = []
    DockerSdkDriver(client=client).pull("reg.example/xorcise/mission-x:base1", progress=got.append)
    assert got == [
        PullProgress(layer_id="l1", status="Pulling fs layer", current=0, total=0),
        PullProgress(layer_id="l0", status="Already exists", current=0, total=0),
        PullProgress(layer_id="l1", status="Downloading", current=10, total=100),
        PullProgress(layer_id="l2", status="Downloading", current=5, total=50),
        PullProgress(layer_id="l1", status="Extracting", current=60, total=100),
    ]


def test_pull_error_event_raises() -> None:
    client = _FakeClient(events=[{"error": "manifest unknown"}])
    with pytest.raises(RuntimeError, match="manifest unknown"):
        DockerSdkDriver(client=client).pull("reg.example/xorcise/mission-x:base1")


def test_pull_without_callback_still_drains_the_stream() -> None:
    # progress=None (existing call sites): events are consumed, errors still surface.
    client = _FakeClient(
        events=[{"status": "Downloading", "id": "l1", "progressDetail": {"current": 1, "total": 2}}]
    )
    DockerSdkDriver(client=client).pull("reg.example/xorcise/mission-x:base1")
    assert client.api.calls  # the generator ran to completion without a callback


def test_run_carries_the_platform() -> None:
    client = _FakeClient()
    DockerSdkDriver(client=client).run(ContainerSpec(image="xorcise/fused:0", name="run-1"))
    assert client.containers.run_kwargs["platform"] == "linux/amd64"


def test_run_never_mounts_the_host_docker_socket() -> None:
    """The host-daemon (sibling) topology is gone, and NOT mounting the socket is what removes
    it: the fused entrypoint branches on that socket's presence. Mounting it would put every
    mission's containers back on the operator's own daemon, where parallel runs collide on the
    missions' fixed container_names and published host ports.

    Both halves are asserted because either alone would re-enable it — the mount gives the
    entrypoint its trigger, and DOCKER_HOST redirects compose even without one.
    """
    client = _FakeClient()
    DockerSdkDriver(client=client).run(ContainerSpec(image="xorcise/fused:0", name="run-1"))
    assert "volumes" not in client.containers.run_kwargs
    assert "DOCKER_HOST" not in client.containers.run_kwargs["environment"]


def test_run_is_platform_independent(monkeypatch) -> None:
    """There is no macOS special case left. The driver used to branch on the host OS; if that
    branch ever comes back it must not come back silently."""
    for system in ("Darwin", "Linux"):
        monkeypatch.setattr("platform.system", lambda s=system: s)
        client = _FakeClient()
        DockerSdkDriver(client=client).run(ContainerSpec(image="xorcise/fused:0", name="run-1"))
        assert "volumes" not in client.containers.run_kwargs
        assert "DOCKER_HOST" not in client.containers.run_kwargs["environment"]


def test_run_keeps_the_privileges_the_inner_daemon_needs() -> None:
    """The inner dockerd and the per-run Tailscale router are the reason for both of these; a
    DinD-only runtime cannot start without them."""
    client = _FakeClient()
    DockerSdkDriver(client=client).run(ContainerSpec(image="xorcise/fused:0", name="run-1"))
    assert client.containers.run_kwargs["privileged"] is True
    assert client.containers.run_kwargs["devices"] == ["/dev/net/tun:/dev/net/tun"]


def test_run_empty_platform_passes_none() -> None:
    client = _FakeClient()
    DockerSdkDriver(client=client, platform="").run(
        ContainerSpec(image="xorcise/fused:0", name="run-1")
    )
    assert client.containers.run_kwargs["platform"] is None

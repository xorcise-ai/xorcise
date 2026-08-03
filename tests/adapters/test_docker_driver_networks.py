"""DockerSdkDriver.list_network_cidrs — enumerate the subnets already carved on the Docker host.

Daemon-free: the driver takes an injected client, so we assert exactly what it reads from docker-py
without a real daemon. The allocator unions these into its in-use set so a LEFTOVER run network
(imperfect teardown) can never have its /24 re-handed to a new run — the collision that silently
kills a mission deploy and leaves the agent joined-but-target-dead.
"""

from __future__ import annotations

from typing import Any

import pytest

from xorcise.core.runner.docker.driver import DockerSdkDriver

pytestmark = pytest.mark.adapters


class _FakeNetwork:
    def __init__(self, attrs: dict[str, Any]) -> None:
        self.attrs = attrs


class _FakeNetworks:
    def __init__(self, nets: list[_FakeNetwork]) -> None:
        self._nets = nets

    def list(self, **_kwargs: Any) -> list[_FakeNetwork]:
        return self._nets


class _FakeClient:
    def __init__(self, nets: list[_FakeNetwork]) -> None:
        self.networks = _FakeNetworks(nets)


def _net(*subnets: str) -> _FakeNetwork:
    return _FakeNetwork({"IPAM": {"Config": [{"Subnet": s} for s in subnets]}})


def test_list_network_cidrs_collects_every_ipam_subnet() -> None:
    client = _FakeClient(
        [
            _net("10.200.1.0/24"),  # a leftover run network
            _net("10.200.7.0/24"),  # a live run network
            _net("172.17.0.0/16"),  # docker's default bridge
        ]
    )
    assert DockerSdkDriver(client=client).list_network_cidrs() == {
        "10.200.1.0/24",
        "10.200.7.0/24",
        "172.17.0.0/16",
    }


def test_list_network_cidrs_tolerates_networks_without_ipam_config() -> None:
    # host/none networks have no IPAM config; a malformed entry must not crash enumeration.
    client = _FakeClient(
        [
            _FakeNetwork({}),  # no IPAM key at all
            _FakeNetwork({"IPAM": {"Config": None}}),  # null config
            _FakeNetwork({"IPAM": {"Config": [{}]}}),  # entry without a Subnet
            _net("10.200.3.0/24"),
        ]
    )
    assert DockerSdkDriver(client=client).list_network_cidrs() == {"10.200.3.0/24"}

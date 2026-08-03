from __future__ import annotations

import pytest

from tests.adapters._contracts import DockerDriverContract
from xorcise.core.runner.docker import DockerDriver, StubDockerDriver


class TestStubDockerDriver(DockerDriverContract):
    @pytest.fixture
    def driver(self) -> DockerDriver:
        return StubDockerDriver()


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

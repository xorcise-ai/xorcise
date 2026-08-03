import dataclasses
from pathlib import Path

import pytest

from xorcise.core.catalog import StubCatalogSource
from xorcise.core.headscale import NetworkController, StubHeadscaleCli
from xorcise.core.orchestration.clients.control import InProcessControlStub
from xorcise.core.orchestration.clients.headscale_client import HeadscaleFenceClient
from xorcise.core.rest.mission_pull import PullDeps
from xorcise.core.rest.run_create import RunCreateDeps, _run_control_url
from xorcise.core.runner.docker import StubDockerDriver

pytestmark = pytest.mark.unit


def _deps() -> RunCreateDeps:
    fence = HeadscaleFenceClient(
        NetworkController(
            StubHeadscaleCli(), router_tag="tag:router", orchestrator_user="orchestrator"
        )
    )
    return RunCreateDeps(
        control=InProcessControlStub(api_key="k"),
        fence=fence,
        api_key="k",
        install_root=Path("/tmp"),
        login_server="https://headscale.local:8443",
        base_network="10.200.0.0/16",
        cidr_prefix=24,
        default_budget=600,
        pull=PullDeps(
            source=StubCatalogSource(enabled=True),
            driver=StubDockerDriver(),
            install_root=Path("/tmp"),
        ),
        advertise_host="host.docker.internal",
        rest_port=3001,
    )


def test_run_control_url_is_server_rest_under_api() -> None:
    url = _run_control_url(_deps(), "abc123")
    assert url == "http://host.docker.internal:3001/api/runs/abc123"


def test_run_control_url_empty_when_no_advertise_host() -> None:
    deps = dataclasses.replace(_deps(), advertise_host="")
    assert _run_control_url(deps, "abc123") == ""

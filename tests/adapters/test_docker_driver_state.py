"""DockerSdkDriver.container_state / .compose_service_states — the readiness primitives.

Daemon-free: the driver takes an injected client, so we assert exactly what it reads from docker-py
without a real daemon. `container_state` surfaces the OUTER lifecycle container's liveness + exit
code (a deploy that died); `compose_service_states` reads the INNER mission stack via a nested
`docker compose ps` exec, so the server can tell "still starting" from "up".
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from xorcise.core.runner.docker.driver import DockerSdkDriver

pytestmark = pytest.mark.adapters


class _FakeContainer:
    def __init__(self, status: str = "running", exit_code: int = 0, exec_out: bytes = b"") -> None:
        self.status = status
        self.attrs = {"State": {"ExitCode": exit_code}}
        self.exec_out = exec_out
        self.exec_cmds: list[Any] = []

    def exec_run(self, cmd, **_kwargs: Any):
        self.exec_cmds.append(cmd)
        return (0, self.exec_out)


class _FakeContainers:
    def __init__(self, container: _FakeContainer | None) -> None:
        self._container = container

    def get(self, _name: str) -> _FakeContainer:
        if self._container is None:
            from docker.errors import NotFound  # type: ignore[import-untyped]

            raise NotFound("absent")
        return self._container


class _FakeClient:
    def __init__(self, container: _FakeContainer | None) -> None:
        self.containers = _FakeContainers(container)


def test_container_state_reports_a_running_container() -> None:
    driver = DockerSdkDriver(client=_FakeClient(_FakeContainer(status="running")))
    state = driver.container_state("run-1")
    assert state is not None
    assert state.status == "running"
    assert state.exited is False


def test_container_state_reports_an_exited_container_with_its_code() -> None:
    driver = DockerSdkDriver(client=_FakeClient(_FakeContainer(status="exited", exit_code=1)))
    state = driver.container_state("run-1")
    assert state is not None
    assert state.exited is True
    assert state.exit_code == 1


def test_container_state_is_none_when_absent() -> None:
    assert DockerSdkDriver(client=_FakeClient(None)).container_state("ghost") is None


def test_compose_service_states_parses_ndjson_from_the_nested_exec() -> None:
    # Modern `docker compose ps --format json` emits one JSON object per line.
    out = (
        json.dumps({"Service": "web", "State": "running"})
        + "\n"
        + json.dumps({"Service": "db", "State": "created"})
        + "\n"
    ).encode()
    container = _FakeContainer(exec_out=out)
    states = DockerSdkDriver(client=_FakeClient(container)).compose_service_states("run-1")
    assert {(s.name, s.status) for s in states} == {("web", "running"), ("db", "created")}
    # the enumeration runs INSIDE the outer container, scoped to the run's compose project
    assert any("run-1" in " ".join(map(str, cmd)) for cmd in container.exec_cmds)


def test_compose_service_states_parses_a_json_array_form() -> None:
    # Some compose versions emit a single JSON array instead of NDJSON.
    out = json.dumps([{"Service": "web", "State": "running"}]).encode()
    driver = DockerSdkDriver(client=_FakeClient(_FakeContainer(exec_out=out)))
    states = driver.compose_service_states("run-1")
    assert [(s.name, s.status) for s in states] == [("web", "running")]


def test_compose_service_states_is_empty_on_unparseable_or_absent() -> None:
    # Unknown ⇒ empty ⇒ the caller degrades to READY rather than wedging the run at PENDING.
    noisy = DockerSdkDriver(client=_FakeClient(_FakeContainer(exec_out=b"not json at all")))
    assert noisy.compose_service_states("run-1") == ()
    assert DockerSdkDriver(client=_FakeClient(None)).compose_service_states("ghost") == ()

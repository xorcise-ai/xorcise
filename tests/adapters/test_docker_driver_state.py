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
    def __init__(
        self,
        status: str = "running",
        exit_code: int = 0,
        exec_out: bytes = b"",
        *,
        exec_code: int = 0,
        exec_raises: Exception | None = None,
        log_out: bytes = b"",
        logs_raise: bool = False,
    ) -> None:
        self.status = status
        self.attrs = {"State": {"ExitCode": exit_code}}
        self.exec_out = exec_out
        self.exec_code = exec_code
        self.exec_raises = exec_raises
        self.log_out = log_out
        self.logs_raise = logs_raise
        self.exec_cmds: list[Any] = []

    def exec_run(self, cmd, **_kwargs: Any):
        self.exec_cmds.append(cmd)
        if self.exec_raises is not None:
            raise self.exec_raises
        return (self.exec_code, self.exec_out)

    def logs(self, **_kwargs: Any) -> bytes:
        if self.logs_raise:
            raise RuntimeError("log read failed")
        return self.log_out


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
    assert states is not None
    assert {(s.name, s.status) for s in states} == {("web", "running"), ("db", "created")}
    # the enumeration runs INSIDE the outer container, scoped to the run's compose project
    assert any("run-1" in " ".join(map(str, cmd)) for cmd in container.exec_cmds)


def test_compose_service_states_parses_a_json_array_form() -> None:
    # Some compose versions emit a single JSON array instead of NDJSON.
    out = json.dumps([{"Service": "web", "State": "running"}]).encode()
    driver = DockerSdkDriver(client=_FakeClient(_FakeContainer(exec_out=out)))
    states = driver.compose_service_states("run-1")
    assert states is not None
    assert [(s.name, s.status) for s in states] == [("web", "running")]


def test_compose_service_states_is_empty_on_unparseable_output() -> None:
    # The daemon ANSWERED (exit 0) with something we cannot read ⇒ () ⇒ the caller degrades to
    # READY rather than wedging a healthy run at PENDING on a compose output-format change.
    noisy = DockerSdkDriver(client=_FakeClient(_FakeContainer(exec_out=b"not json at all")))
    assert noisy.compose_service_states("run-1") == ()


def test_compose_service_states_is_none_when_compose_ps_fails() -> None:
    """#43 finding 3. `compose ps` exiting non-zero — "Cannot connect to the Docker daemon" — is
    an inner daemon that is not running, not a service list. Parsing its stderr yielded (), which
    the runner read as READY, so a wedged daemon passed the gate and squatted its subnet until the
    30-minute budget watchdog fired. Unanswered is None: the runner reads it as not ready."""
    dead = _FakeContainer(
        exec_out=b"Cannot connect to the Docker daemon at unix:///var/run/docker.sock", exec_code=1
    )
    assert DockerSdkDriver(client=_FakeClient(dead)).compose_service_states("run-1") is None


def test_compose_service_states_is_none_when_the_exec_raises_or_the_container_is_gone() -> None:
    broken = _FakeContainer(exec_raises=RuntimeError("exec failed"))
    assert DockerSdkDriver(client=_FakeClient(broken)).compose_service_states("run-1") is None
    assert DockerSdkDriver(client=_FakeClient(None)).compose_service_states("ghost") is None


# ---------------------------------------------------------------- container_logs (evidence)


def test_container_logs_reads_the_outer_tail_and_the_inner_daemon_while_running() -> None:
    c = _FakeContainer(
        status="running",
        log_out=b"xorcise: waiting for dockerd\ncompose up: service web exited (1)\n",
        exec_out=b'time=... level=error msg="failed to start daemon"\n',
    )
    text = DockerSdkDriver(client=_FakeClient(c)).container_logs("run-1", tail=40)
    assert text is not None
    assert text.startswith("outer container, last 40 lines:\n")
    assert "service web exited (1)" in text
    assert "inner dockerd, last 30 lines:" in text and "failed to start daemon" in text
    assert c.exec_cmds == [["tail", "-n", "30", "/var/log/dockerd.log"]]


def test_container_logs_skips_the_inner_daemon_once_the_container_has_exited() -> None:
    # An exited container cannot be exec'd — and its entrypoint already echoed the daemon log
    # tail to stderr when it gave up waiting, so the outer logs carry that case.
    c = _FakeContainer(status="exited", exit_code=1, log_out=b"inner dockerd did not come up\n")
    text = DockerSdkDriver(client=_FakeClient(c)).container_logs("run-1")
    assert text is not None and "inner dockerd did not come up" in text
    assert "inner dockerd, last" not in text
    assert c.exec_cmds == []


def test_container_logs_is_none_when_the_container_is_gone_and_partial_on_a_read_failure() -> None:
    assert DockerSdkDriver(client=_FakeClient(None)).container_logs("ghost") is None
    unreadable = _FakeContainer(status="exited", logs_raise=True)
    text = DockerSdkDriver(client=_FakeClient(unreadable)).container_logs("run-1")
    assert text == "outer container: logs could not be read"

"""Reusable port/adapter contract suites.

Each adapter test module subclasses the relevant *PortContract and supplies the
adapter via the overridden fixture; the SAME assertions then run against every
adapter (stub now, real http/sqlite/BYOM later) unchanged. Modules here have no
`test_` prefix so pytest does not collect them directly.
"""

from __future__ import annotations

import pytest

from xorcise.core.contracts.control import DeployRequest, MissionRef, NetworkSpec, RunState
from xorcise.core.contracts.errors import AuthError
from xorcise.core.contracts.grading import GradeRequest
from xorcise.core.contracts.otlp import SpanEnvelope
from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.orchestration.ports import ControlPort, JudgePort
from xorcise.core.otel.ports import OtelIngest, TraceStore
from xorcise.core.runner.docker import ContainerSpec, DockerDriver

VALID_KEY = "test-key"


class ControlPortContract:
    """Behavioral contract every ControlPort adapter must satisfy."""

    @pytest.fixture
    def port(self) -> ControlPort:
        raise NotImplementedError("adapter test must override the `port` fixture")

    @staticmethod
    def _deploy_request(run_id: str = "run-1") -> DeployRequest:
        return DeployRequest(
            run_id=run_id,
            mission=MissionRef(mission_id="chal-1", image="ghcr.io/xorcise/mission-1:1"),
            network=NetworkSpec(tailnet="10.0.0.0/24", auth_key="k"),  # deploy needs a key
        )

    def test_deploy_returns_endpoints(self, port: ControlPort) -> None:
        endpoints = port.deploy(self._deploy_request(), credential=VALID_KEY)
        assert endpoints.run_id == "run-1"

    def test_deploy_is_idempotent(self, port: ControlPort) -> None:
        first = port.deploy(self._deploy_request(), credential=VALID_KEY)
        second = port.deploy(self._deploy_request(), credential=VALID_KEY)
        assert first == second  # replay returns the same result, no double-apply

    def test_missing_or_bad_key_is_rejected(self, port: ControlPort) -> None:
        with pytest.raises(AuthError):
            port.deploy(self._deploy_request(), credential="wrong")

    def test_teardown_then_status_is_torn_down(self, port: ControlPort) -> None:
        port.deploy(self._deploy_request(), credential=VALID_KEY)
        port.teardown("run-1", credential=VALID_KEY)
        assert port.status("run-1", credential=VALID_KEY).state == RunState.TORN_DOWN

    def test_teardown_is_idempotent(self, port: ControlPort) -> None:
        port.deploy(self._deploy_request(), credential=VALID_KEY)
        assert port.teardown("run-1", credential=VALID_KEY).ok
        assert port.teardown("run-1", credential=VALID_KEY).ok  # repeat stays ok


class JudgePortContract:
    """Behavioral contract every JudgePort adapter must satisfy."""

    @pytest.fixture
    def judge(self) -> JudgePort:
        raise NotImplementedError("adapter test must override the `judge` fixture")

    def test_grade_returns_5050(self, judge: JudgePort) -> None:
        result = judge.grade(GradeRequest(run_id="r1", trace_ref="r1"))
        assert result.run_id == "r1"
        assert result.breakdown.deterministic == 0.5
        assert result.breakdown.judge == 0.5


class OtelIngestContract:
    """Behavioral contract every OtelIngest adapter must satisfy."""

    @pytest.fixture
    def ingest(self) -> OtelIngest:
        raise NotImplementedError("adapter test must override the `ingest` fixture")

    def test_receive_acks_count(self, ingest: OtelIngest) -> None:
        ack = ingest.receive([SpanEnvelope(run_id="r1", span_id="s1", name="op")])
        assert ack.accepted == 1

    def test_stream_returns_received(self, ingest: OtelIngest) -> None:
        ingest.receive([SpanEnvelope(run_id="r1", span_id="s1", name="op")])
        assert [s.span_id for s in ingest.stream("r1")] == ["s1"]


class TraceStoreContract:
    """Behavioral contract every TraceStore adapter must satisfy."""

    @pytest.fixture
    def store(self) -> TraceStore:
        raise NotImplementedError("adapter test must override the `store` fixture")

    def test_append_then_read_by_run_id(self, store: TraceStore) -> None:
        store.append(TraceRecord(run_id="r1", seq=0, payload="a"))
        store.append(TraceRecord(run_id="r1", seq=1, payload="b"))
        assert [r.payload for r in store.read("r1")] == ["a", "b"]

    def test_read_unknown_run_is_empty(self, store: TraceStore) -> None:
        assert store.read("nope") == []


class DockerDriverContract:
    """Behavioral contract every DockerDriver adapter must satisfy."""

    @pytest.fixture
    def driver(self) -> DockerDriver:
        raise NotImplementedError("adapter test must override the `driver` fixture")

    def test_pull_then_run_returns_handle(self, driver: DockerDriver) -> None:
        driver.pull("ghcr.io/xorcise/mission-1:1")
        handle = driver.run(ContainerSpec(image="ghcr.io/xorcise/mission-1:1", name="c1"))
        assert handle.image == "ghcr.io/xorcise/mission-1:1"

    def test_stop_is_safe(self, driver: DockerDriver) -> None:
        handle = driver.run(ContainerSpec(image="img:1", name="c"))
        driver.stop(handle.container_id)  # must not raise

    def test_stop_by_name(self, driver: DockerDriver) -> None:
        # stop a container by its deterministic name (no in-memory handle needed),
        # so teardown works across a restart / from a second process. True iff one was removed.
        driver.run(ContainerSpec(image="img:1", name="by-name-1"))
        assert driver.stop_by_name("by-name-1") is True
        assert driver.stop_by_name("by-name-1") is False  # already gone — idempotent
        assert driver.stop_by_name("never-existed") is False

    def test_inspect_by_name(self, driver: DockerDriver) -> None:
        # report a container's identity by its deterministic name (no in-memory
        # handle), so deploy-state survives a restart / a second process. None when absent — this
        # is the live/durable fallback the runner and the reconcile loop query.
        assert driver.inspect_by_name("never-ran") is None
        handle = driver.run(ContainerSpec(image="img:1", name="inspect-1"))
        got = driver.inspect_by_name("inspect-1")
        assert got is not None and got.container_id == handle.container_id
        driver.stop_by_name("inspect-1")
        assert driver.inspect_by_name("inspect-1") is None

    def test_image_exists_reflects_pull(self, driver: DockerDriver) -> None:
        assert driver.image_exists("xorcise/definitely-absent:0") is False
        driver.pull("ghcr.io/xorcise/mission-1:1")
        assert driver.image_exists("ghcr.io/xorcise/mission-1:1") is True

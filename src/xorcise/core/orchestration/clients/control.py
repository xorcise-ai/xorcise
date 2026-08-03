"""In-process stub adapter for ControlPort.

Stub: canned endpoints, but REAL (verb, run_id) idempotency + API-key auth — no real
runner, no Docker. The future http adapter (Shape A) will reuse
the same ControlPortContract suite.
"""

from __future__ import annotations

from collections.abc import Sequence

from xorcise.core.contracts.control import (
    ApiKey,
    CollectTargetsResult,
    DeployRequest,
    RunId,
    RunnerEndpoints,
    RunState,
    StatusResult,
    TeardownResult,
)
from xorcise.core.contracts.errors import AuthError, NotFoundError
from xorcise.core.orchestration.ports import ControlPort
from xorcise.core.runner.service import RunnerControlService


class InProcessControlStub(ControlPort):
    def __init__(self, *, api_key: ApiKey) -> None:
        self._api_key = api_key
        self._deployed: dict[RunId, RunnerEndpoints] = {}
        self._torn_down: set[RunId] = set()

    def _check(self, credential: ApiKey) -> None:
        if credential != self._api_key:
            raise AuthError("invalid or missing API key")

    def deploy(self, request: DeployRequest, *, credential: ApiKey) -> RunnerEndpoints:
        self._check(credential)
        existing = self._deployed.get(request.run_id)
        if existing is not None:
            return existing  # idempotent replay — no double-apply
        endpoints = RunnerEndpoints(
            run_id=request.run_id,
            control_url=f"inprocess://control/{request.run_id}",
            otlp_url=f"inprocess://otlp/{request.run_id}",
        )
        self._deployed[request.run_id] = endpoints
        return endpoints

    def status(self, run_id: RunId, *, credential: ApiKey) -> StatusResult:
        self._check(credential)
        if run_id in self._torn_down:
            return StatusResult(run_id=run_id, state=RunState.TORN_DOWN)
        if run_id not in self._deployed:
            raise NotFoundError(run_id)
        return StatusResult(run_id=run_id, state=RunState.READY, ready=True)

    def collect_targets(self, run_id: RunId, *, credential: ApiKey) -> CollectTargetsResult:
        self._check(credential)
        if run_id not in self._deployed:
            raise NotFoundError(run_id)
        return CollectTargetsResult(run_id=run_id)

    def teardown(self, run_id: RunId, *, credential: ApiKey) -> TeardownResult:
        self._check(credential)
        self._deployed.pop(run_id, None)
        self._torn_down.add(run_id)  # idempotent — repeat teardown stays ok
        return TeardownResult(run_id=run_id, ok=True)


class RunnerControlAdapter(ControlPort):
    """In-process ControlPort bound to a real RunnerControlService (runner-side Docker).

    The server-side adapter owns the api-key auth; the runner service owns the Docker actions.
    Same ControlPortContract as the stub — Shape A→B stays additive.
    """

    def __init__(self, service: RunnerControlService, *, api_key: ApiKey) -> None:
        self._service = service
        self._api_key = api_key

    def _check(self, credential: ApiKey) -> None:
        if credential != self._api_key:
            raise AuthError("invalid or missing API key")

    def deploy(self, request: DeployRequest, *, credential: ApiKey) -> RunnerEndpoints:
        self._check(credential)
        return self._service.deploy(request)

    def status(self, run_id: RunId, *, credential: ApiKey) -> StatusResult:
        self._check(credential)
        return self._service.status(run_id)

    def collect_targets(self, run_id: RunId, *, credential: ApiKey) -> CollectTargetsResult:
        self._check(credential)
        return self._service.collect_targets(run_id)

    def teardown(self, run_id: RunId, *, credential: ApiKey) -> TeardownResult:
        self._check(credential)
        return self._service.teardown(run_id)

    def reap_orphan_environments(self, keep: Sequence[RunId], *, credential: ApiKey) -> list[RunId]:
        self._check(credential)
        return self._service.reap_orphan_environments(keep)

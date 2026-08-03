from __future__ import annotations

import pytest
from pydantic import ValidationError

from xorcise.core.contracts.control import (
    DeployRequest,
    MissionRef,
    RunnerEndpoints,
    RunState,
    StatusResult,
)


def test_deploy_request_builds_with_mission_ref() -> None:
    req = DeployRequest(
        run_id="run-1",
        mission=MissionRef(mission_id="chal-1", image="ghcr.io/xorcise/mission-1:1"),
    )
    assert req.run_id == "run-1"
    assert req.mission.image == "ghcr.io/xorcise/mission-1:1"


def test_dtos_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        MissionRef(mission_id="c", image="i", bogus="x")  # type: ignore[call-arg]


def test_dtos_are_frozen() -> None:
    endpoints = RunnerEndpoints(run_id="r", control_url="u", otlp_url="o")
    with pytest.raises(ValidationError):
        endpoints.run_id = "other"


def test_status_result_defaults() -> None:
    status = StatusResult(run_id="r", state=RunState.PENDING)
    assert status.ready is False
    assert status.targets == ()

"""POST /api/runs/{run_id}/terminate — operator-initiated termination (CLI↔GUI parity)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from xorcise.core import runs
from xorcise.core.roles.boot.role_all import build_rest_app


def _client() -> TestClient:
    return TestClient(build_rest_app())


@pytest.mark.unit
def test_terminate_active_run_stamps_operator(migrated_home) -> None:
    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    resp = _client().post(f"/api/runs/{r.run_id}/terminate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["terminal_trigger"] == "operator"
    assert body["completed_at"] is not None


@pytest.mark.unit
def test_terminate_records_result_via_background_task(migrated_home) -> None:
    """Endpoint seals synchronously + grades on a background task. Under the test client
    the task runs before the response returns, so the run is terminal AND the result is recorded."""
    from xorcise.core import reporting

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    resp = _client().post(f"/api/runs/{r.run_id}/terminate")
    assert resp.status_code == 200
    assert runs.terminal_state(r.run_id)[0] is True
    assert reporting.get_result(r.run_id) is not None  # graded via the background task


@pytest.mark.unit
def test_terminate_unknown_run_404(migrated_home) -> None:
    resp = _client().post("/api/runs/does-not-exist/terminate")
    assert resp.status_code == 404


@pytest.mark.unit
def test_terminate_already_terminal_409(migrated_home) -> None:
    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    client = _client()
    assert client.post(f"/api/runs/{r.run_id}/terminate").status_code == 200
    assert client.post(f"/api/runs/{r.run_id}/terminate").status_code == 409

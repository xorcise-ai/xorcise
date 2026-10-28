"""POST /api/runs/{run_id}/terminate — operator-initiated termination (CLI↔GUI parity)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from xorcise.core import runs
from xorcise.core.contracts.run import RunEntry
from xorcise.core.roles.boot.role_all import build_rest_app


def _client() -> TestClient:
    return TestClient(build_rest_app())


def _started_run() -> RunEntry:
    """A run whose agent has started (#209): only such a run is graded when stopped."""
    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    assert runs.mark_started(r.run_id, "telemetry", datetime.now(UTC))
    return r


@pytest.mark.unit
def test_terminate_xorcising_run_stamps_operator(migrated_home) -> None:
    r = _started_run()
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

    r = _started_run()
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


# --- #187: a run the agent never started is cancelled, never graded --------------------------


@pytest.mark.unit
def test_terminating_a_run_that_never_started_cancels_it_ungraded(migrated_home) -> None:
    """No 0.00 against the agent: the run ends `cancelled`, /result gives the final not-graded
    answer, and re-grading is refused (#187)."""
    from xorcise.core import reporting

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    client = _client()
    body = client.post(f"/api/runs/{r.run_id}/terminate").json()
    assert (body["state"], body["terminal_trigger"], body["outcome"]) == (
        "terminal",
        "cancelled",
        "not_started",
    )
    assert body["status"]["label"] == "Cancelled"
    assert body["started_at"] is None and body["start_trigger"] is None
    assert reporting.get_result(r.run_id) is None  # the background finaliser graded nothing

    result = client.get(f"/api/runs/{r.run_id}/result")
    assert result.status_code == 200
    assert (result.json()["status"], result.json()["terminal_trigger"]) == (
        "not_graded",
        "cancelled",
    )
    regrade = client.post(f"/api/runs/{r.run_id}/regrade")
    assert regrade.status_code == 409
    assert "before the agent started" in regrade.json()["detail"]


@pytest.mark.unit
def test_an_agent_signal_that_wins_the_race_makes_the_stop_an_operator_stop(migrated_home) -> None:
    """The agent's first signal and the stop race for the same row. Whichever lands first wins:
    a run that started is stopped as `operator` and graded, never cancelled (#187)."""
    from xorcise.core.rest.run_terminate import stop_run

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    now = datetime.now(UTC)
    assert runs.mark_started(r.run_id, "run_control", now)
    assert stop_run(r.run_id, now) == "operator"
    # ...and a signal after the stop changes nothing.
    other = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    assert stop_run(other.run_id, now) == "cancelled"
    assert runs.mark_started(other.run_id, "telemetry", now) is False
    # Nor can another ending overwrite it: mark_terminal is first-wins in one conditional UPDATE.
    assert runs.mark_terminal(other.run_id, "timeout", now) == "cancelled"
    entry = runs.get(other.run_id)
    assert entry is not None
    assert (entry.state, entry.terminal_trigger) == ("terminal", "cancelled")
    assert entry.started_at is None

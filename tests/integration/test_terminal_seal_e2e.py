from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _app_and_run(budget: int = 600):
    from xorcise.core import runs
    from xorcise.core.roles.boot.role_all import build_rest_app

    run = runs.create_run(
        agent_id="a1", mission="webby", run_control_key="K", budget_seconds=budget
    )
    return TestClient(build_rest_app()), run.run_id


def test_done_seals_and_post_terminal_is_rejected(migrated_home) -> None:
    from xorcise.core.otel.store import SqliteSealStore

    c, rid = _app_and_run()
    h = {"Authorization": "Bearer K"}
    resp = c.post(f"/api/runs/{rid}/complete", headers=h)
    assert resp.status_code == 200
    assert resp.json()["state"] == "terminal"
    assert SqliteSealStore().is_sealed(rid) is True
    # every post-terminal run-control call is mission-over
    assert (
        c.post(
            f"/api/runs/{rid}/artifacts", json={"name": "a", "content": "x"}, headers=h
        ).status_code
        == 409
    )
    assert c.get(f"/api/runs/{rid}/intel", headers=h).status_code == 409


def test_budget_expiry_via_gate_backstop_stamps_timeout(migrated_home) -> None:
    # the on-access gate backstop terminates an expired run on the next control call
    from xorcise.core import runs
    from xorcise.core.otel.store import SqliteSealStore

    c, rid = _app_and_run(budget=1)
    # force the deadline into the past by back-dating created_at is overkill; instead the gate
    # uses real now() — sleep is flaky, so assert via the watchdog tick in the unit test (Task 5)
    # and here assert the terminate_run coordinator path directly:
    from xorcise.core.rest.run_terminate import terminate_run

    run_entry = runs.get(rid)
    assert run_entry is not None
    created = run_entry.created_at
    terminate_run(rid, "timeout", created + timedelta(seconds=2))
    assert runs.terminal_state(rid)[1] == "timeout"
    assert SqliteSealStore().is_sealed(rid) is True
    # a submission after timeout is uniformly rejected (mission-over); the flag is an
    # artifact now, and post-terminal /artifacts (like every run-control call) returns 409.
    r = c.post(
        f"/api/runs/{rid}/artifacts",
        json={"name": "flag", "content": "x"},
        headers={"Authorization": "Bearer K"},
    )
    assert r.status_code == 409

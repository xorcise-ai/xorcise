import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.adapters


@pytest.fixture
def client(migrated_home):
    from xorcise.core import runs
    from xorcise.core.roles.boot.role_all import build_rest_app

    run = runs.create_run(agent_id="a1", mission="webby", run_control_key="K", budget_seconds=600)
    return TestClient(build_rest_app()), run.run_id


def _h(k: str = "K") -> dict[str, str]:
    return {"Authorization": f"Bearer {k}"}


def test_complete_makes_run_terminal_and_seals(client) -> None:
    from xorcise.core import runs
    from xorcise.core.otel.store import SqliteSealStore

    c, rid = client
    resp = c.post(f"/api/runs/{rid}/complete", headers=_h())
    assert resp.status_code == 200
    assert resp.json()["state"] == "terminal"
    assert runs.terminal_state(rid)[1] == "done"
    assert SqliteSealStore().is_sealed(rid) is True


def test_post_terminal_call_is_409_mission_over(client) -> None:
    c, rid = client
    c.post(f"/api/runs/{rid}/complete", headers=_h())
    r = c.post(f"/api/runs/{rid}/artifacts", json={"name": "a", "content": "x"}, headers=_h())
    assert r.status_code == 409 and "mission-over" in r.json()["detail"]


def test_get_mission_after_terminal_is_409(client) -> None:
    c, rid = client
    c.post(f"/api/runs/{rid}/complete", headers=_h())  # run is now terminal
    r = c.get(f"/api/runs/{rid}/mission", headers=_h())
    assert r.status_code == 409 and "mission-over" in r.json()["detail"]

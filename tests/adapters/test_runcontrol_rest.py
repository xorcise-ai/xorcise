import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.adapters

# Build an app with ONLY the runcontrol router + a seeded run, using the in-memory deps so
# the adapter is tested in isolation from boot wiring.


@pytest.fixture
def client(migrated_home):
    from xorcise.core import runs
    from xorcise.core.roles.boot.role_all import build_rest_app

    run = runs.create_run(agent_id="a1", mission="webby", run_control_key="key-1")
    app: FastAPI = build_rest_app()
    return TestClient(app), run.run_id


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _artifact(name: str = "flag", content: str = "x") -> dict[str, str]:
    return {"name": name, "content": content}


def test_missing_bearer_is_401(client) -> None:
    c, run_id = client
    assert c.post(f"/api/runs/{run_id}/artifacts", json=_artifact()).status_code == 401


def test_wrong_bearer_is_401(client) -> None:
    c, run_id = client
    r = c.post(f"/api/runs/{run_id}/artifacts", json=_artifact(), headers=_auth("WRONG"))
    assert r.status_code == 401


def test_submit_flag_as_artifact_records(client) -> None:
    # the flag is submitted as the artifact named "flag" via the one extensible endpoint.
    c, run_id = client
    r = c.post(
        f"/api/runs/{run_id}/artifacts",
        json=_artifact(name="flag", content="FLAG{x}"),
        headers=_auth("key-1"),
    )
    assert r.status_code == 200 and r.json()["accepted"] is True and r.json()["name"] == "flag"


def test_flag_endpoint_removed(client) -> None:
    # the single-purpose /flag endpoint no longer exists.
    c, run_id = client
    r = c.post(f"/api/runs/{run_id}/flag", json={"flag": "x"}, headers=_auth("key-1"))
    assert r.status_code == 404


def test_foreign_run_key_cannot_reach_another_run(client) -> None:
    from xorcise.core import runs

    c, run_id = client
    runs.create_run(agent_id="a1", mission="webby", run_control_key="key-2")
    # key-2 belongs to the other run, not run_id → rejected for run_id's route
    r = c.post(f"/api/runs/{run_id}/artifacts", json=_artifact(), headers=_auth("key-2"))
    assert r.status_code == 401

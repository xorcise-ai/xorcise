from fastapi.testclient import TestClient

from xorcise.core.roles.boot.role_all import build_rest_app


def client() -> TestClient:
    return TestClient(build_rest_app())


def test_health_ok():
    r = client().get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_agents_list_empty_by_default(migrated_home):
    r = client().get("/api/agents")
    assert r.status_code == 200
    assert r.json() == []


def test_runs_create_requires_registered_agent(migrated_home):
    # Real gate now lives here; happy path + tagging are in test_runs_rest.py
    r = client().post("/api/runs", json={"agent": "a1", "mission": "c1"})
    assert r.status_code == 409


def test_ui_serves_placeholder():
    # Passes with or without a built export: the real UI and the pre-build
    # placeholder both identify as XORCISE.
    r = client().get("/ui/")
    assert r.status_code == 200
    assert "XORCISE" in r.text


def test_ui_answers_before_first_frontend_build(monkeypatch, tmp_path):
    # Fresh clone: _static/ is VCS-ignored build output and absent — /ui must
    # still answer with the fix, not 404 like a broken install.
    from xorcise.core.rest import app as rest_app

    monkeypatch.setattr(rest_app, "_STATIC_DIR", tmp_path / "_static")
    r = client().get("/ui/")
    assert r.status_code == 200
    assert "XORCISE" in r.text
    assert "xorcise up" in r.text


def test_runs_complete_unknown_run_is_401(migrated_home):
    # /complete auth runs before the run lookup — a ghost run returns 401, not 404.
    # Happy-path recording is covered in test_runs_rest.py.
    r = client().post("/api/runs/run-001/complete", headers={"Authorization": "Bearer K"})
    assert r.status_code == 401

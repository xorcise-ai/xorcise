from __future__ import annotations

from fastapi.testclient import TestClient

from tests._helpers import install_mission
from xorcise.core.roles.boot.role_all import build_rest_app


def _client() -> TestClient:
    return TestClient(build_rest_app())


def _register_run_done(c: TestClient, name: str) -> dict[str, str]:
    c.post("/api/agents", json={"name": name})
    run: dict[str, str] = c.post("/api/runs", json={"agent": name, "mission": "c1"}).json()
    key = run["run_control_key"]
    c.post(f"/api/runs/{run['run_id']}/complete", headers={"Authorization": f"Bearer {key}"})
    return run


def test_history_lists_completed_runs(migrated_home):
    install_mission(migrated_home, "c1")
    c = _client()
    _register_run_done(c, "alpha")
    _register_run_done(c, "alpha")
    hist = c.get("/api/agents/alpha/history")
    assert hist.status_code == 200
    assert len(hist.json()) == 2
    assert all(h["agent_id"] for h in hist.json())


def test_history_entries_carry_conditions_with_versions(migrated_home):
    """Each history entry must expose conditions.agent_version + conditions.install_revision."""
    install_mission(migrated_home, "c1")
    c = _client()
    _register_run_done(c, "beta")
    hist = c.get("/api/agents/beta/history")
    assert hist.status_code == 200
    entry = hist.json()[0]
    assert "conditions" in entry
    cond = entry["conditions"]
    assert "agent_version" in cond
    assert "install_revision" in cond
    # versions are positive integers
    assert isinstance(cond["agent_version"], int) and cond["agent_version"] >= 1
    assert isinstance(cond["install_revision"], int) and cond["install_revision"] >= 1


def test_history_unknown_agent_404(migrated_home):
    assert _client().get("/api/agents/ghost/history").status_code == 404


def test_delete_agent_cascades_runs_and_results(migrated_home):
    install_mission(migrated_home, "c1")
    c = _client()
    _register_run_done(c, "alpha")
    assert c.delete("/api/agents/alpha").status_code == 204
    # agent gone, history endpoint 404s, and no runs/results linger
    assert c.get("/api/agents/alpha/history").status_code == 404
    assert c.get("/api/runs").json() == []

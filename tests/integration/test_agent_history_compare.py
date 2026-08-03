"""Integration: agent history — two-run cross-run compare + single-run renders cleanly.

No Docker required. Uses the REST TestClient directly (migrated_home fixture) to register an agent,
record two completed runs (via REST complete endpoint), then assert the history endpoint returns
entries labelled by agent_version + mission_version.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests._helpers import install_mission
from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.integration


def _client() -> TestClient:
    return TestClient(build_rest_app())


def _register_and_complete(c: TestClient, agent_name: str, mission_slug: str) -> dict[str, object]:
    """Register a run then immediately complete it, returning the run dict."""
    run_resp = c.post("/api/runs", json={"agent": agent_name, "mission": mission_slug})
    assert run_resp.status_code == 201, run_resp.text
    run: dict[str, object] = run_resp.json()
    key = run["run_control_key"]
    c.post(f"/api/runs/{run['run_id']}/complete", headers={"Authorization": f"Bearer {key}"})
    return run


def test_history_two_run_compare_carries_versions(migrated_home) -> None:
    """Two completed runs for the same agent each appear in history with version labels."""
    install_mission(migrated_home, "c1")
    c = _client()
    assert c.post("/api/agents", json={"name": "compare-agent"}).status_code == 201

    _register_and_complete(c, "compare-agent", "c1")
    # bump agent version so the second run records a different agent_version
    bump = c.put("/api/agents/compare-agent", json={"name": "compare-agent", "model": "m2"})
    assert bump.status_code == 200, bump.text
    _register_and_complete(c, "compare-agent", "c1")

    resp = c.get("/api/agents/compare-agent/history")
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 2

    # entries are oldest → newest
    first, second = entries[0], entries[1]

    # both entries must carry conditions with version fields
    for entry in entries:
        assert "conditions" in entry
        cond = entry["conditions"]
        assert "agent_version" in cond
        assert "mission_version" in cond

    # second run was recorded after a version bump → agent_version is higher
    assert second["conditions"]["agent_version"] > first["conditions"]["agent_version"]

    # each entry has the core score fields
    for entry in entries:
        assert "overall" in entry
        assert "deterministic" in entry
        assert "judge" in entry


def test_history_single_run_returns_one_entry_cleanly(migrated_home) -> None:
    """A single completed run returns a list of exactly one entry — no crash, no broken framing."""
    install_mission(migrated_home, "c1")
    c = _client()
    assert c.post("/api/agents", json={"name": "solo-agent"}).status_code == 201
    _register_and_complete(c, "solo-agent", "c1")

    resp = c.get("/api/agents/solo-agent/history")
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    entry = entries[0]
    assert "conditions" in entry
    assert entry["conditions"]["agent_version"] >= 1
    assert entry["conditions"]["mission_version"] >= 1

from __future__ import annotations

import subprocess
import sys

from fastapi.testclient import TestClient

from tests._helpers import install_mission
from xorcise.core.roles.boot.role_all import build_rest_app


def _client() -> TestClient:
    return TestClient(build_rest_app())


def test_register_then_list(migrated_home):
    c = _client()
    r = c.post("/api/agents", json={"name": "alpha", "endpoint": "http://a"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "alpha" and body["id"]
    listed = c.get("/api/agents").json()
    assert [a["name"] for a in listed] == ["alpha"]


def test_duplicate_name_is_409(migrated_home):
    c = _client()
    c.post("/api/agents", json={"name": "alpha"})
    r = c.post("/api/agents", json={"name": "alpha"})
    assert r.status_code == 409
    assert "already registered" in r.json()["detail"]


def test_delete_removes_and_404_when_absent(migrated_home):
    c = _client()
    c.post("/api/agents", json={"name": "alpha"})
    assert c.delete("/api/agents/alpha").status_code == 204
    assert c.get("/api/agents").json() == []
    assert c.delete("/api/agents/ghost").status_code == 404


def test_put_agent_bumps_version_same_id(migrated_home):
    c = _client()
    r1 = c.post("/api/agents", json={"name": "alpha", "endpoint": "http://a", "model": "m1"})
    assert r1.status_code == 201
    original_id = r1.json()["id"]
    r2 = c.put("/api/agents/alpha", json={"name": "alpha", "endpoint": "http://b", "model": "m2"})
    assert r2.status_code == 200
    body = r2.json()
    assert body["id"] == original_id  # same agent_id
    assert body["version"] == 2  # bumped
    assert body["endpoint"] == "http://b"
    assert body["model"] == "m2"


def test_register_and_update_round_trip_kind(migrated_home):
    c = _client()
    r1 = c.post("/api/agents", json={"name": "alpha", "endpoint": "http://a", "kind": "openhands"})
    assert r1.status_code == 201
    body1 = r1.json()
    assert body1["kind"] == "openhands"
    original_id = body1["id"]
    r2 = c.put(
        "/api/agents/alpha", json={"name": "alpha", "endpoint": "http://b", "kind": "claude-code"}
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["id"] == original_id  # same agent_id
    assert body2["version"] == 2  # bumped
    assert body2["kind"] == "claude-code"


def test_put_rename_changes_name_same_id_bumps_version(migrated_home):
    c = _client()
    r1 = c.post("/api/agents", json={"name": "alpha", "endpoint": "http://a"})
    assert r1.status_code == 201
    original_id = r1.json()["id"]
    r2 = c.put("/api/agents/alpha", json={"name": "beta", "endpoint": "http://b"})
    assert r2.status_code == 200
    body = r2.json()
    assert body["name"] == "beta"
    assert body["id"] == original_id  # same agent_id — rename, not re-register
    assert body["version"] == 2  # a rename is a re-declaration at a new version
    listed = c.get("/api/agents").json()
    assert [a["name"] for a in listed] == ["beta"]  # old name gone


def test_put_rename_to_taken_name_is_409(migrated_home):
    c = _client()
    c.post("/api/agents", json={"name": "alpha"})
    c.post("/api/agents", json={"name": "beta"})
    r = c.put("/api/agents/alpha", json={"name": "beta"})
    assert r.status_code == 409
    assert "already registered" in r.json()["detail"]


def test_put_old_name_404s_after_rename(migrated_home):
    c = _client()
    c.post("/api/agents", json={"name": "alpha"})
    assert c.put("/api/agents/alpha", json={"name": "beta"}).status_code == 200
    r = c.put("/api/agents/alpha", json={"name": "alpha"})
    assert r.status_code == 404


def test_history_survives_rename(migrated_home):
    install_mission(migrated_home, "c1")
    c = _client()
    c.post("/api/agents", json={"name": "alpha"})
    run = c.post("/api/runs", json={"agent": "alpha", "mission": "c1"}).json()
    key = run["run_control_key"]
    c.post(f"/api/runs/{run['run_id']}/complete", headers={"Authorization": f"Bearer {key}"})
    assert c.put("/api/agents/alpha", json={"name": "beta"}).status_code == 200
    # History (keyed by agent id) is reachable under the new name; the old name is gone.
    assert c.get("/api/agents/beta/history").status_code == 200
    assert c.get("/api/agents/alpha/history").status_code == 404
    # The run stays attached to the renamed agent's id.
    runs_listed = c.get("/api/runs").json()
    assert [r["run_id"] for r in runs_listed] == [run["run_id"]]


def test_put_absent_agent_is_404(migrated_home):
    c = _client()
    r = c.put("/api/agents/ghost", json={"name": "ghost"})
    assert r.status_code == 404


def test_post_duplicate_still_409_after_put(migrated_home):
    c = _client()
    c.post("/api/agents", json={"name": "alpha"})
    c.put("/api/agents/alpha", json={"name": "alpha", "endpoint": "http://x"})
    r = c.post("/api/agents", json={"name": "alpha"})
    assert r.status_code == 409


def test_delete_agent_cascades_its_runs(migrated_home):
    install_mission(migrated_home, "c1")
    c = _client()
    c.post("/api/agents", json={"name": "alpha"})
    run = c.post("/api/runs", json={"agent": "alpha", "mission": "c1"}).json()
    key = run["run_control_key"]
    c.post(f"/api/runs/{run['run_id']}/complete", headers={"Authorization": f"Bearer {key}"})
    assert c.delete("/api/agents/alpha").status_code == 204
    assert c.get("/api/runs").json() == []


# --- #141: an unrecognised kind is accepted, but the response discloses it ---


def test_register_and_update_warn_on_an_unrecognised_kind(migrated_home):
    c = _client()
    r1 = c.post("/api/agents", json={"name": "typo", "kind": "openhand"})
    assert r1.status_code == 201
    assert r1.json()["kind"] == "openhand"  # warned, not rejected: custom kinds stay legal
    [warning] = r1.json()["warnings"]
    assert "'openhand' is not a registered harness" in warning
    assert "did you mean 'openhands'?" in warning

    r2 = c.put("/api/agents/typo", json={"name": "typo", "kind": "my-agent-cli"})
    assert r2.status_code == 200
    assert r2.json()["kind"] == "my-agent-cli"
    [warning] = r2.json()["warnings"]
    assert "'my-agent-cli' is not a registered harness" in warning
    assert "did you mean" not in warning


def test_registered_and_blank_kinds_do_not_warn(migrated_home):
    c = _client()
    for name, kind in (("a", "openhands"), ("b", None), ("c", ""), ("d", "  "), ("e", "generic")):
        r = c.post("/api/agents", json={"name": name, "kind": kind})
        assert (r.status_code, r.json()["warnings"]) == (201, []), kind
    r = c.put("/api/agents/a", json={"name": "a", "kind": "codex"})
    assert (r.status_code, r.json()["warnings"]) == (200, [])
    # The read side is unchanged: warnings are a property of a write, not of the agent.
    assert "warnings" not in c.get("/api/agents").json()[0]


def test_first_request_in_a_fresh_process_knows_the_real_harnesses(migrated_home):
    # The adapter registry holds only `generic` until harness_adapters.load_adapters() runs, so
    # a check that trusted it as-is would flag every real kind when POST /agents is the first
    # request a server answers. A fresh interpreter is the only place that state still exists.
    code = (
        "from fastapi.testclient import TestClient\n"
        "from xorcise.core.otel.adapters import registry\n"
        "from xorcise.core.roles.boot.role_all import build_rest_app\n"
        "c = TestClient(build_rest_app())\n"
        "assert registry.registered_names() == {'generic'}, registry.registered_names()\n"
        "ok = c.post('/api/agents', json={'name': 'real', 'kind': 'openhands'}).json()\n"
        "bad = c.post('/api/agents', json={'name': 'typo', 'kind': 'openhand'}).json()\n"
        "print(len(ok['warnings']), len(bad['warnings']))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["0", "1"]


def test_the_server_and_its_clients_agree_on_which_kinds_are_registered(migrated_home):
    # The warning above checks the adapter registry (it decides the renderer); the CLI gate and
    # the register page check GET /harnesses. If an adapter ever lands without a launch provider
    # (or the reverse), the three surfaces would disagree about the same kind. Fail here instead.
    c = _client()
    listed = sorted(h["kind"] for h in c.get("/api/harnesses").json())
    adapters = sorted(p["adapter_name"] for p in c.get("/api/harnesses/capabilities").json())
    assert listed == adapters
    assert "openhands" in listed
    for kind in listed:
        r = c.post("/api/agents", json={"name": f"agent-{kind}", "kind": kind})
        assert (r.status_code, r.json()["warnings"]) == (201, []), kind

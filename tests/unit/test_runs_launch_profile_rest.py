# tests/unit/test_runs_launch_profile_rest.py
"""REST test for /runs/{id}/launch-profile sourcing command + tips from the LAUNCH provider
(decoupled from the telemetry provider) — Task 4 of the harness-adapters-colocation feature."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import EnvironmentSpec, MissionManifest, MissionMetadata
from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission
from xorcise.core.roles.boot.role_all import build_rest_app


def _client() -> TestClient:
    return TestClient(build_rest_app())


def _install_mission(home: Path, slug: str = "c1") -> None:
    root = home / "missions" / slug
    root.mkdir(parents=True)
    manifest = MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id=slug, name=slug, objective="Solve it.", type="lab"),
        environment=EnvironmentSpec(),
    )
    ref = MissionRef(mission_id=slug, image=f"xorcise/mission-{slug}:0")
    (root / INSTALLED_FILE).write_text(InstalledMission(slug, root, manifest, ref).to_record())


def test_launch_profile_returns_claude_command_and_tips(migrated_home: Path):
    # A run whose source_agent == "claude-code" (agent kind snapshotted at create) gets
    # its command + tips from the LAUNCH provider (harness_adapters.claude_code.launch), not the
    # telemetry provider.
    _install_mission(migrated_home, "c1")
    client = _client()
    client.post("/api/agents", json={"name": "cc", "kind": "claude-code"})
    run = client.post("/api/runs", json={"agent": "cc", "mission": "c1"}).json()
    resp = client.get(f"/api/runs/{run['run_id']}/launch-profile?launch_mode=host")
    assert resp.status_code == 200
    body = resp.json()
    assert body["command"].startswith("claude ") and " -p " in body["command"]
    assert any("permission-mode" in t for t in body["tips"])


def test_launch_profile_is_host_only_for_claude_code(migrated_home: Path):
    # Claude Code is host-only: the response advertises only "host" (so the GUI hides the toggle),
    # and a container request is clamped to the only supported mode rather than served a
    # container endpoint the host CLI can't reach.
    _install_mission(migrated_home, "c2")
    client = _client()
    client.post("/api/agents", json={"name": "cc2", "kind": "claude-code"})
    run = client.post("/api/runs", json={"agent": "cc2", "mission": "c2"}).json()
    resp = client.get(f"/api/runs/{run['run_id']}/launch-profile?launch_mode=container")
    assert resp.status_code == 200
    body = resp.json()
    assert body["launch_modes"] == ["host"]
    assert body["launch_mode"] == "host"  # container request clamped to the only supported mode


def test_launch_profile_applies_registered_model_to_provider_command(
    migrated_home: Path,
):
    _install_mission(migrated_home, "model-command")
    client = _client()
    client.post(
        "/api/agents",
        json={"name": "modeled", "kind": "claude-code", "model": "claude-sonnet-5"},
    )
    run = client.post("/api/runs", json={"agent": "modeled", "mission": "model-command"}).json()

    body = client.get(f"/api/runs/{run['run_id']}/launch-profile").json()
    assert "--model claude-sonnet-5" in body["command"]


def test_launch_profile_uses_registered_agent_command_and_tips_overrides(
    migrated_home: Path,
):
    _install_mission(migrated_home, "c3")
    client = _client()
    registered = client.post(
        "/api/agents",
        json={
            "name": "custom-cc",
            "kind": "claude-code",
            "launch_command_template": "wrapper --prompt {mission}",
            "launch_tips": ["Use the team wrapper."],
        },
    )
    assert registered.status_code == 201
    run = client.post("/api/runs", json={"agent": "custom-cc", "mission": "c3"}).json()

    body = client.get(f"/api/runs/{run['run_id']}/launch-profile").json()
    assert body["command"].startswith("wrapper --prompt ")
    assert body["tips"] == ["Use the team wrapper."]


def test_agent_container_launch_mode_controls_profile_and_prompt_addresses(
    migrated_home: Path,
):
    _install_mission(migrated_home, "container-agent")
    client = _client()
    registered = client.post(
        "/api/agents",
        json={
            "name": "containerized-cc",
            "kind": "claude-code",
            "launch_command_template": "docker run team-claude {mission}",
            "launch_mode": "container",
        },
    )
    assert registered.status_code == 201
    assert registered.json()["launch_mode"] == "container"
    run = client.post(
        "/api/runs", json={"agent": "containerized-cc", "mission": "container-agent"}
    ).json()

    # The saved agent context wins over a contradictory per-request query and collapses the
    # run-page mode picker to the one address context this command was authored for.
    profile = client.get(f"/api/runs/{run['run_id']}/launch-profile?launch_mode=host").json()
    assert profile["launch_mode"] == "container"
    assert profile["launch_modes"] == ["container"]
    assert "host.docker.internal" in profile["shell_block"]

    prompt = client.get(f"/api/runs/{run['run_id']}/prompt?launch_mode=host").json()["prompt"]
    assert "host.docker.internal" in prompt

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


# --- #172: the run-control bearer rides the launch block, not the prompt ----------------------


def test_the_launch_block_exports_the_bearer_and_its_command_carries_none(migrated_home: Path):
    # The command puts the prompt on the agent's argv (`claude -p '<prompt>'`), readable by any
    # local user, so the prompt names $XORCISE_RUN_BEARER and the block exports the token.
    _install_mission(migrated_home, "c172")
    client = _client()
    client.post("/api/agents", json={"name": "cc172", "kind": "claude-code"})
    run = client.post("/api/runs", json={"agent": "cc172", "mission": "c172"}).json()
    key = run["run_control_key"]
    body = client.get(f"/api/runs/{run['run_id']}/launch-profile?launch_mode=host").json()
    assert f"export XORCISE_RUN_BEARER={key}" in body["shell_block"].splitlines()
    assert key not in body["command"]  # the prompt on the command line never holds the token
    assert "$XORCISE_RUN_BEARER" in body["command"]
    # Only the block carries it: `env` is what `xorcise run launch-profile` prints as a dotenv
    # file an operator may save anywhere, so it stays the telemetry env.
    assert "XORCISE_RUN_BEARER" not in body["env"]
    # And the operator is told a command that starts the agent elsewhere must pass it through.
    assert any("docker run -e XORCISE_RUN_BEARER" in note for note in body["notes"])
    # The block exports the token before the command that needs it.
    block = body["shell_block"]
    assert block.endswith(body["command"])
    assert block.index("export XORCISE_RUN_BEARER=") < block.index(body["command"])


def test_the_prompt_handed_to_an_agent_inlines_the_bearer_even_beside_a_launch_block(
    migrated_home: Path,
):
    # /prompt is the "give this to your agent" hand-off (GUI copy, `xorcise run prompt`). An agent
    # started from it has no launch block, so nothing exports the variable: the token is inlined.
    # Only the launch command itself is built from the variable form.
    _install_mission(migrated_home, "c173")
    client = _client()
    client.post("/api/agents", json={"name": "cc173", "kind": "claude-code"})
    run = client.post("/api/runs", json={"agent": "cc173", "mission": "c173"}).json()
    prompt = client.get(f"/api/runs/{run['run_id']}/prompt?launch_mode=host").json()["prompt"]
    assert f"Authorization: Bearer {run['run_control_key']}" in prompt
    assert "$XORCISE_RUN_BEARER" not in prompt


def test_a_prompt_only_harness_is_served_the_bearer_inline(migrated_home: Path):
    # No launch command means no launch block, so nothing would set XORCISE_RUN_BEARER: the
    # prompt the operator pastes by hand has to carry the token itself.
    _install_mission(migrated_home, "c174")
    client = _client()
    client.post("/api/agents", json={"name": "gen174", "kind": "generic"})
    run = client.post("/api/runs", json={"agent": "gen174", "mission": "c174"}).json()
    key = run["run_control_key"]
    prompt = client.get(f"/api/runs/{run['run_id']}/prompt").json()["prompt"]
    assert f"Authorization: Bearer {key}" in prompt
    assert "$XORCISE_RUN_BEARER" not in prompt
    profile = client.get(f"/api/runs/{run['run_id']}/launch-profile").json()
    assert profile["command"] is None
    assert "XORCISE_RUN_BEARER" not in profile["shell_block"]
    assert "XORCISE_RUN_BEARER" not in profile["env"]


def test_a_registered_launch_command_gets_the_bearer_export_too(migrated_home: Path):
    # An operator's own template is a launch block like any other: the export is added, and the
    # prompt stays free of the token.
    _install_mission(migrated_home, "c175")
    client = _client()
    client.post(
        "/api/agents",
        json={"name": "wrap175", "kind": "generic", "launch_command_template": "wrap {mission}"},
    )
    run = client.post("/api/runs", json={"agent": "wrap175", "mission": "c175"}).json()
    key = run["run_control_key"]
    body = client.get(f"/api/runs/{run['run_id']}/launch-profile").json()
    assert f"export XORCISE_RUN_BEARER={key}" in body["shell_block"]
    assert key not in body["command"]
    assert "$XORCISE_RUN_BEARER" in body["command"]


def test_an_agent_that_cleared_its_launch_command_is_treated_as_prompt_only(migrated_home: Path):
    # An empty template clears the provider's command. Both sides must agree it is prompt-only:
    # no export (there is no command to run after it) and the token inlined in the prompt —
    # otherwise the agent is told to read a variable nothing sets.
    _install_mission(migrated_home, "c176")
    client = _client()
    client.post(
        "/api/agents",
        json={"name": "cc176", "kind": "claude-code", "launch_command_template": ""},
    )
    run = client.post("/api/runs", json={"agent": "cc176", "mission": "c176"}).json()
    key = run["run_control_key"]
    body = client.get(f"/api/runs/{run['run_id']}/launch-profile").json()
    assert body["command"] is None
    assert "XORCISE_RUN_BEARER" not in body["shell_block"]
    prompt = client.get(f"/api/runs/{run['run_id']}/prompt").json()["prompt"]
    assert f"Authorization: Bearer {key}" in prompt

# tests/unit/test_run_create_preamble.py
"""The per-harness mission preamble (Tasks 1-3) is baked into the PERSISTED prompt at run-create
(Task 5), not just available as a helper — a claude-code run's rendered prompt carries the
Claude launch provider's headless-sandbox note."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from xorcise.core.contracts.connect import MissionPrompt
from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import EnvironmentSpec, MissionManifest, MissionMetadata
from xorcise.core.harness_adapters import load_launch_providers
from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission
from xorcise.core.roles.boot.role_all import build_rest_app
from xorcise.core.runs.launch.base import LaunchContext
from xorcise.core.runs.launch.registry import select as select_launch
from xorcise.core.runs.prompt import render_prompt_text


def _mission() -> MissionPrompt:
    return MissionPrompt(
        run_id="r1",
        mission="c",
        objective="o",
        login_server="http://ls",
        join_key="jk",
        run_control_url="http://rc",
        run_control_key="rk",
    )


def test_claude_preamble_lands_in_rendered_prompt():
    # This mirrors what run_create does at render time: select the launch provider by the run's
    # source_agent and pass its preamble into render_prompt_text.
    load_launch_providers()
    provider, _ = select_launch("claude-code")
    preamble = provider.mission_preamble(LaunchContext("r1", "claude-code", "host"))
    text = render_prompt_text(_mission(), preamble=preamble)
    assert preamble and preamble[0] in text  # the provider's actual first preamble line lands


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


def test_claude_code_run_create_persists_prompt_with_preamble(migrated_home: Path):
    # INTEGRATION gate: prove the preamble actually lands in a created claude-code run's
    # PERSISTED prompt end-to-end (not just the render_prompt_text helper in isolation above).
    _install_mission(migrated_home, "c1")
    client = _client()
    client.post("/api/agents", json={"name": "cc", "kind": "claude-code"})
    run = client.post("/api/runs", json={"agent": "cc", "mission": "c1"}).json()
    resp = client.get(f"/api/runs/{run['run_id']}/prompt")
    assert resp.status_code == 200
    load_launch_providers()
    provider, _ = select_launch("claude-code")
    expected = provider.mission_preamble(LaunchContext(run["run_id"], "claude-code", "host"))[0]
    assert expected in resp.json()["prompt"]


def test_generic_run_create_persists_prompt_without_preamble(migrated_home: Path):
    # A source_agent with no registered launch provider falls back to the generic (empty)
    # preamble — the wiring must not force preamble text onto every run.
    _install_mission(migrated_home, "c2")
    client = _client()
    client.post("/api/agents", json={"name": "gen", "kind": "some-other-agent"})
    run = client.post("/api/runs", json={"agent": "gen", "mission": "c2"}).json()
    resp = client.get(f"/api/runs/{run['run_id']}/prompt")
    assert resp.status_code == 200
    load_launch_providers()
    provider, _ = select_launch("claude-code")
    claude_pre = provider.mission_preamble(LaunchContext(run["run_id"], "claude-code", "host"))[0]
    assert claude_pre not in resp.json()["prompt"]  # generic run gets no harness preamble


def test_agent_mission_preamble_replaces_provider_default(migrated_home: Path):
    _install_mission(migrated_home, "c3")
    client = _client()
    custom = "Use the organization's custom mission procedure."
    client.post(
        "/api/agents",
        json={
            "name": "custom-cc",
            "kind": "claude-code",
            "mission_preamble": [custom],
        },
    )
    run = client.post("/api/runs", json={"agent": "custom-cc", "mission": "c3"}).json()
    prompt = client.get(f"/api/runs/{run['run_id']}/prompt").json()["prompt"]

    load_launch_providers()
    provider, _ = select_launch("claude-code")
    provider_default = provider.mission_preamble(
        LaunchContext(run["run_id"], "claude-code", "host")
    )[0]
    assert custom in prompt
    assert provider_default not in prompt

from __future__ import annotations

from pathlib import Path
from typing import Any

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


def test_create_run_blocked_when_no_agents(migrated_home):
    r = _client().post("/api/runs", json={"agent": "alpha", "mission": "c1"})
    assert r.status_code == 409
    assert "register an agent first" in r.json()["detail"]


def test_create_run_blocked_for_unknown_agent(migrated_home):
    c = _client()
    c.post("/api/agents", json={"name": "alpha"})
    r = c.post("/api/runs", json={"agent": "ghost", "mission": "c1"})
    assert r.status_code == 409
    assert "no agent named 'ghost'" in r.json()["detail"]


def test_create_run_blocked_for_mission_not_installed_and_not_in_catalog(migrated_home):
    # catalog disabled (no XORCISE_CATALOG_URL) + not installed => nothing to auto-pull
    c = _client()
    c.post("/api/agents", json={"name": "alpha"})
    r = c.post("/api/runs", json={"agent": "alpha", "mission": "absent"})
    assert r.status_code == 409
    assert "not in the catalog" in r.json()["detail"]


def test_create_run_tags_registered_agent(migrated_home):
    _install_mission(migrated_home, "c1")
    c = _client()
    agent = c.post("/api/agents", json={"name": "alpha"}).json()
    r = c.post("/api/runs", json={"agent": "alpha", "mission": "c1", "budget_seconds": 600})
    assert r.status_code == 201
    body = r.json()
    assert body["agent_id"] == agent["id"]
    assert body["mission"] == "c1" and body["run_id"]
    assert body["budget_seconds"] == 600
    # run_control_key is returned on create (once, like an API token)
    assert body["run_control_key"]
    listed = c.get("/api/runs").json()
    assert [run["run_id"] for run in listed] == [body["run_id"]]
    # LEAK GUARD: the bearer must NEVER appear in list/get responses
    assert "run_control_key" not in listed[0]


def test_create_run_image_not_built_is_409_with_remediation(migrated_home, monkeypatch):
    # a missing local fused image → clean 409 + re-ingest remediation, not a raw 500.
    import xorcise.core.rest.routers.runs as runs_router
    from xorcise.core.contracts.errors import ImageNotInstalledError

    def _boom(**_k):
        raise ImageNotInstalledError("image 'xorcise/mission-c1:local' is not in the local store")

    monkeypatch.setattr(runs_router, "create_run_spine", _boom)
    c = _client()
    c.post("/api/agents", json={"name": "alpha"})
    r = c.post("/api/runs", json={"agent": "alpha", "mission": "c1"})
    assert r.status_code == 409
    assert "not in the local store" in r.json()["detail"]
    assert "mission ingest" in r.json()["detail"]  # actionable remediation


def test_create_run_infra_unready_is_503(migrated_home, monkeypatch):
    # the fail-loud RuntimeError (Docker/Headscale unreachable) → 503 JSON,
    # not a text/plain 500 that the CLI can't decode.
    import xorcise.core.rest.routers.runs as runs_router

    def _boom(*_a, **_k):
        raise RuntimeError(
            "Headscale control plane 'headscale' is not reachable — run 'xorcise up'"
        )

    monkeypatch.setattr(runs_router, "build_run_create_deps", _boom)
    c = _client()
    c.post("/api/agents", json={"name": "alpha"})
    r = c.post("/api/runs", json={"agent": "alpha", "mission": "c1"})
    assert r.status_code == 503
    assert "not reachable" in r.json()["detail"]


def test_run_prompt_returns_stored_prompt(migrated_home):
    _install_mission(migrated_home, "c1")
    c = _client()
    c.post("/api/agents", json={"name": "alpha"})
    run = c.post("/api/runs", json={"agent": "alpha", "mission": "c1"}).json()
    r = c.get(f"/api/runs/{run['run_id']}/prompt")
    assert r.status_code == 200
    assert r.json()["run_id"] == run["run_id"]
    assert "/join.sh" in r.json()["prompt"]  # the join recipe delegates to the served join script


def test_run_prompt_unknown_run_is_404(migrated_home):
    assert _client().get("/api/runs/ghost/prompt").status_code == 404


def test_run_prompt_host_mode_rewrites_run_control_to_localhost(migrated_home):
    # the run-control Base URL in the prompt must follow the launch-mode toggle the same
    # way the OTLP endpoint does. A GENERIC agent supports both modes; local topology bakes the
    # container host (host.docker.internal). ?launch_mode=host must move the run-control host to the
    # server's loopback and drop the container-only --add-host note — otherwise an operator on the
    # host pastes a prompt whose run-control URL it can't resolve (the reported bug).
    _install_mission(migrated_home, "c1")
    c = _client()
    c.post("/api/agents", json={"name": "gen"})  # no kind → generic → both launch modes
    run = c.post("/api/runs", json={"agent": "gen", "mission": "c1"}).json()
    rid = run["run_id"]
    container = c.get(f"/api/runs/{rid}/prompt?launch_mode=container").json()["prompt"]
    assert "http://host.docker.internal:" in container
    assert f"/api/runs/{rid}" in container
    assert "--add-host host.docker.internal:host-gateway" in container
    host = c.get(f"/api/runs/{rid}/prompt?launch_mode=host").json()["prompt"]
    assert "host.docker.internal" not in host  # base URL host + the --add-host note both gone
    assert "--add-host" not in host
    assert f"/api/runs/{rid}" in host  # the run-control path itself is preserved


def test_run_prompt_host_only_harness_never_bakes_container_host(migrated_home):
    # A host-only harness (Claude Code) bakes the loopback run-control host; neither the default nor
    # an explicit container request may reintroduce host.docker.internal (the GUI hides the toggle,
    # and a container request is clamped to the only supported mode).
    _install_mission(migrated_home, "c1")
    c = _client()
    c.post("/api/agents", json={"name": "cc", "kind": "claude-code"})
    run = c.post("/api/runs", json={"agent": "cc", "mission": "c1"}).json()
    rid = run["run_id"]
    for mode in ("host", "container"):
        prompt = c.get(f"/api/runs/{rid}/prompt?launch_mode={mode}").json()["prompt"]
        assert "host.docker.internal" not in prompt
        assert "--add-host" not in prompt


def test_run_launch_profile_returns_otel_env(migrated_home):
    # durable fix: the harness-facing LaunchProfile (OTel env) is served here, out of
    # the agent prompt. Default topology is "local" → collector at host.docker.internal:4318.
    _install_mission(migrated_home, "c1")
    c = _client()
    c.post("/api/agents", json={"name": "alpha"})
    run = c.post("/api/runs", json={"agent": "alpha", "mission": "c1"}).json()
    r = c.get(f"/api/runs/{run['run_id']}/launch-profile")
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == run["run_id"]
    env = body["env"]
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://host.docker.internal:4318"
    assert env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
    assert env["OTEL_TRACES_EXPORTER"] == "otlp"


def test_run_launch_profile_claude_code_binds_run_correlation(migrated_home):
    # a claude-code run gets its harness-specific OTel env INCLUDING the run-correlation
    # resource attr, so every OTLP batch routes to this run (not just the prompt-echo batch).
    _install_mission(migrated_home, "c1")
    c = _client()
    c.post("/api/agents", json={"name": "cc", "kind": "claude-code"})
    run = c.post("/api/runs", json={"agent": "cc", "mission": "c1"}).json()
    body = c.get(f"/api/runs/{run['run_id']}/launch-profile").json()
    env = body["env"]
    assert env["OTEL_RESOURCE_ATTRIBUTES"] == f"xorcise.run_id={run['run_id']}"
    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    assert body["correlation"] == "resource-attr"
    assert body["fallback"] is False
    assert body["notes"]  # operator hint about not clobbering an existing OTEL_RESOURCE_ATTRIBUTES


def test_run_launch_profile_host_mode_rewrites_endpoint_to_localhost(migrated_home):
    # a HOST-launched harness can't resolve the container-only host.docker.internal —
    # ?launch_mode=host rewrites the OTLP endpoint to localhost so the exporter actually reaches
    # the server's collector. Uses a GENERIC agent, which supports BOTH launch modes (a host-only
    # harness like Claude Code is covered by test_run_launch_profile_is_host_only_for_claude_code).
    _install_mission(migrated_home, "c1")
    c = _client()
    c.post("/api/agents", json={"name": "gen"})  # no kind → generic → both launch modes
    run = c.post("/api/runs", json={"agent": "gen", "mission": "c1"}).json()
    host = c.get(f"/api/runs/{run['run_id']}/launch-profile?launch_mode=host").json()
    # host mode points at the server's loopback bind host (settings.host, config-driven), not the
    # container-only host.docker.internal — so a terminal exporter can actually connect.
    endpoint = host["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"]
    assert "host.docker.internal" not in endpoint and endpoint.endswith(":4318")
    assert host["launch_mode"] == "host"
    # container (the default) keeps the host.docker.internal address for a containerized harness
    container = c.get(f"/api/runs/{run['run_id']}/launch-profile").json()
    assert container["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://host.docker.internal:4318"
    assert container["launch_mode"] == "container"


def test_run_launch_profile_codex_one_liner_and_resource_attr(migrated_home):
    # codex adapter (Phase 1): a kind=codex run gets the paste-and-go one-liner. Correlation rides
    # OTEL_RESOURCE_ATTRIBUTES (resource-attr tier, like Claude Code); the exporter config rides the
    # command's `-c` flags with the loopback per-signal endpoints filled in (host-only harness).
    _install_mission(migrated_home, "c1")
    c = _client()
    c.post("/api/agents", json={"name": "cx", "kind": "codex"})
    run = c.post("/api/runs", json={"agent": "cx", "mission": "c1"}).json()
    body = c.get(f"/api/runs/{run['run_id']}/launch-profile").json()
    assert body["launch_modes"] == ["host"]
    assert body["launch_mode"] == "host"  # container request clamped to the only supported mode
    assert body["correlation"] == "resource-attr"
    env = body["env"]
    assert env.get("OTEL_RESOURCE_ATTRIBUTES") == f"xorcise.run_id={run['run_id']}"
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in env  # codex ignores these — not in the block
    cmd = body["command"]
    assert cmd.startswith("codex ")
    assert "/v1/traces" in cmd and "/v1/logs" in cmd  # full per-signal URLs filled from the profile
    assert "host.docker.internal" not in cmd  # host mode → loopback, not the container address
    assert body["shell_block"].startswith("export OTEL_RESOURCE_ATTRIBUTES=")


def test_run_launch_profile_is_host_only_for_claude_code(migrated_home):
    # Claude Code is host-only: a container request is clamped to host (no container endpoint the
    # host `claude -p` could reach), and the response advertises only the host mode.
    _install_mission(migrated_home, "c1")
    c = _client()
    c.post("/api/agents", json={"name": "cc2", "kind": "claude-code"})
    run = c.post("/api/runs", json={"agent": "cc2", "mission": "c1"}).json()
    body = c.get(f"/api/runs/{run['run_id']}/launch-profile?launch_mode=container").json()
    assert body["launch_modes"] == ["host"]
    assert body["launch_mode"] == "host"  # container clamped to the only supported mode
    assert "host.docker.internal" not in body["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"]


def test_run_launch_profile_includes_startup_tips_for_claude_code(migrated_home):
    # A claude-code run also gets copy-paste startup tips: the `claude … -p …` command plus a
    # shell_block of `export`s ending in that command.
    _install_mission(migrated_home, "c1")
    c = _client()
    c.post("/api/agents", json={"name": "cc4", "kind": "claude-code"})
    run = c.post("/api/runs", json={"agent": "cc4", "mission": "c1"}).json()
    body = c.get(f"/api/runs/{run['run_id']}/launch-profile").json()
    assert body["command"].startswith("claude ") and " -p " in body["command"]
    assert "export OTEL_EXPORTER_OTLP_ENDPOINT" in body["shell_block"]
    assert body["shell_block"].strip().endswith(body["command"])


def test_mission_run_control_url_is_host_loopback_for_claude_code(migrated_home):
    # Claude Code runs on the host (`claude -p`); the persisted mission must point run-control at
    # the server's loopback, not the container-only host.docker.internal a host process can't
    # resolve — and the container-only --add-host note must be gone too.
    _install_mission(migrated_home, "c1")
    c = _client()
    c.post("/api/agents", json={"name": "cc", "kind": "claude-code"})
    run = c.post("/api/runs", json={"agent": "cc", "mission": "c1"}).json()
    prompt = c.get(f"/api/runs/{run['run_id']}/prompt").json()["prompt"]
    assert "host.docker.internal" not in prompt
    assert "127.0.0.1:3001/api/runs/" in prompt


def test_mission_run_control_url_is_container_for_generic_agent(migrated_home):
    # A container/generic harness keeps host.docker.internal (reachable from inside a container).
    _install_mission(migrated_home, "c1")
    c = _client()
    c.post("/api/agents", json={"name": "gen"})
    run = c.post("/api/runs", json={"agent": "gen", "mission": "c1"}).json()
    prompt = c.get(f"/api/runs/{run['run_id']}/prompt").json()["prompt"]
    assert "host.docker.internal:3001/api/runs/" in prompt


def test_run_launch_profile_unknown_run_is_404(migrated_home):
    assert _client().get("/api/runs/ghost/launch-profile").status_code == 404


def test_complete_records_result_for_runs_agent(migrated_home):
    _install_mission(migrated_home, "c1")
    c = _client()
    agent = c.post("/api/agents", json={"name": "alpha"}).json()
    run = c.post("/api/runs", json={"agent": "alpha", "mission": "c1"}).json()
    key = run["run_control_key"]
    r = c.post(f"/api/runs/{run['run_id']}/complete", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    assert r.json()["state"] == "terminal"
    assert r.json()["run_id"] == run["run_id"]
    hist = c.get(f"/api/agents/{agent['name']}/history").json()
    assert [h["run_id"] for h in hist] == [run["run_id"]]
    assert hist[0]["agent_id"] == agent["id"]


def test_complete_unknown_run_is_401(migrated_home):
    # /complete auth runs before the run lookup — a ghost run returns 401, not 404
    assert (
        _client()
        .post("/api/runs/ghost/complete", headers={"Authorization": "Bearer K"})
        .status_code
        == 401
    )


def test_complete_seals_the_run_trace(migrated_home):
    from xorcise.core import agents, runs
    from xorcise.core.otel.store import SqliteSealStore

    _install_mission(migrated_home, "c1")
    agent = agents.register("alice", endpoint="http://a")
    run = runs.create_run(agent_id=agent.id, mission="c1", run_id="run-seal", run_control_key="K")

    client = _client()
    assert SqliteSealStore().is_sealed("run-seal") is False
    resp = client.post(f"/api/runs/{run.run_id}/complete", headers={"Authorization": "Bearer K"})
    assert resp.status_code == 200
    assert SqliteSealStore().is_sealed("run-seal") is True


def test_run_result_serves_recorded_detail(migrated_home):
    """GET /runs/{id}/result returns grade + conditions composite."""
    from xorcise.core import agents, reporting, runs
    from xorcise.core.contracts.grading import GradeResult, ScoreBreakdown
    from xorcise.core.contracts.reporting import ResultConditions

    _install_mission(migrated_home, "c1")
    agent = agents.register("beta", endpoint="http://b")
    run = runs.create_run(
        agent_id=agent.id, mission="c1", run_id="run-result-1", run_control_key="K2"
    )
    grade = GradeResult(
        run_id=run.run_id,
        overall=0.75,
        breakdown=ScoreBreakdown(deterministic=0.8, judge=0.7),
        trace_ref=run.run_id,
    )
    reporting.record_result(
        run_id=run.run_id,
        agent_id=agent.id,
        result=grade,
        conditions=ResultConditions(model="m", budget_seconds=120, sandbox_ref="img"),
    )

    r = _client().get(f"/api/runs/{run.run_id}/result")
    assert r.status_code == 200
    body = r.json()
    assert body["grade"]["overall"] == 0.75
    assert body["conditions"]["model"] == "m"
    assert body["conditions"]["budget_seconds"] == 120
    assert body["conditions"]["sandbox_ref"] == "img"
    assert body["conditions"]["intel_disclosed"] == 0  # no intel disclosed for this run


def test_run_result_surfaces_intel_disclosed_provenance(migrated_home):
    """GET /runs/{id}/result reports how many intel were disclosed (disclosure provenance).

    Counted from the run-control submission store at read time (no results-table migration)."""
    from xorcise.core import agents, reporting, runs
    from xorcise.core.contracts.grading import GradeResult, ScoreBreakdown
    from xorcise.core.contracts.reporting import ResultConditions
    from xorcise.core.runcontrol.store import SqliteSubmissionStore

    _install_mission(migrated_home, "c1")
    agent = agents.register("beta", endpoint="http://b")
    run = runs.create_run(
        agent_id=agent.id, mission="c1", run_id="run-intel-1", run_control_key="K3"
    )
    store = SqliteSubmissionStore()
    store.record(run.run_id, "intel", "i1", "look here")
    store.record(run.run_id, "intel", "i2", "and here")
    reporting.record_result(
        run_id=run.run_id,
        agent_id=agent.id,
        result=GradeResult(
            run_id=run.run_id,
            overall=0.5,
            breakdown=ScoreBreakdown(deterministic=0.5, judge=0.5),
            trace_ref=run.run_id,
        ),
        conditions=ResultConditions(),
    )

    body = _client().get(f"/api/runs/{run.run_id}/result").json()
    assert body["conditions"]["intel_disclosed"] == 2


def test_run_artifacts_lists_submitted_payloads(migrated_home):
    """GET /runs/{id}/artifacts returns each submitted artifact's name/kind/seq/payload.

    The payloads are stored on submission but were never surfaced for operator review — only
    artifact names reached the result. This endpoint returns the full content, ordered by seq.
    """
    from xorcise.core import agents, runs
    from xorcise.core.runcontrol.store import SqliteSubmissionStore

    _install_mission(migrated_home, "c1")
    agent = agents.register("zeta", endpoint="http://z")
    run = runs.create_run(agent_id=agent.id, mission="c1", run_id="run-art-1", run_control_key="K")
    store = SqliteSubmissionStore()
    store.record(run.run_id, "artifact", "notes.txt", "recon findings")
    store.record(run.run_id, "flag", "flag", "XORCISE{demo}")
    store.record(run.run_id, "intel", "i1", "a intel")  # non-artifact — must be excluded

    r = _client().get(f"/api/runs/{run.run_id}/artifacts")
    assert r.status_code == 200
    body = r.json()
    assert [a["name"] for a in body] == ["notes.txt", "flag"]  # intel excluded, seq order
    flag = next(a for a in body if a["name"] == "flag")
    assert flag["payload"] == "XORCISE{demo}"
    assert flag["kind"] == "flag"


def test_run_artifacts_empty_when_none_submitted(migrated_home):
    """A known run with no submitted artifacts returns an empty list (200), not a 404."""
    from xorcise.core import agents, runs

    _install_mission(migrated_home, "c1")
    agent = agents.register("eta", endpoint="http://e")
    run = runs.create_run(agent_id=agent.id, mission="c1", run_id="run-art-2", run_control_key="K")
    r = _client().get(f"/api/runs/{run.run_id}/artifacts")
    assert r.status_code == 200
    assert r.json() == []


def test_run_artifacts_unknown_run_is_404(migrated_home):
    assert _client().get("/api/runs/ghost/artifacts").status_code == 404


def test_delete_run_removes_result_and_record(migrated_home):
    """DELETE /runs/{id} removes a terminal run's result + record so it leaves history."""
    from datetime import UTC, datetime

    from xorcise.core import agents, reporting, runs
    from xorcise.core.contracts.grading import GradeResult, ScoreBreakdown

    _install_mission(migrated_home, "c1")
    agent = agents.register("theta", endpoint="http://t")
    run = runs.create_run(agent_id=agent.id, mission="c1", run_id="run-del-1", run_control_key="K")
    runs.mark_terminal(run.run_id, "done", datetime.now(UTC))
    reporting.record_result(
        run_id=run.run_id,
        agent_id=agent.id,
        result=GradeResult(
            run_id=run.run_id,
            overall=0.5,
            breakdown=ScoreBreakdown(deterministic=0.5, judge=0.5),
            trace_ref=run.run_id,
        ),
    )
    c = _client()
    assert c.delete(f"/api/runs/{run.run_id}").status_code == 204
    # gone from the runs list, agent history, and the result endpoint
    assert [r["run_id"] for r in c.get("/api/runs").json()] == []
    assert c.get(f"/api/agents/{agent.name}/history").json() == []
    assert c.get(f"/api/runs/{run.run_id}/result").status_code == 404


def test_delete_run_unknown_is_404(migrated_home):
    assert _client().delete("/api/runs/ghost").status_code == 404


def test_delete_run_active_is_409(migrated_home):
    """A still-active run can't be deleted — terminate it first (avoids stranding its env)."""
    from xorcise.core import agents, runs

    _install_mission(migrated_home, "c1")
    agent = agents.register("iota", endpoint="http://i")
    run = runs.create_run(
        agent_id=agent.id, mission="c1", run_id="run-del-active", run_control_key="K"
    )
    r = _client().delete(f"/api/runs/{run.run_id}")
    assert r.status_code == 409
    assert runs.get(run.run_id) is not None  # not deleted


def test_run_result_404_when_unrecorded(migrated_home):
    """GET /runs/{id}/result returns 404 for an UNKNOWN run."""
    assert _client().get("/api/runs/does-not-exist/result").status_code == 404


def test_run_result_202_grading_when_terminal_but_ungraded(migrated_home):
    """A terminal run with no result yet returns 202 grading-in-progress, not 404.

    Grading is async after /complete, so a terminal-but-ungraded run is a normal transient
    state and must be distinguishable from an unknown run (404) and a still-active run (409).
    """
    from datetime import UTC, datetime

    from xorcise.core import agents, runs

    _install_mission(migrated_home, "c1")
    agent = agents.register("delta", endpoint="http://d")
    run = runs.create_run(
        agent_id=agent.id, mission="c1", run_id="run-grading-1", run_control_key="K4"
    )
    runs.mark_terminal(run.run_id, "done", datetime.now(UTC))
    r = _client().get(f"/api/runs/{run.run_id}/result")
    assert r.status_code == 202
    assert r.json()["status"] == "grading"


def test_run_result_409_when_active_and_ungraded(migrated_home):
    """An active (non-terminal) run with no result returns 409, not 404."""
    from xorcise.core import agents, runs

    _install_mission(migrated_home, "c1")
    agent = agents.register("eps", endpoint="http://e")
    run = runs.create_run(
        agent_id=agent.id, mission="c1", run_id="run-active-1", run_control_key="K5"
    )
    r = _client().get(f"/api/runs/{run.run_id}/result")
    assert r.status_code == 409


def test_run_result_partial_fields_present_for_timed_out_run(migrated_home):
    """GET /runs/{id}/result includes partial=True + partial_trigger for a timed-out run.

    Asserts the partial state recorded by T1 is now surfaced in the REST response.
    """
    from xorcise.core import agents, reporting, runs
    from xorcise.core.contracts.grading import GradeResult, ScoreBreakdown

    _install_mission(migrated_home, "c1")
    agent = agents.register("gamma", endpoint="http://g")
    run = runs.create_run(
        agent_id=agent.id, mission="c1", run_id="run-partial-1", run_control_key="K3"
    )
    grade = GradeResult(
        run_id=run.run_id,
        overall=0.4,
        breakdown=ScoreBreakdown(deterministic=0.5, judge=0.3),
        trace_ref=run.run_id,
    )
    reporting.record_result(
        run_id=run.run_id,
        agent_id=agent.id,
        result=grade,
        partial=True,
        partial_trigger="timeout",
    )

    r = _client().get(f"/api/runs/{run.run_id}/result")
    assert r.status_code == 200
    body = r.json()
    assert body["partial"] is True
    assert body["partial_trigger"] == "timeout"


def test_run_result_partial_false_for_clean_run(migrated_home):
    """GET /runs/{id}/result has partial=False for a clean (done) run.

    No-false-positive guard: the partial marker must be ABSENT / False for a non-partial result.
    """
    from xorcise.core import agents, reporting, runs
    from xorcise.core.contracts.grading import GradeResult, ScoreBreakdown

    _install_mission(migrated_home, "c1")
    agent = agents.register("delta", endpoint="http://d")
    run = runs.create_run(
        agent_id=agent.id, mission="c1", run_id="run-clean-1", run_control_key="K4"
    )
    grade = GradeResult(
        run_id=run.run_id,
        overall=1.0,
        breakdown=ScoreBreakdown(deterministic=1.0, judge=1.0),
        trace_ref=run.run_id,
    )
    reporting.record_result(
        run_id=run.run_id,
        agent_id=agent.id,
        result=grade,
        partial=False,
        partial_trigger=None,
    )

    r = _client().get(f"/api/runs/{run.run_id}/result")
    assert r.status_code == 200
    body = r.json()
    # No-false-positive: clean run must NOT be flagged as partial
    assert body["partial"] is False
    assert body["partial_trigger"] is None


# ── GET /runs/{id}/report — downloadable Markdown / HTML run report ──────────────────────────


def _graded_run(
    home: Path,
    *,
    slug: str = "c1",
    agent_name: str = "report-agent",
    run_id: str = "run-rep-1",
) -> tuple[Any, Any]:
    """A terminal, graded run with a submitted artifact — the happy path for a report."""
    from datetime import UTC, datetime

    from xorcise.core import agents, reporting, runs
    from xorcise.core.contracts.grading import CheckVerdict, GradeResult, ScoreBreakdown
    from xorcise.core.runcontrol.store import SqliteSubmissionStore

    _install_mission(home, slug)
    agent = agents.register(agent_name, endpoint="http://r")
    run = runs.create_run(agent_id=agent.id, mission=slug, run_id=run_id, run_control_key="K")
    runs.mark_terminal(run.run_id, "done", datetime.now(UTC))
    SqliteSubmissionStore().record(run.run_id, "flag", "flag", "XORCISE{demo}")
    reporting.record_result(
        run_id=run.run_id,
        agent_id=agent.id,
        result=GradeResult(
            run_id=run.run_id,
            overall=0.75,
            breakdown=ScoreBreakdown(deterministic=1.0, judge=0.5),
            key_evidence=("found the flag",),
            trace_ref=run.run_id,
            check_breakdown=(
                CheckVerdict(
                    id="flag-correct",
                    source="control",
                    ref="flag",
                    op="equals",
                    value="XORCISE{demo}",
                    passed=True,
                    weight=1.0,
                ),
            ),
        ),
    )
    return agent, run


def test_run_report_markdown_is_an_attachment_with_the_full_result(migrated_home):
    agent, run = _graded_run(migrated_home)
    r = _client().get(f"/api/runs/{run.run_id}/report?format=md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "attachment" in r.headers["content-disposition"]
    assert f'filename="xorcise-run-{run.run_id[:8]}-c1.md"' in r.headers["content-disposition"]
    body = r.text
    assert body.startswith("# XORCISE Run Report — c1")
    assert f"| Run ID | {run.run_id} |" in body
    assert "| Name | c1 |" in body  # unnamed run falls back to the mission as its label
    # Agent joins from the registry (not the raw id) and carries its pinned version.
    assert f"| Agent | {agent.name} v1 |" in body
    assert "**Overall** | **0.75 (75%)**" in body
    assert "| PASS | flag-correct |" in body
    assert "- found the flag" in body
    assert "XORCISE{demo}" in body  # the submitted artifact's payload is embedded


def test_run_report_html_is_a_standalone_document(migrated_home):
    _agent, run = _graded_run(migrated_home, run_id="run-rep-html")
    r = _client().get(f"/api/runs/{run.run_id}/report?format=html")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert f'filename="xorcise-run-{run.run_id[:8]}-c1.html"' in r.headers["content-disposition"]
    assert r.text.startswith("<!doctype html>")
    assert "<title>XORCISE Run Report — c1</title>" in r.text


def test_run_report_defaults_to_markdown(migrated_home):
    _agent, run = _graded_run(migrated_home, run_id="run-rep-default")
    r = _client().get(f"/api/runs/{run.run_id}/report")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")


def test_run_report_rejects_an_unknown_format(migrated_home):
    _agent, run = _graded_run(migrated_home, run_id="run-rep-fmt")
    r = _client().get(f"/api/runs/{run.run_id}/report?format=pdf")
    assert r.status_code == 422
    assert "pdf" in r.json()["detail"]


def test_run_report_unknown_run_is_404(migrated_home):
    assert _client().get("/api/runs/ghost/report").status_code == 404


def test_run_report_terminal_but_ungraded_is_202_grading(migrated_home):
    """Grading is async after the run completes — mirror /result's 202 rather than 404."""
    from datetime import UTC, datetime

    from xorcise.core import agents, runs

    _install_mission(migrated_home, "c1")
    agent = agents.register("rep-grading", endpoint="http://g")
    run = runs.create_run(
        agent_id=agent.id, mission="c1", run_id="run-rep-grading", run_control_key="K"
    )
    runs.mark_terminal(run.run_id, "done", datetime.now(UTC))
    r = _client().get(f"/api/runs/{run.run_id}/report")
    assert r.status_code == 202
    assert r.json() == {"run_id": run.run_id, "status": "grading"}


def test_run_report_active_run_is_409(migrated_home):
    from xorcise.core import agents, runs

    _install_mission(migrated_home, "c1")
    agent = agents.register("rep-active", endpoint="http://a")
    run = runs.create_run(
        agent_id=agent.id, mission="c1", run_id="run-rep-active", run_control_key="K"
    )
    r = _client().get(f"/api/runs/{run.run_id}/report")
    assert r.status_code == 409
    assert "not terminal yet" in r.json()["detail"]

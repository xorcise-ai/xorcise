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


def test_history_entries_carry_the_grades_judge_status(migrated_home):
    """The agent page averages these entries, so each must say whether its judge ran (#143)."""
    from xorcise.core import agents, reporting
    from xorcise.core.contracts.grading import GradeResult, JudgeStatus, ScoreBreakdown

    agent = agents.register("gamma", endpoint="http://g")
    cases: tuple[tuple[str, JudgeStatus], ...] = (
        ("r-ok", "ok"),
        ("r-none", "unavailable"),
        ("r-part", "partial"),
    )
    for run_id, status in cases:
        reporting.record_result(
            run_id=run_id,
            agent_id=agent.id,
            result=GradeResult(
                run_id=run_id,
                overall=0.5,
                breakdown=ScoreBreakdown(deterministic=1.0, judge=0.0),
                judge_status=status,
            ),
        )
    hist = _client().get("/api/agents/gamma/history").json()
    assert {h["run_id"]: h["judge_status"] for h in hist} == {
        "r-ok": "ok",
        "r-none": "unavailable",
        "r-part": "partial",
    }


def test_a_stored_grade_that_cannot_be_read_back_has_no_judge_status(migrated_home):
    from sqlalchemy import update

    from xorcise.core import agents, reporting
    from xorcise.core.contracts.grading import GradeResult, ScoreBreakdown
    from xorcise.core.db import session_scope
    from xorcise.core.reporting.models import ResultRow

    agent = agents.register("delta", endpoint="http://d")
    reporting.record_result(
        run_id="r-bad",
        agent_id=agent.id,
        result=GradeResult(
            run_id="r-bad", overall=0.5, breakdown=ScoreBreakdown(deterministic=0.5, judge=0.5)
        ),
    )
    with session_scope() as s:
        s.execute(update(ResultRow).where(ResultRow.run_id == "r-bad").values(detail_json="{"))
    (entry,) = _client().get("/api/agents/delta/history").json()
    assert entry["judge_status"] is None

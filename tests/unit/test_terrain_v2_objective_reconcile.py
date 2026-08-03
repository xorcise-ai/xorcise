"""Objective terminal-grade reconciliation.

The v2 map's objective completion is model-driven live, then reconciled at terminal against
XORCISE's OWN recorded grade (reporting.get_result) — a deterministic, model-independent read —
so the served map always agrees with the official verdict. No-op when
there's no objective, the run isn't terminal, or it wasn't graded solved.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from xorcise.core import reporting, runs
from xorcise.core.contracts.grading import CheckVerdict, GradeResult, ScoreBreakdown
from xorcise.core.contracts.mission import (
    EnvironmentSpec,
    MissionManifest,
    MissionMetadata,
    TerrainSpec,
)
from xorcise.core.rest.run_terminate import seal_terminal
from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.unit


def _now() -> datetime:
    return datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


def _manifest_with_objective() -> MissionManifest:
    spec = TerrainSpec(
        summary="pivot mission",
        groups=({"id": "dmz", "description": "DMZ segment"},),
        nodes=(
            {
                "id": "web",
                "parent": "dmz",
                "type": "web_service",
                "objective": True,
                "completion_condition": "recover the flag",
            },
        ),
    )
    return MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id="c1", name="c1", objective="o", type="lab"),
        environment=EnvironmentSpec(entry_networks=("dmz",)),
        terrain=spec,
    )


def _manifest_without_objective() -> MissionManifest:
    return MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id="c2", name="c2", objective="o", type="lab"),
        environment=EnvironmentSpec(entry_networks=("dmz",), static_ips={"web": {"dmz": 10}}),
        terrain=None,
    )


def _install_mission(home, slug: str, manifest: MissionManifest) -> None:
    from xorcise.core.contracts.control import MissionRef
    from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission

    root = home / "missions" / slug
    root.mkdir(parents=True)
    ref = MissionRef(mission_id=slug, image=f"xorcise/mission-{slug}:0")
    (root / INSTALLED_FILE).write_text(InstalledMission(slug, root, manifest, ref).to_record())


def _solved_grade(run_id: str) -> GradeResult:
    return GradeResult(
        run_id=run_id,
        overall=1.0,
        breakdown=ScoreBreakdown(deterministic=1.0, judge=1.0),
    )


def _partial_grade(run_id: str) -> GradeResult:
    return GradeResult(
        run_id=run_id,
        overall=0.5,
        breakdown=ScoreBreakdown(deterministic=1.0, judge=0.0),
    )


def _check_verdict(*, passed: bool) -> CheckVerdict:
    return CheckVerdict(
        id="flag", source="artifact", ref="flag", op="eq", passed=passed, weight=1.0
    )


def _deterministic_pass_partial_judge_grade(run_id: str) -> GradeResult:
    """Flag check passed (deterministic sub-score maxed) but the judge half is unscored — this
    is the KEY new behavior: overall stays 0.5 (below the old perfect-grade bar) yet the
    deterministic half alone must green the objective."""
    return GradeResult(
        run_id=run_id,
        overall=0.5,
        breakdown=ScoreBreakdown(deterministic=1.0, judge=0.0),
        check_breakdown=(_check_verdict(passed=True),),
    )


def _deterministic_fail_high_judge_grade(run_id: str) -> GradeResult:
    """Flag check failed (deterministic sub-score partial) even though the judge scored high —
    must stay red regardless of the judge half."""
    return GradeResult(
        run_id=run_id,
        overall=0.75,
        breakdown=ScoreBreakdown(deterministic=0.5, judge=1.0),
        check_breakdown=(_check_verdict(passed=False),),
    )


def _no_deterministic_checks_full_grade(run_id: str) -> GradeResult:
    """Judge-only mission (no deterministic checks at all): falls back to the full blended
    `overall` since there is no deterministic sub-score to read as a pass signal."""
    return GradeResult(
        run_id=run_id,
        overall=1.0,
        breakdown=ScoreBreakdown(deterministic=0.0, judge=1.0),
        check_breakdown=(),
    )


def _no_deterministic_checks_partial_grade(run_id: str) -> GradeResult:
    return GradeResult(
        run_id=run_id,
        overall=0.5,
        breakdown=ScoreBreakdown(deterministic=0.0, judge=1.0),
        check_breakdown=(),
    )


@pytest.fixture()
def client(migrated_home):
    return TestClient(build_rest_app())


def _completed_objective_updates(body: dict[str, Any], target_id: str) -> list[dict[str, Any]]:
    return [
        u
        for u in body["updates"]
        if u["target_kind"] == "node" and u["target_id"] == target_id and u["state"] == "completed"
    ]


def test_terminal_and_solved_marks_objective_completed(client, migrated_home):
    _install_mission(migrated_home, "c-solved", _manifest_with_objective())
    runs.create_run(
        run_id="r-solved",
        agent_id="a1",
        mission="c-solved",
        budget_seconds=60,
        source_agent="generic",
    )
    seal_terminal("r-solved", "done", _now())
    reporting.record_result("r-solved", "a1", _solved_grade("r-solved"))

    body = client.get("/api/runs/r-solved/terrain2").json()
    assert body["objective_id"] == "web"
    matches = _completed_objective_updates(body, "web")
    assert len(matches) == 1
    assert matches[0]["event_id"] is None


def test_terminal_but_not_solved_no_completed_update(client, migrated_home):
    _install_mission(migrated_home, "c-partial", _manifest_with_objective())
    runs.create_run(
        run_id="r-partial",
        agent_id="a1",
        mission="c-partial",
        budget_seconds=60,
        source_agent="generic",
    )
    seal_terminal("r-partial", "done", _now())
    reporting.record_result("r-partial", "a1", _partial_grade("r-partial"))

    body = client.get("/api/runs/r-partial/terrain2").json()
    assert body["objective_id"] == "web"
    assert _completed_objective_updates(body, "web") == []


def test_deterministic_pass_marks_objective_completed_despite_partial_judge(client, migrated_home):
    """The deterministic flag-check half passing is sufficient to green the objective, even when
    the judge half is unscored (overall=0.5, well below the old perfect-grade bar)."""
    _install_mission(migrated_home, "c-detpass", _manifest_with_objective())
    runs.create_run(
        run_id="r-detpass",
        agent_id="a1",
        mission="c-detpass",
        budget_seconds=60,
        source_agent="generic",
    )
    seal_terminal("r-detpass", "done", _now())
    reporting.record_result("r-detpass", "a1", _deterministic_pass_partial_judge_grade("r-detpass"))

    body = client.get("/api/runs/r-detpass/terrain2").json()
    assert body["objective_id"] == "web"
    matches = _completed_objective_updates(body, "web")
    assert len(matches) == 1
    assert matches[0]["event_id"] is None


def test_deterministic_fail_stays_red_despite_high_judge(client, migrated_home):
    """A failed deterministic flag-check must NOT green the objective even when the judge half
    scored high (overall=0.75)."""
    _install_mission(migrated_home, "c-detfail", _manifest_with_objective())
    runs.create_run(
        run_id="r-detfail",
        agent_id="a1",
        mission="c-detfail",
        budget_seconds=60,
        source_agent="generic",
    )
    seal_terminal("r-detfail", "done", _now())
    reporting.record_result("r-detfail", "a1", _deterministic_fail_high_judge_grade("r-detfail"))

    body = client.get("/api/runs/r-detfail/terrain2").json()
    assert body["objective_id"] == "web"
    assert _completed_objective_updates(body, "web") == []


def test_no_deterministic_checks_falls_back_to_full_grade_green(client, migrated_home):
    """A judge-only mission (empty check_breakdown) has no deterministic sub-score to read, so
    the reconciler falls back to the full blended `overall`."""
    _install_mission(migrated_home, "c-nodetok", _manifest_with_objective())
    runs.create_run(
        run_id="r-nodetok",
        agent_id="a1",
        mission="c-nodetok",
        budget_seconds=60,
        source_agent="generic",
    )
    seal_terminal("r-nodetok", "done", _now())
    reporting.record_result("r-nodetok", "a1", _no_deterministic_checks_full_grade("r-nodetok"))

    body = client.get("/api/runs/r-nodetok/terrain2").json()
    assert body["objective_id"] == "web"
    matches = _completed_objective_updates(body, "web")
    assert len(matches) == 1
    assert matches[0]["event_id"] is None


def test_no_deterministic_checks_falls_back_to_full_grade_not_solved(client, migrated_home):
    _install_mission(migrated_home, "c-nodetpartial", _manifest_with_objective())
    runs.create_run(
        run_id="r-nodetpartial",
        agent_id="a1",
        mission="c-nodetpartial",
        budget_seconds=60,
        source_agent="generic",
    )
    seal_terminal("r-nodetpartial", "done", _now())
    reporting.record_result(
        "r-nodetpartial", "a1", _no_deterministic_checks_partial_grade("r-nodetpartial")
    )

    body = client.get("/api/runs/r-nodetpartial/terrain2").json()
    assert body["objective_id"] == "web"
    assert _completed_objective_updates(body, "web") == []


def test_no_objective_is_a_no_op(client, migrated_home):
    _install_mission(migrated_home, "c-noobj", _manifest_without_objective())
    runs.create_run(
        run_id="r-noobj",
        agent_id="a1",
        mission="c-noobj",
        budget_seconds=60,
        source_agent="generic",
    )
    seal_terminal("r-noobj", "done", _now())
    reporting.record_result("r-noobj", "a1", _solved_grade("r-noobj"))

    body = client.get("/api/runs/r-noobj/terrain2").json()
    assert body["objective_id"] is None
    assert all(u["state"] != "completed" for u in body["updates"])


def test_not_terminal_no_completed_update(client, migrated_home):
    _install_mission(migrated_home, "c-live", _manifest_with_objective())
    runs.create_run(
        run_id="r-live",
        agent_id="a1",
        mission="c-live",
        budget_seconds=60,
        source_agent="generic",
    )
    # run stays active — no seal_terminal, no recorded result

    body = client.get("/api/runs/r-live/terrain2").json()
    assert body["objective_id"] == "web"
    assert _completed_objective_updates(body, "web") == []


def test_reconciliation_does_not_duplicate_an_existing_completed_update(client, migrated_home):
    """If the BYOM mission-plane attribution already marked the objective completed, the
    deterministic reconciliation must not add a second update for it."""
    from xorcise.core.runs.terrain_update_store import SqliteTerrainUpdateStore, _UpdateInput

    _install_mission(migrated_home, "c-already", _manifest_with_objective())
    runs.create_run(
        run_id="r-already",
        agent_id="a1",
        mission="c-already",
        budget_seconds=60,
        source_agent="generic",
    )
    SqliteTerrainUpdateStore().record_many(
        "r-already",
        [_UpdateInput(event_id="e1", target_kind="node", target_id="web", state="completed")],
    )
    seal_terminal("r-already", "done", _now())
    reporting.record_result("r-already", "a1", _solved_grade("r-already"))

    body = client.get("/api/runs/r-already/terrain2").json()
    assert len(_completed_objective_updates(body, "web")) == 1

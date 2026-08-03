"""A run report's TERRAIN must agree with the screen for a SOLVED run.

The live /terrain2 endpoint greens the objective node from XORCISE's OWN recorded grade
(objective_grade_update) — never persisted, recomputed on every read. The offline report replays
only persisted evidence, so without the same reconciliation a solved run's report shows a greyed
map while the app shows the objective reached. These tests pin the report to apply it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from xorcise.core import reporting, runs
from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.grading import GradeResult, ScoreBreakdown
from xorcise.core.contracts.mission import (
    EnvironmentSpec,
    MissionManifest,
    MissionMetadata,
    TerrainSpec,
)
from xorcise.core.rest.report_assembly import assemble_report
from xorcise.core.rest.run_terminate import seal_terminal

pytestmark = pytest.mark.unit


def _install_terrain_mission(home: Path, slug: str) -> None:
    from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission

    root = home / "missions" / slug
    root.mkdir(parents=True)
    spec = TerrainSpec(
        summary="reach the web host and recover the flag",
        groups=({"id": "dmz", "description": "DMZ segment"},),
        nodes=(
            {
                "id": "web",
                "parent": "dmz",
                "type": "web_service",
                "objective": True,
                "discovery_condition": "reach the web host",
                "completion_condition": "recover the flag",
            },
        ),
        edges=({"id": "e1", "src": "web", "dst": "dmz"},),
    )
    manifest = MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id=slug, name=slug, objective="o", type="lab"),
        environment=EnvironmentSpec(entry_networks=("dmz",)),
        terrain=spec,
    )
    ref = MissionRef(mission_id=slug, image=f"xorcise/mission-{slug}:0")
    (root / INSTALLED_FILE).write_text(InstalledMission(slug, root, manifest, ref).to_record())


def _terminal_run(slug: str) -> str:
    run = runs.create_run(agent_id="a1", mission=slug, budget_seconds=600)
    seal_terminal(run.run_id, "done", datetime.now(UTC))
    return run.run_id


def _record(run_id: str, overall: float) -> None:
    reporting.record_result(
        run_id,
        "a1",
        GradeResult(
            run_id=run_id,
            overall=overall,
            breakdown=ScoreBreakdown(deterministic=overall),
            trace_ref=run_id,
        ),
    )


def _objective_state(run_id: str, mission: str) -> str | None:
    ctx = assemble_report(run_id)
    assert ctx is not None and ctx.terrain is not None
    web = next((n for n in ctx.terrain.nodes if n.id == "web"), None)
    assert web is not None
    return web.state


def test_report_terrain_greens_the_objective_for_a_solved_run(migrated_home) -> None:
    """No BYOM terrain updates at all (attribution never ran — e.g. the judge key is down), yet a
    run XORCISE graded solved shows its objective node reached, exactly as the live map does."""
    _install_terrain_mission(migrated_home, "c1")
    rid = _terminal_run("c1")
    _record(rid, overall=1.0)

    assert _objective_state(rid, "c1") == "completed"


def test_report_terrain_leaves_the_objective_grey_for_an_unsolved_run(migrated_home) -> None:
    """No-false-positive: a run that did NOT grade solved keeps its greyed objective."""
    _install_terrain_mission(migrated_home, "c2")
    rid = _terminal_run("c2")
    _record(rid, overall=0.0)

    assert _objective_state(rid, "c2") == "defined"

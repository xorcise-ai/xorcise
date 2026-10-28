"""Run performance: how each finished run counts, and the summaries every performance view draws.

The median is over runs graded with a judge, a partly-judged run counting at its lower bound. A
run that timed out is in it, at the score it earned: not finishing within the budget is the
agent's failure. A run whose judge never ran, one an operator stopped, an environment failure, a
run whose grading crashed and a run still grading are each counted and disclosed, never in the
median. An older mission version is summarised apart from the current one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests._helpers import install_mission
from xorcise.core.contracts.grading import GradeResult, JudgeStatus, ScoreBreakdown
from xorcise.core.contracts.run import RunEntry
from xorcise.core.reporting.scores import (
    GradeFacts,
    bucket,
    current_versions,
    score_rows,
    summarize,
)

pytestmark = pytest.mark.unit

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _run(i: int, trigger: str | None = "done", **over: Any) -> RunEntry:
    fields: dict[str, Any] = {
        "run_id": f"r{i}",
        "agent_id": "a1",
        "mission": "m1",
        "state": "terminal" if trigger else "xorcising",
        "created_at": T0 + timedelta(minutes=i),
        "completed_at": T0 + timedelta(minutes=i, seconds=30) if trigger else None,
        "terminal_trigger": trigger,
        "mission_version": "1.0.0",
    }
    fields.update(over)
    return RunEntry(**fields)


def _grade(
    run_id: str,
    det: float,
    judge: float,
    status: JudgeStatus = "ok",
    judge_upper: float | None = None,
) -> GradeResult:
    upper = 0.5 * det + 0.5 * judge_upper if judge_upper is not None else None
    return GradeResult(
        run_id=run_id,
        overall=0.5 * det + 0.5 * judge,
        breakdown=ScoreBreakdown(deterministic=det, judge=judge),
        judge_status=status,
        judge_upper=judge_upper,
        overall_upper=upper,
    )


# --- bucket ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("trigger", "grade", "expected"),
    [
        ("done", _grade("x", 1, 0.8), "scored"),
        ("done", _grade("x", 1, 0.4, "partial", 0.9), "scored"),
        ("done", _grade("x", 1, 0, "unavailable"), "no_judge"),
        ("done", _grade("x", 1, 0, "model-not-configured"), "no_judge"),
        # Not finishing within the budget is the agent's failure: scored at what it earned.
        ("timeout", _grade("x", 0.2, 0.1), "scored"),
        ("timeout", _grade("x", 0.2, 0, "unavailable"), "no_judge"),
        # An operator's stop says nothing about the agent.
        ("operator", _grade("x", 1, 0.8), "stopped"),
        ("deploy_failed", None, "infra_failed"),
        ("crashed", None, "infra_failed"),
        ("done", None, "grading"),
    ],
)
def test_each_finished_run_counts_one_way(trigger, grade, expected) -> None:
    assert bucket(_run(1, trigger), GradeFacts.of(grade) if grade else None) == expected


def _crashed(**over: Any) -> GradeResult:
    """rest.run_terminate's stand-in when grading itself raises."""
    fields: dict[str, Any] = {
        "run_id": "x",
        "overall": 0.0,
        "breakdown": ScoreBreakdown(),
        "judge_status": "unavailable",
        "judge_detail": "grading failed: boom",
        "grading_failed": True,
    }
    fields.update(over)
    return GradeResult(**fields)


@pytest.mark.parametrize("trigger", ["done", "timeout", "operator"])
def test_a_run_whose_grading_crashed_is_not_a_run_with_no_judge(trigger) -> None:
    """Nothing graded it, the checks included: its zero is a stand-in, not a score to disclose as
    "no judge"."""
    assert bucket(_run(1, trigger), GradeFacts.of(_crashed())) == "grading_failed"


def test_a_crash_recorded_before_the_flag_is_read_from_its_detail() -> None:
    legacy = _crashed(grading_failed=False)
    assert bucket(_run(1), GradeFacts.of(legacy)) == "grading_failed"
    # Read straight from a stored row that predates the field.
    facts = GradeFacts.from_detail(
        {"judge_status": "unavailable", "judge_detail": "grading failed: boom"},
        overall=0.0,
        deterministic=0.0,
        judge=0.0,
        judge_model=None,
    )
    assert facts.grading_failed


def test_a_run_that_ended_before_the_agent_started_is_not_a_row() -> None:
    """A run stopped, or out of budget, before its agent started is not one of the agent's runs
    (#187): not even a mark that does not count, unlike an environment failure."""
    runs = [_run(1, "cancelled"), _run(2, "deploy_failed"), _run(3, "never_started")]
    rows = score_rows(runs, {}, {"m1": "1.0.0"})
    assert [(r.run_id, r.bucket) for r in rows] == [("r2", "infra_failed")]
    assert summarize(rows, by="agent")[0].runs == 1


def test_a_run_still_in_progress_is_not_a_row() -> None:
    assert score_rows([_run(1, None)], {}, {}) == []


# --- the median and its disclosures ------------------------------------------------------------


def _facts(grades: dict[str, GradeResult], judge_model: str | None = None) -> dict[str, GradeFacts]:
    return {k: GradeFacts.of(g, judge_model) for k, g in grades.items()}


def _summary(runs: list[RunEntry], grades: dict[str, GradeResult], by: Any = "agent"):
    rows = score_rows(runs, _facts(grades, "judge-m"), {"m1": "1.0.0"})
    return summarize(rows, by)


def test_the_median_is_over_scored_runs_only() -> None:
    """The three judged runs that finished and the one that timed out are in it. A 0.5 from a run
    with no judge, or an operator-stopped run's score, would have moved it."""
    runs = [
        _run(1),
        _run(2),
        _run(3),
        _run(4),  # no judge
        _run(5, "timeout"),
        _run(6, "deploy_failed"),
        _run(7),  # grading
        _run(8, "operator"),
        _run(9),  # grading crashed
    ]
    grades = {
        "r1": _grade("r1", 1, 0.8),
        "r2": _grade("r2", 1, 0.6),
        "r3": _grade("r3", 0.8, 0.6),
        "r4": _grade("r4", 1, 0, "unavailable"),
        "r5": _grade("r5", 0.2, 0.1),
        "r8": _grade("r8", 0, 0),
        "r9": _crashed(run_id="r9"),
    }
    (s,) = _summary(runs, grades)
    assert s.runs == 9
    assert s.scored == 4
    assert s.timed_out == 1
    # 0.9, 0.8, 0.7 and the timeout's 0.15: the timeout pulls the median down, as it should.
    assert s.median == pytest.approx(0.75)
    assert (s.no_judge, s.stopped, s.grading_failed, s.infra_failed, s.grading) == (1, 1, 1, 1, 1)
    assert s.completed == 6
    assert s.best == pytest.approx(0.9)
    # The mean is over the same four runs: (0.9 + 0.8 + 0.7 + 0.15) / 4.
    assert s.mean == pytest.approx(0.6375)
    assert s.mean_deterministic == pytest.approx(0.75)
    assert s.mean_judge == pytest.approx(0.525)


def test_a_timed_out_run_is_marked_so_the_views_can_show_it() -> None:
    rows = score_rows([_run(1, "timeout"), _run(2)], {}, {"m1": "1.0.0"})
    assert [r.timed_out for r in rows] == [True, False]


def test_one_summary_over_every_run() -> None:
    runs = [_run(1), _run(2, agent_id="a2", mission="m2")]
    grades = {"r1": _grade("r1", 1, 0.8), "r2": _grade("r2", 1, 0.4)}
    (s,) = _summary(runs, grades, by="all")
    assert (s.agent_id, s.mission, s.runs, s.scored) == (None, None, 2, 2)
    assert s.median == pytest.approx(0.8)


def test_rows_carry_how_many_intel_each_run_was_given() -> None:
    grades = _facts({"r1": _grade("r1", 1, 0.8), "r2": _grade("r2", 1, 0.8)})
    rows = score_rows([_run(1), _run(2), _run(3)], grades, {"m1": "1.0.0"}, {"r2": 3, "r3": 1})
    assert [r.intel_disclosed for r in rows] == [0, 3, 1]
    # r3 is still grading, so only r2 is an assisted run in the median.
    (s,) = summarize(rows, "all")
    assert (s.scored, s.assisted) == (2, 1)


def test_a_partly_judged_run_counts_at_its_lower_bound() -> None:
    (s,) = _summary([_run(1)], {"r1": _grade("r1", 1, 0.4, "partial", 0.9)})
    assert s.scored == 1
    assert s.partial_judge == 1
    assert s.median == pytest.approx(0.7)


def test_the_judge_gap_uses_only_fully_judged_runs() -> None:
    """A partial judge's score is a lower bound, so it would overstate the disagreement."""
    runs = [_run(1), _run(2), _run(3)]
    grades = {
        "r1": _grade("r1", 1, 0.4),
        "r2": _grade("r2", 0.8, 0.4),
        "r3": _grade("r3", 1, 0.1, "partial", 0.9),
    }
    (s,) = _summary(runs, grades)
    assert s.judge_gap == pytest.approx(-0.5)


def test_no_scored_run_leaves_the_median_empty_not_zero() -> None:
    (s,) = _summary([_run(1)], {"r1": _grade("r1", 1, 0, "unavailable")})
    assert s.median is None and s.q1 is None and s.best is None
    assert s.no_judge == 1


# --- mission versions --------------------------------------------------------------------------


def test_an_older_mission_version_is_its_own_group_and_marked_older() -> None:
    runs = [_run(1, mission_version="1.0.0"), _run(2, mission_version="1.1.0")]
    grades = {"r1": _grade("r1", 1, 0.2), "r2": _grade("r2", 1, 0.8)}
    rows = score_rows(runs, _facts(grades), {"m1": "1.1.0"})
    assert [(r.mission_version, r.current_version) for r in rows] == [
        ("1.0.0", False),
        ("1.1.0", True),
    ]
    by_version = {s.mission_version: s for s in summarize(rows, "mission")}
    assert by_version["1.0.0"].current_version is False
    assert by_version["1.0.0"].median == pytest.approx(0.6)
    assert by_version["1.1.0"].current_version is True
    assert by_version["1.1.0"].median == pytest.approx(0.9)


def test_a_pre_contract_run_is_labelled_by_its_install_revision() -> None:
    rows = score_rows([_run(1, mission_version=None, install_revision=3)], {}, {"m1": "3"})
    assert rows[0].mission_version == "3"
    assert rows[0].current_version is True


def test_for_a_mission_no_longer_installed_its_newest_version_is_current() -> None:
    runs = [_run(1, mission_version="1.0.0"), _run(2, mission_version="2.0.0")]
    rows = score_rows(runs, {}, current_versions(runs, {}))
    assert [r.current_version for r in rows] == [False, True]


def test_which_version_is_current_does_not_depend_on_which_runs_are_shown() -> None:
    """One agent's runs only reached 1.0.0, another's 2.0.0: narrowed to the first agent, 1.0.0
    must still read as older, as it does on every other view."""
    runs = [
        _run(1, mission_version="1.0.0"),
        _run(2, agent_id="a2", mission_version="2.0.0"),
    ]
    current = current_versions(runs, {})
    (row,) = score_rows([runs[0]], {}, current)
    assert row.current_version is False
    # The installed version wins over the newest run's.
    assert current_versions(runs, {"m1": "1.0.0"}) == {"m1": "1.0.0"}


def test_summaries_per_agent_on_each_mission_version() -> None:
    runs = [_run(1), _run(2, agent_id="a2"), _run(3, agent_id="a2")]
    grades = {k: _grade(k, 1, 0.6) for k in ("r1", "r2", "r3")}
    out = _summary(runs, grades, by="mission_agent")
    # The same median: the agent with more scored runs ranks first.
    assert [(s.agent_id, s.mission, s.mission_version, s.runs) for s in out] == [
        ("a2", "m1", "1.0.0", 2),
        ("a1", "m1", "1.0.0", 1),
    ]
    assert out[0].judge_models == ("judge-m",)


def test_the_judge_models_are_those_behind_the_median() -> None:
    """A run left out of the median cannot make its scores look like a mix of judges."""
    runs = [_run(1), _run(2), _run(3, "operator")]
    facts = {
        "r1": GradeFacts.of(_grade("r1", 1, 0.8), "judge-a"),
        "r2": GradeFacts.of(_grade("r2", 1, 0, "unavailable"), "judge-b"),
        "r3": GradeFacts.of(_grade("r3", 1, 0.8), "judge-c"),
    }
    (s,) = summarize(score_rows(runs, facts, {}), "all")
    assert s.judge_models == ("judge-a",)


def test_agents_come_back_ranked_and_named() -> None:
    """Best median first; an agent with no scored run last; ties to more scored runs, then the
    name. Every client shows this order, so none ranks on its own."""
    runs = [
        _run(1, agent_id="a1"),
        _run(2, agent_id="a2"),
        _run(3, agent_id="a2"),
        _run(4, agent_id="a3"),
        _run(5, agent_id="deadbeef-gone"),
        _run(6, agent_id="a4"),
    ]
    grades = {
        "r1": _grade("r1", 1, 0.6),
        "r2": _grade("r2", 1, 0.6),
        "r3": _grade("r3", 1, 0.6),
        "r4": _grade("r4", 1, 0, "unavailable"),
        "r5": _grade("r5", 1, 1),
        "r6": _grade("r6", 1, 0.6),
    }
    names = {"a1": "Zed", "a2": "yak", "a3": "alpha", "a4": "beta"}
    out = summarize(score_rows(runs, _facts(grades), {}), "agent", names)
    assert [s.agent_name for s in out] == ["deadbeef", "yak", "beta", "Zed", "alpha"]


def test_per_mission_agents_rank_within_each_version_in_turn() -> None:
    runs = [
        _run(1, agent_id="a1", mission_version="1.0.0"),
        _run(2, agent_id="a2", mission_version="1.0.0"),
        _run(3, agent_id="a1", mission_version="2.0.0"),
    ]
    grades = {
        "r1": _grade("r1", 0, 0),
        "r2": _grade("r2", 1, 1),
        "r3": _grade("r3", 1, 1),
    }
    out = summarize(score_rows(runs, _facts(grades), {}), "mission_agent")
    assert [(s.mission_version, s.agent_id) for s in out] == [
        ("1.0.0", "a2"),
        ("1.0.0", "a1"),
        ("2.0.0", "a1"),
    ]


# --- the routes --------------------------------------------------------------------------------


def _client() -> TestClient:
    from xorcise.core.roles.boot.role_all import build_rest_app

    return TestClient(build_rest_app())


def _graded(run_id: str, agent_id: str, trigger: str, grade: GradeResult | None) -> None:
    from xorcise.core import reporting, runs

    run = runs.create_run(agent_id=agent_id, mission="c1", run_id=run_id, run_control_key="K")
    runs.mark_terminal(run_id, trigger, run.created_at + timedelta(minutes=5))  # type: ignore[arg-type]
    if grade is not None:
        reporting.record_result(
            run_id=run_id,
            agent_id=agent_id,
            result=grade,
            partial=trigger in ("timeout", "operator"),
            partial_trigger=trigger if trigger in ("timeout", "operator") else None,
        )


def test_the_routes_serve_rows_and_summaries(migrated_home) -> None:
    from xorcise.core import agents

    install_mission(migrated_home, "c1")
    a = agents.register("scout", endpoint="http://s")
    b = agents.register("probe", endpoint="http://p")
    _graded("s1", a.id, "done", _grade("s1", 1, 0.8))
    _graded("s2", a.id, "done", _grade("s2", 1, 0, "unavailable"))
    _graded("s3", a.id, "deploy_failed", None)
    _graded("p1", b.id, "timeout", _grade("p1", 0.4, 0.2))
    c = _client()

    rows = c.get("/api/results/runs").json()
    assert [(r["run_id"], r["bucket"]) for r in rows] == [
        ("s1", "scored"),
        ("s2", "no_judge"),
        ("s3", "infra_failed"),
        ("p1", "scored"),
    ]
    assert all(r["mission"] == "c1" and r["current_version"] for r in rows)

    only_a = c.get("/api/results/runs", params={"agent": a.id}).json()
    assert {r["run_id"] for r in only_a} == {"s1", "s2", "s3"}

    ranked = c.get("/api/results/summary").json()
    assert [s["agent_name"] for s in ranked] == ["scout", "probe"]
    summary = {s["agent_id"]: s for s in ranked}
    assert summary[a.id]["median"] == pytest.approx(0.9)
    assert (summary[a.id]["scored"], summary[a.id]["no_judge"]) == (1, 1)
    assert summary[a.id]["infra_failed"] == 1
    # The timed-out run counts, at what it earned.
    assert summary[b.id]["median"] == pytest.approx(0.3)
    assert summary[b.id]["timed_out"] == 1

    (mission,) = c.get("/api/results/summary", params={"by": "mission"}).json()
    assert (mission["mission"], mission["runs"], mission["current_version"]) == ("c1", 4, True)


def test_the_summary_refuses_an_unknown_grouping(migrated_home) -> None:
    assert _client().get("/api/results/summary", params={"by": "nonsense"}).status_code == 422


def test_the_routes_count_intel_and_summarise_everything(migrated_home) -> None:
    from xorcise.core import agents
    from xorcise.core.runcontrol.store import SqliteSubmissionStore

    install_mission(migrated_home, "c1")
    a = agents.register("scout", endpoint="http://s")
    _graded("s1", a.id, "done", _grade("s1", 1, 0.8))
    _graded("s2", a.id, "operator", _grade("s2", 0, 0))
    store = SqliteSubmissionStore()
    store.record("s1", "intel", "i1", "look here")
    store.record("s1", "intel", "i2", "and here")
    c = _client()
    rows = {r["run_id"]: r for r in c.get("/api/results/runs").json()}
    assert (rows["s1"]["intel_disclosed"], rows["s2"]["intel_disclosed"]) == (2, 0)
    assert rows["s2"]["bucket"] == "stopped"
    (everything,) = c.get("/api/results/summary", params={"by": "all"}).json()
    assert (everything["runs"], everything["scored"], everything["stopped"]) == (2, 1, 1)


def test_the_routes_read_a_recorded_grade_without_the_judges_evidence(migrated_home) -> None:
    """Only the scores and the judge's status are read: a grade whose evidence no longer
    validates still counts, by its columns."""
    from sqlalchemy import update

    from xorcise.core import agents, reporting
    from xorcise.core.db import session_scope
    from xorcise.core.reporting.models import ResultRow

    install_mission(migrated_home, "c1")
    a = agents.register("scout", endpoint="http://s")
    _graded("s1", a.id, "done", _grade("s1", 1, 0.6))
    with session_scope() as s:
        s.execute(
            update(ResultRow).values(detail_json='{"judge_status": "ok", "breakdown": "junk"}')
        )
    assert reporting.recorded_grades()["s1"].overall == pytest.approx(0.8)
    (row,) = _client().get("/api/results/runs").json()
    assert (row["bucket"], row["overall"]) == ("scored", pytest.approx(0.8))


@pytest.mark.parametrize("detail", ["{not json", "[1, 2]"])
def test_a_grade_that_cannot_be_read_back_counts_as_failed_not_grading(
    migrated_home, detail
) -> None:
    """One unreadable stored grade must not take every view down: read as `grading`, it was
    re-driven, and the re-drive's full read of it raised (a 500 on every route)."""
    from sqlalchemy import update

    from xorcise.core import agents, reporting
    from xorcise.core.db import session_scope
    from xorcise.core.reporting.models import ResultRow

    install_mission(migrated_home, "c1")
    a = agents.register("scout", endpoint="http://s")
    _graded("s1", a.id, "done", _grade("s1", 1, 0.6))
    _graded("s2", a.id, "done", _grade("s2", 1, 0.4))
    with session_scope() as s:
        s.execute(update(ResultRow).where(ResultRow.run_id == "s1").values(detail_json=detail))
    assert reporting.has_result("s1")
    c = _client()

    resp = c.get("/api/results/runs")
    assert resp.status_code == 200
    rows = {r["run_id"]: r for r in resp.json()}
    assert (rows["s1"]["bucket"], rows["s1"]["overall"]) == ("grading_failed", pytest.approx(0.8))
    assert rows["s2"]["bucket"] == "scored"
    for by in ("agent", "all", "mission", "mission_agent"):
        resp = c.get("/api/results/summary", params={"by": by})
        assert resp.status_code == 200, by
        (summary,) = resp.json()
        assert (summary["scored"], summary["grading_failed"]) == (1, 1)
        assert summary["median"] == pytest.approx(0.7)


def test_a_grade_that_cannot_be_read_back_is_not_re_driven(migrated_home) -> None:
    from sqlalchemy import update

    from xorcise.core import agents
    from xorcise.core.db import session_scope
    from xorcise.core.reporting.models import ResultRow
    from xorcise.core.rest.run_terminate import ensure_graded_async

    a = agents.register("scout", endpoint="http://s")
    _graded("s1", a.id, "done", _grade("s1", 1, 0.6))
    with session_scope() as s:
        s.execute(update(ResultRow).values(detail_json="{not json"))
    scheduled: list[object] = []
    assert ensure_graded_async("s1", scheduled.append) is False
    assert scheduled == []


def test_a_corrupt_install_record_does_not_break_the_routes(migrated_home, monkeypatch) -> None:
    from xorcise.core import agents
    from xorcise.core.rest.routers import results

    install_mission(migrated_home, "c1")
    a = agents.register("scout", endpoint="http://s")
    _graded("s1", a.id, "done", _grade("s1", 1, 0.6))

    def broken(*_: Any) -> None:
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr(results, "get_installed", broken)
    resp = _client().get("/api/results/runs")
    assert resp.status_code == 200
    assert resp.json()[0]["current_version"] is True


def test_the_routes_re_drive_a_run_still_grading(migrated_home, monkeypatch) -> None:
    """A grade lost to a restart would otherwise leave the run `grading` on every view for good:
    reading the views heals it, as polling its `/result` does."""
    from xorcise.core import agents
    from xorcise.core.rest import run_terminate

    install_mission(migrated_home, "c1")
    a = agents.register("scout", endpoint="http://s")
    _graded("s1", a.id, "done", None)
    _graded("s2", a.id, "done", _grade("s2", 1, 0.6))
    driven: list[str] = []
    monkeypatch.setattr(
        run_terminate, "ensure_graded_async", lambda run_id, _schedule: driven.append(run_id)
    )
    c = _client()
    c.get("/api/results/runs")
    c.get("/api/results/summary")
    assert driven == ["s1", "s1"]

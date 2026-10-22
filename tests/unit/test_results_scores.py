"""Run performance: how each finished run counts, and the summaries the performance views draw.

The median is over runs that finished on the agent's own terms and were graded with a judge, a
partly-judged run counting at its lower bound. A run whose judge never ran, a run that timed out
or was stopped, an environment failure and a run still grading are each counted and disclosed,
never in the median. An older mission version is summarised apart from the current one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests._helpers import install_mission
from xorcise.core.contracts.grading import GradeResult, JudgeStatus, ScoreBreakdown
from xorcise.core.contracts.run import RunEntry
from xorcise.core.reporting.scores import bucket, score_rows, summarize

pytestmark = pytest.mark.unit

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _run(i: int, trigger: str | None = "done", **over: Any) -> RunEntry:
    fields: dict[str, Any] = {
        "run_id": f"r{i}",
        "agent_id": "a1",
        "mission": "m1",
        "state": "terminal" if trigger else "active",
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
        ("timeout", _grade("x", 1, 0.8), "ended_early"),
        ("operator", _grade("x", 1, 0.8), "ended_early"),
        ("deploy_failed", None, "infra_failed"),
        ("crashed", None, "infra_failed"),
        ("done", None, "grading"),
    ],
)
def test_each_finished_run_counts_one_way(trigger, grade, expected) -> None:
    assert bucket(_run(1, trigger), grade) == expected


def test_a_run_still_in_progress_is_not_a_row() -> None:
    assert score_rows([_run(1, None)], {}, {}) == []


# --- the median and its disclosures ------------------------------------------------------------


def _summary(runs: list[RunEntry], grades: dict[str, GradeResult], by: Any = "agent"):
    rows = score_rows(runs, {k: (g, "judge-m") for k, g in grades.items()}, {"m1": "1.0.0"})
    return summarize(rows, by)


def test_the_median_is_over_scored_runs_only() -> None:
    """Only the three judged runs that finished on their own terms are in it; a 0.5 from a run
    with no judge would have dragged it to 0.5."""
    runs = [
        _run(1),
        _run(2),
        _run(3),
        _run(4),  # no judge
        _run(5, "timeout"),
        _run(6, "deploy_failed"),
        _run(7),  # grading
    ]
    grades = {
        "r1": _grade("r1", 1, 0.8),
        "r2": _grade("r2", 1, 0.6),
        "r3": _grade("r3", 0.8, 0.6),
        "r4": _grade("r4", 1, 0, "unavailable"),
        "r5": _grade("r5", 0.2, 0.1),
    }
    (s,) = _summary(runs, grades)
    assert s.runs == 7
    assert s.scored == 3
    assert s.median == pytest.approx(0.8)
    assert (s.no_judge, s.ended_early, s.infra_failed, s.grading) == (1, 1, 1, 1)
    assert s.completed == 5
    assert s.best == pytest.approx(0.9)


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
    rows = score_rows(runs, {k: (g, None) for k, g in grades.items()}, {"m1": "1.1.0"})
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
    rows = score_rows(runs, {}, {})
    assert [r.current_version for r in rows] == [False, True]


def test_summaries_per_agent_on_each_mission_version() -> None:
    runs = [_run(1), _run(2, agent_id="a2"), _run(3, agent_id="a2")]
    grades = {k: _grade(k, 1, 0.6) for k in ("r1", "r2", "r3")}
    out = _summary(runs, grades, by="mission_agent")
    assert [(s.agent_id, s.mission, s.mission_version, s.runs) for s in out] == [
        ("a1", "m1", "1.0.0", 1),
        ("a2", "m1", "1.0.0", 2),
    ]
    assert out[0].judge_models == ("judge-m",)


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
        ("p1", "ended_early"),
    ]
    assert all(r["mission"] == "c1" and r["current_version"] for r in rows)

    only_a = c.get("/api/results/runs", params={"agent": a.id}).json()
    assert {r["run_id"] for r in only_a} == {"s1", "s2", "s3"}

    summary = {s["agent_id"]: s for s in c.get("/api/results/summary").json()}
    assert summary[a.id]["median"] == pytest.approx(0.9)
    assert (summary[a.id]["scored"], summary[a.id]["no_judge"]) == (1, 1)
    assert summary[a.id]["infra_failed"] == 1
    assert summary[b.id]["median"] is None
    assert summary[b.id]["ended_early"] == 1

    (mission,) = c.get("/api/results/summary", params={"by": "mission"}).json()
    assert (mission["mission"], mission["runs"], mission["current_version"]) == ("c1", 4, True)


def test_the_summary_refuses_an_unknown_grouping(migrated_home) -> None:
    assert _client().get("/api/results/summary", params={"by": "nonsense"}).status_code == 422

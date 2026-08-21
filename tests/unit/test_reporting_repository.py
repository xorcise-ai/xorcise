from __future__ import annotations

from xorcise.core import reporting
from xorcise.core.contracts.grading import GradeResult, ScoreBreakdown
from xorcise.core.contracts.reporting import ResultConditions, RunStats, TokenStats


def _result(run_id: str) -> GradeResult:
    return GradeResult(
        run_id=run_id,
        overall=0.5,
        breakdown=ScoreBreakdown(deterministic=0.5, judge=0.5),
        trace_ref=run_id,
    )


def test_record_then_history(migrated_home):
    reporting.record_result("r1", "a1", _result("r1"))
    reporting.record_result("r2", "a1", _result("r2"))
    hist = reporting.agent_history("a1")
    assert [h.run_id for h in hist] == ["r1", "r2"]
    assert hist[0].agent_id == "a1"
    assert hist[0].overall == 0.5
    assert hist[0].deterministic == 0.5 and hist[0].judge == 0.5


def test_history_is_scoped_per_agent(migrated_home):
    reporting.record_result("r1", "a1", _result("r1"))
    reporting.record_result("r2", "a2", _result("r2"))
    assert [h.run_id for h in reporting.agent_history("a1")] == ["r1"]


def test_delete_for_agent_removes_only_that_agent(migrated_home):
    reporting.record_result("r1", "a1", _result("r1"))
    reporting.record_result("r2", "a2", _result("r2"))
    assert reporting.delete_for_agent("a1") == 1
    assert reporting.agent_history("a1") == []
    assert [h.run_id for h in reporting.agent_history("a2")] == ["r2"]


def test_delete_result_removes_only_that_run(migrated_home):
    """An operator can delete a single run's result, leaving the agent's others intact."""
    reporting.record_result("r1", "a1", _result("r1"))
    reporting.record_result("r2", "a1", _result("r2"))
    assert reporting.delete_result("r1") is True
    assert [h.run_id for h in reporting.agent_history("a1")] == ["r2"]
    assert reporting.get_result("r1") is None


def test_delete_result_absent_is_false(migrated_home):
    """Deleting a result that was never recorded is a no-op that reports False."""
    assert reporting.delete_result("ghost") is False


def test_record_result_with_conditions_persists_conditions(migrated_home):
    """ResultConditions passed to record_result are persisted on the row."""
    conditions = ResultConditions(
        model="gpt-4o",
        judge_model="claude-sonnet",
        budget_seconds=120,
        sandbox_ref="xorcise/mission-x:1",
    )
    reporting.record_result("r1", "a1", _result("r1"), conditions)
    rc = reporting.result_conditions("r1")
    assert rc is not None
    assert rc.model == "gpt-4o"
    assert rc.judge_model == "claude-sonnet"
    assert rc.budget_seconds == 120
    assert rc.sandbox_ref == "xorcise/mission-x:1"


def test_record_result_without_conditions_returns_defaults(migrated_home):
    """Back-compat: calling record_result without conditions yields default ResultConditions."""
    reporting.record_result("r2", "a1", _result("r2"))
    rc = reporting.result_conditions("r2")
    assert rc is not None
    assert rc.model is None
    assert rc.judge_model is None
    assert rc.budget_seconds == 0
    assert rc.sandbox_ref is None


def test_agent_history_entry_carries_conditions(migrated_home):
    """AgentHistoryEntry.conditions reflects the recorded conditions."""
    conditions = ResultConditions(model="llama3", budget_seconds=60)
    reporting.record_result("r1", "a1", _result("r1"), conditions)
    hist = reporting.agent_history("a1")
    assert len(hist) == 1
    assert hist[0].conditions.model == "llama3"
    assert hist[0].conditions.budget_seconds == 60
    assert hist[0].conditions.judge_model is None


def test_result_conditions_returns_none_for_missing_run(migrated_home):
    """result_conditions returns None when the run has no recorded result."""
    assert reporting.result_conditions("no-such-run") is None


def test_record_result_with_versions_persists_versions(migrated_home):
    """agent_version + install_revision in ResultConditions are persisted and round-tripped."""
    conditions = ResultConditions(
        model="gpt-4o",
        agent_version=2,
        install_revision=3,
    )
    reporting.record_result("r1", "a1", _result("r1"), conditions)
    rc = reporting.result_conditions("r1")
    assert rc is not None
    assert rc.agent_version == 2
    assert rc.install_revision == 3


def test_agent_history_entry_carries_versions(migrated_home):
    """AgentHistoryEntry.conditions carries agent_version + install_revision."""
    conditions = ResultConditions(agent_version=5, install_revision=7)
    reporting.record_result("r1", "a1", _result("r1"), conditions)
    hist = reporting.agent_history("a1")
    assert len(hist) == 1
    assert hist[0].conditions.agent_version == 5
    assert hist[0].conditions.install_revision == 7


def test_record_result_without_versions_defaults_to_1(migrated_home):
    """Back-compat: no-conditions call yields agent_version=1 + install_revision=1."""
    reporting.record_result("r2", "a1", _result("r2"))
    rc = reporting.result_conditions("r2")
    assert rc is not None
    assert rc.agent_version == 1
    assert rc.install_revision == 1


def test_record_result_marks_partial(migrated_home):
    res = GradeResult(
        run_id="r1", overall=0.3, breakdown=ScoreBreakdown(deterministic=0.3, judge=0.3)
    )
    reporting.record_result("r1", "ag1", res, partial=True, partial_trigger="timeout")
    assert reporting.result_partial("r1") == (True, "timeout")
    assert reporting.agent_history("ag1")[0].partial is True
    assert reporting.agent_history("ag1")[0].partial_trigger == "timeout"


def test_record_result_clean_is_not_partial(migrated_home):
    res = GradeResult(
        run_id="r2", overall=0.9, breakdown=ScoreBreakdown(deterministic=0.9, judge=0.9)
    )
    reporting.record_result("r2", "ag1", res)  # no partial args → clean
    assert reporting.result_partial("r2") == (False, None)
    assert reporting.agent_history("ag1")[-1].partial is False


def test_record_and_read_stats(migrated_home):
    """A RunStats snapshot passed to record_result round-trips via get_stats."""
    stats = RunStats(tokens=TokenStats(input=100, output=20, total=120))
    reporting.record_result("r1", "a1", _result("r1"), stats=stats)
    got = reporting.get_stats("r1")
    assert got is not None
    assert got.tokens.total == 120
    assert got.tokens.input == 100


def test_stats_absent_returns_none(migrated_home):
    """A result recorded without stats has no snapshot."""
    reporting.record_result("r2", "a1", _result("r2"))
    assert reporting.get_stats("r2") is None


def test_get_stats_missing_run_is_none(migrated_home):
    assert reporting.get_stats("ghost") is None


def test_record_result_idempotent_keeps_first_stats(migrated_home):
    """First-write-wins: a second record must not overwrite the first snapshot."""
    reporting.record_result("r3", "a1", _result("r3"), stats=RunStats(tokens=TokenStats(total=1)))
    reporting.record_result("r3", "a1", _result("r3"), stats=RunStats(tokens=TokenStats(total=999)))
    got = reporting.get_stats("r3")
    assert got is not None and got.tokens.total == 1


def test_record_result_persists_and_reads_full_detail(migrated_home):
    from xorcise.core import reporting
    from xorcise.core.contracts.grading import CheckVerdict, GradeResult, ScoreBreakdown

    result = GradeResult(
        run_id="r1",
        overall=0.4,
        breakdown=ScoreBreakdown(deterministic=0.6, judge=0.2),
        key_evidence=("port 22 open",),
        major_deductions=("no flag submitted",),
        artifacts=("a1",),
        hard_fails=("rooted host",),
        check_breakdown=(
            CheckVerdict(id="c1", source="otel", ref="x", op="eq", passed=True, weight=1.0),
        ),
        trace_ref="r1",
    )
    reporting.record_result("r1", "agent-1", result)
    got = reporting.get_result("r1")
    assert got is not None
    assert got.overall == 0.4
    assert got.hard_fails == ("rooted host",)
    assert got.major_deductions == ("no flag submitted",)
    assert got.check_breakdown[0].passed is True

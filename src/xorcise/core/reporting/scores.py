"""Run performance: how each finished run counts, and the summaries the GUI's run strip, grader
agreement plot and run ledger draw from (domain module, pure).

The rules live here and nowhere else (contracts.reporting.ScoreBucket): the median is over runs
that finished on the agent's own terms and were graded with a judge, a partly-judged run counting
at its lower bound. A run whose judge never ran, one that timed out or was stopped, one cut short
by an environment failure and one still grading are each counted and disclosed, never averaged in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from statistics import median
from typing import Literal

from xorcise.core.contracts.grading import GradeResult
from xorcise.core.contracts.reporting import RunScore, ScoreBucket, ScoreSummary
from xorcise.core.contracts.run import RunEntry

GroupBy = Literal["agent", "mission", "mission_agent"]

# The judge never graded: it contributed 0.0 to `overall` without scoring anything.
_NO_JUDGE = frozenset({"unavailable", "model-not-configured"})


def version_label(run: RunEntry) -> str:
    """The version a run was on, as the GUI shows it (`v…`): the creator SemVer, else the local
    install revision."""
    return run.mission_version or str(run.install_revision)


def bucket(run: RunEntry, grade: GradeResult | None) -> ScoreBucket:
    """How one finished run counts (contracts.reporting.ScoreBucket)."""
    if run.outcome == "infra_failed":
        return "infra_failed"
    if grade is None:
        return "grading"
    if run.outcome == "partial":
        return "ended_early"
    if grade.judge_status in _NO_JUDGE:
        return "no_judge"
    return "scored"


def score_rows(
    runs: Iterable[RunEntry],
    grades: Mapping[str, tuple[GradeResult, str | None]],
    installed_versions: Mapping[str, str],
) -> list[RunScore]:
    """One RunScore per finished run, oldest first.

    `grades` maps a run id to its recorded grade and judge model. `installed_versions` maps an
    installed mission to its version label; a run on any other label is on an older version. For
    a mission no longer installed, the version of its most recent run stands in as current."""
    finished = sorted(
        (r for r in runs if r.outcome is not None), key=lambda r: (r.created_at, r.run_id)
    )
    current = dict(installed_versions)
    for run in finished:  # oldest first, so the newest run's label is the one that sticks
        if run.mission not in installed_versions:
            current[run.mission] = version_label(run)
    rows: list[RunScore] = []
    for run in finished:
        recorded = grades.get(run.run_id)
        grade, judge_model = recorded if recorded is not None else (None, None)
        assert run.outcome is not None
        label = version_label(run)
        rows.append(
            RunScore(
                run_id=run.run_id,
                agent_id=run.agent_id,
                mission=run.mission,
                mission_version=label,
                current_version=current.get(run.mission) == label,
                created_at=run.created_at,
                completed_at=run.completed_at,
                outcome=run.outcome,
                terminal_trigger=run.terminal_trigger,
                bucket=bucket(run, grade),
                deterministic=grade.breakdown.deterministic if grade else None,
                judge=grade.breakdown.judge if grade else None,
                judge_upper=grade.judge_upper if grade else None,
                judge_status=grade.judge_status if grade else None,
                overall=grade.overall if grade else None,
                overall_upper=grade.overall_upper if grade else None,
                judge_model=judge_model if grade else None,
            )
        )
    return rows


def _quartiles(values: list[float]) -> tuple[float | None, float | None]:
    """The middle half's edges, by linear interpolation; both None for no values."""
    if not values:
        return (None, None)
    s = sorted(values)

    def at(q: float) -> float:
        pos = (len(s) - 1) * q
        lo = int(pos)
        hi = min(lo + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (pos - lo)

    return (at(0.25), at(0.75))


def summarize(rows: Iterable[RunScore], by: GroupBy) -> list[ScoreSummary]:
    """Summarise rows per agent, per mission version, or per agent on each mission version.

    Groups come back in first-seen order of the rows (oldest run first); callers rank them."""
    groups: dict[tuple[str | None, ...], list[RunScore]] = {}
    for row in rows:
        key: tuple[str | None, ...]
        if by == "agent":
            key = (row.agent_id, None, None)
        elif by == "mission":
            key = (None, row.mission, row.mission_version)
        else:
            key = (row.agent_id, row.mission, row.mission_version)
        groups.setdefault(key, []).append(row)

    summaries: list[ScoreSummary] = []
    for (agent_id, mission, version), group in groups.items():
        scored = [r for r in group if r.bucket == "scored"]
        overalls = [r.overall for r in scored if r.overall is not None]
        q1, q3 = _quartiles(overalls)
        fully = [r for r in scored if r.judge_status == "ok"]
        gaps = [
            r.judge - r.deterministic
            for r in fully
            if r.judge is not None and r.deterministic is not None
        ]
        models = sorted({r.judge_model for r in group if r.judge_model})
        summaries.append(
            ScoreSummary(
                agent_id=agent_id,
                mission=mission,
                mission_version=version,
                current_version=group[-1].current_version if mission is not None else None,
                runs=len(group),
                completed=sum(1 for r in group if r.outcome == "completed"),
                scored=len(scored),
                partial_judge=sum(1 for r in scored if r.judge_status == "partial"),
                no_judge=sum(1 for r in group if r.bucket == "no_judge"),
                ended_early=sum(1 for r in group if r.bucket == "ended_early"),
                infra_failed=sum(1 for r in group if r.bucket == "infra_failed"),
                grading=sum(1 for r in group if r.bucket == "grading"),
                median=median(overalls) if overalls else None,
                q1=q1,
                q3=q3,
                best=max(overalls) if overalls else None,
                judge_gap=sum(gaps) / len(gaps) if gaps else None,
                judge_models=tuple(models),
                last_run=max(r.completed_at or r.created_at for r in group),
            )
        )
    return summaries
